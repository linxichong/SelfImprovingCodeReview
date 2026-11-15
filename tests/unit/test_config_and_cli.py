from __future__ import annotations

import importlib
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest

from self_improving_code_review.application.config.settings import load_config, read_api_key
from self_improving_code_review.cli.review_commands import _build_llm_client
from self_improving_code_review.domain.common.errors import (
    InfrastructureError,
    InvalidDiffError,
    InvalidInputError,
    InvalidLlmOutputError,
    LlmClientError,
    PolicyViolationError,
)
from self_improving_code_review.infrastructure.llm import DatasetLlmClient


@pytest.mark.case("UT-M1-001")
def test_config_precedence_and_secret_environment(tmp_path: Path) -> None:
    config_file = tmp_path / "review.yaml"
    config_file.write_text(
        """schema_version: 1
project_id: from-file
llm:
  model: claude-file-v1
review:
  mode: baseline
""",
        encoding="utf-8",
    )
    environment = {
        "REVIEW_PROJECT_ID": "from-env",
        "REVIEW_LLM_MODEL": "claude-env-v2",
        "REVIEW_REVIEW_MODE": "rules",
        "REVIEW_LLM_API_KEY": "credential",
    }

    config = load_config(config_file, environ=environment)

    assert config.project_id == "from-env"
    assert config.llm.model == "claude-env-v2"
    assert config.review.mode == "rules"
    assert config.llm.is_resolved
    assert read_api_key(environment) == "credential"
    assert "credential" not in config.model_dump_json()


@pytest.mark.case("UT-M1-001")
@pytest.mark.parametrize(
    "content",
    [
        "- not-a-mapping\n",
        "schema_version: 2\n",
        "unknown: true\n",
        "llm:\n  api_key: forbidden\n",
        "llm: [unterminated\n",
    ],
)
def test_invalid_configurations_are_rejected(tmp_path: Path, content: str) -> None:
    config_file = tmp_path / "invalid.yaml"
    config_file.write_text(content, encoding="utf-8")

    with pytest.raises(InvalidInputError):
        load_config(config_file, environ={})


def test_dataset_provider_is_resolved_without_model_or_api_key(tmp_path: Path) -> None:
    config_file = tmp_path / "development.yaml"
    config_file.write_text(
        """schema_version: 1
llm:
  provider: dataset
  dataset_path: datasets/development/review_cases.json
security:
  allow_external_source: false
""",
        encoding="utf-8",
    )

    config = load_config(config_file, environ={})

    assert config.llm.provider == "dataset"
    assert config.llm.dataset_path == Path("datasets/development/review_cases.json")
    assert config.llm.is_resolved
    assert read_api_key({}) is None


def test_development_config_builds_offline_dataset_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REVIEW_LLM_API_KEY", raising=False)
    repository = Path.cwd()
    config = load_config(repository / "config/development.yaml", environ={})

    client = _build_llm_client(config, repository)

    assert isinstance(client, DatasetLlmClient)
    assert client.model == "local-development-dataset-v1"


def test_dataset_provider_can_be_selected_with_environment() -> None:
    config = load_config(
        None,
        environ={
            "REVIEW_LLM_PROVIDER": "dataset",
            "REVIEW_LLM_DATASET_PATH": "datasets/test/review_cases.json",
        },
    )

    assert config.llm.provider == "dataset"
    assert config.llm.dataset_path == Path("datasets/test/review_cases.json")
    assert config.llm.is_resolved


@pytest.mark.case("UT-M1-001")
def test_unresolved_and_latest_models_are_rejected_by_resolution_check() -> None:
    assert not load_config(None, environ={}).llm.is_resolved
    assert not load_config(None, environ={"REVIEW_LLM_MODEL": "latest"}).llm.is_resolved
    assert read_api_key({}) is None


def test_rules_check_dispatch_and_help(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli: Any = importlib.import_module("self_improving_code_review.cli.main")
    called: list[str] = []
    monkeypatch.setattr(
        cli,
        "execute_rules_check_command",
        lambda arguments: called.append(arguments.file) or 0,
    )

    assert cli.main(["rules", "check", "--git-diff", "--file", "src/app.py"]) == 0
    assert called == ["src/app.py"]

    with pytest.raises(SystemExit) as captured:
        cli.build_parser().parse_args(["rules", "check", "--help"])

    assert captured.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "--file PATH" in help_text
    assert "跳过 LLM 调用" in help_text


def test_scm_sync_feedback_arguments_are_documented_and_parsed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli: Any = importlib.import_module("self_improving_code_review.cli.main")

    arguments = cli.build_parser().parse_args(
        [
            "scm",
            "sync-feedback",
            "--repository",
            ".",
            "--config",
            "review.yaml",
            "--change",
            "42",
            "--output",
            ".review/sync.json",
        ]
    )

    assert arguments.scm_command == "sync-feedback"
    assert arguments.change == "42"
    with pytest.raises(SystemExit) as captured:
        cli.build_parser().parse_args(["scm", "sync-feedback", "--help"])
    assert captured.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "--change NUMBER" in help_text
    assert "--output JSON" in help_text


@pytest.mark.case("IT-M1-015", "IT-M2-017", "UT-M1-002", "UT-M2-021")
@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (InvalidInputError("bad input"), 1),
        (InvalidDiffError("bad diff"), 1),
        (LlmClientError("gateway"), 2),
        (InvalidLlmOutputError("shape"), 3),
        (PolicyViolationError("blocked"), 4),
        (InfrastructureError("database"), 5),
    ],
)
def test_cli_maps_errors_to_stable_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_code: int,
) -> None:
    cli: Any = importlib.import_module("self_improving_code_review.cli.main")
    recorded: list[tuple[str, str]] = []

    def fail(_arguments: object) -> int:
        raise error

    monkeypatch.setattr(cli, "execute_review_command", fail)
    monkeypatch.setattr(cli, "write_error", lambda code, message: recorded.append((code, message)))

    code = cli.main(["review", "--git-diff"])

    assert code == expected_code
    assert recorded == [(type(error).__name__, str(error))]


@pytest.mark.case("IT-M1-015", "UT-M1-002")
def test_cli_success_and_argument_error(monkeypatch: pytest.MonkeyPatch) -> None:
    cli: Any = importlib.import_module("self_improving_code_review.cli.main")
    monkeypatch.setattr(cli, "execute_review_command", lambda _arguments: 0)

    assert cli.main(["review", "--git-diff", "--mode", "rules"]) == 0
    assert cli.main(["review"]) == 1


@pytest.mark.case("UT-M2-021")
@pytest.mark.parametrize(
    ("command", "handler"),
    [
        ("feedback", "execute_feedback_command"),
        ("memory", "execute_memory_command"),
        ("eval", "execute_evaluation_command"),
    ],
)
def test_m2_cli_commands_are_dispatched(
    command: str, handler: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli: Any = importlib.import_module("self_improving_code_review.cli.main")
    parser = type(
        "Parser",
        (),
        {"parse_args": lambda _self, _argv: Namespace(command=command)},
    )()
    monkeypatch.setattr(cli, "build_parser", lambda: parser)
    monkeypatch.setattr(cli, handler, lambda _arguments: 7)

    assert cli.main([]) == 7


@pytest.mark.case("UT-M1-002")
def test_review_help_explains_all_parameters(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 利用者向けヘルプも外部インターフェースの契約として固定する。
    cli: Any = importlib.import_module("self_improving_code_review.cli.main")

    with pytest.raises(SystemExit) as captured:
        cli.build_parser().parse_args(["review", "--help"])

    assert captured.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    for option in (
        "--repository PATH",
        "--project PROJECT_ID",
        "--config YAML",
        "--mode MODE",
        "--output JSON",
        "--git-diff",
        "--range BASE HEAD",
        "--commit SHA",
        "--diff-file PATCH",
    ):
        assert option in help_text
    assert "未暂存差异" in help_text
    assert "三点比较差异" in help_text
    assert "memory 使用规则和已批准的记忆" in help_text
    assert "文件最大为 20 MiB" in help_text
    assert "选择一项" in help_text
    assert "显示此帮助并退出" in help_text
