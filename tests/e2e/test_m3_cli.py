"""M3 パイプラインを CLI 入力から保存結果まで検証する一連処理テスト."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from self_improving_code_review.cli.main import main

_USAGE = {"input_tokens": 2, "output_tokens": 1, "latency_ms": 1, "cost_usd": "0"}


@pytest.mark.case("E2E-M3-001")
def test_m3_tool_and_reflection_run_offline_end_to_end(tmp_path: Path) -> None:
    """限定ツールと自己検証をローカル固定応答だけで完了する."""
    repository = _repository(tmp_path, ("src/app.py",), helper_names=("helper.py",))
    patch = _change_files(repository, ("src/app.py",))
    dataset = repository / "datasets/m3.json"
    finding = _finding("src/app.py")
    _write_dataset(
        dataset,
        [
            _case(
                "reflection",
                ("自己検証担当",),
                _reflection("B001-F001", "supported", "EVIDENCE_SUFFICIENT"),
            ),
            _case("final", ("context-tool-result",), _agent_final(finding)),
            _case("tool", ("src/app.py",), _tool_request("src/helper.py")),
        ],
    )
    config = _config(repository, context=True, reflection=True)
    output = repository / "result.json"

    code = _run(repository, config, patch, output)

    assert code == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["run"]["status"] == "success"
    assert document["run"]["versions"]["agent_prompt_version"] == "review_agent_v1@4"
    assert document["run"]["versions"]["reflection_prompt_version"] == "reflection_v1@2"
    assert document["findings"][0]["validation"]["reflection"] == "supported"
    assert document["pipeline_metrics"]["tool_call_count"] == 1
    assert document["pipeline_metrics"]["reflection_call_count"] == 1
    with closing(sqlite3.connect(repository / ".review/m3.db")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM reflection_results").fetchone()[0] == 1


@pytest.mark.case("E2E-M3-002", "IT-M3-006")
def test_m3_partial_run_resumes_only_failed_bundle_end_to_end(tmp_path: Path) -> None:
    """一部失敗を同じ実行 ID で再開し、成功単位を再利用する."""
    repository = _repository(tmp_path, ("src/a.py", "src/b.py"))
    patch = _change_files(repository, ("src/a.py", "src/b.py"))
    dataset = repository / "datasets/m3.json"
    _write_dataset(
        dataset,
        [
            _case("b-invalid", ("src/b.py",), {"schema_version": 1, "findings": [], "x": 1}),
            _case("a", ("src/a.py",), {"schema_version": 1, "findings": []}),
        ],
    )
    config = _config(repository, max_workers=2)
    first_output = repository / "first.json"

    assert _run(repository, config, patch, first_output) == 2
    first = json.loads(first_output.read_text(encoding="utf-8"))
    assert first["run"]["status"] == "partial-success"

    _write_dataset(
        dataset,
        [
            _case("b", ("src/b.py",), {"schema_version": 1, "findings": []}),
            _case("a", ("src/a.py",), {"schema_version": 1, "findings": []}),
        ],
    )
    resumed_output = repository / "resumed.json"

    assert _run(repository, config, patch, resumed_output) == 0
    resumed = json.loads(resumed_output.read_text(encoding="utf-8"))
    assert resumed["run"]["run_id"] == first["run"]["run_id"]
    assert resumed["run"]["status"] == "success"
    assert resumed["pipeline_metrics"]["resumed_bundle_count"] == 1
    assert resumed["pipeline_metrics"]["reexecuted_bundle_count"] == 1


@pytest.mark.case("E2E-M3-003", "IT-M3-007")
def test_m3_changed_context_source_starts_new_run_end_to_end(tmp_path: Path) -> None:
    """再開前に追加ソースが変化した場合は過去単位を再利用しない."""
    repository = _repository(
        tmp_path,
        ("src/a.py", "src/b.py"),
        helper_names=("helper_a.py", "helper_b.py"),
    )
    patch = _change_files(repository, ("src/a.py", "src/b.py"))
    dataset = repository / "datasets/m3.json"
    _write_agent_dataset(dataset, is_b_valid=False)
    config = _config(repository, context=True, max_workers=1)
    first_output = repository / "first-context.json"

    assert _run(repository, config, patch, first_output) == 2
    first = json.loads(first_output.read_text(encoding="utf-8"))
    assert first["run"]["status"] == "partial-success"

    (repository / "src/helper_a.py").write_text("context_a = 2\n", encoding="utf-8")
    _write_agent_dataset(dataset, is_b_valid=True)
    second_output = repository / "changed-context.json"

    assert _run(repository, config, patch, second_output) == 0
    second = json.loads(second_output.read_text(encoding="utf-8"))
    assert second["run"]["run_id"] != first["run"]["run_id"]
    assert second["pipeline_metrics"]["resumed_bundle_count"] == 0
    assert second["pipeline_metrics"]["reexecuted_bundle_count"] == 0


@pytest.mark.case("CT-M3-003", "E2E-M3-004")
def test_m3_secret_in_tool_result_blocks_without_exposure(tmp_path: Path) -> None:
    """ツール取得本文の模擬秘密を遮断し、結果 JSON へ値を出力しない."""
    repository = _repository(tmp_path, ("src/app.py",), helper_names=("secret.py",))
    patch = _change_files(repository, ("src/app.py",))
    secret = "AKIAIOSFODNN7EXAMPLE"
    (repository / "src/secret.py").write_text(f'key = "{secret}"\n', encoding="utf-8")
    dataset = repository / "datasets/m3.json"
    _write_dataset(dataset, [_case("tool", ("src/app.py",), _tool_request("src/secret.py"))])
    config = _config(repository, context=True)
    output = repository / "blocked.json"

    assert _run(repository, config, patch, output) == 4
    rendered = output.read_text(encoding="utf-8")
    document = json.loads(rendered)
    assert document["run"]["status"] == "blocked"
    assert document["pipeline_metrics"]["tool_reason_counts"] == {"SECRET_DETECTED": 1}
    assert secret not in rendered
    with closing(sqlite3.connect(repository / ".review/m3.db")) as connection:
        assert connection.execute("SELECT status FROM tool_calls").fetchone()[0] == "blocked"


def _repository(
    tmp_path: Path,
    source_names: tuple[str, ...],
    *,
    helper_names: tuple[str, ...] = (),
) -> Path:
    """M3 用プロンプトと固定 Git 履歴を持つ一時リポジトリを作成する."""
    repository = tmp_path / "repository"
    (repository / "src").mkdir(parents=True)
    for name in source_names:
        (repository / name).write_text("value = 1\n", encoding="utf-8")
    for index, name in enumerate(helper_names, start=1):
        (repository / "src" / name).write_text(f"context_{index} = 1\n", encoding="utf-8")
    prompt_target = repository / "prompts"
    prompt_target.mkdir()
    for name in ("review_general_v1.yaml", "review_agent_v1.yaml", "reflection_v1.yaml"):
        (prompt_target / name).write_text(
            (Path("prompts") / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=repository,
        check=True,
    )
    return repository


def _change_files(repository: Path, source_names: tuple[str, ...]) -> Path:
    """作業ツリーへ反映済みの差分と同じパッチファイルを作成する."""
    patch_lines: list[str] = []
    for name in source_names:
        marker = Path(name).stem
        (repository / name).write_text(
            f"value = 1\nunsafe_{marker} = user.name\n", encoding="utf-8"
        )
        patch_lines.extend(
            (
                f"diff --git a/{name} b/{name}\n",
                f"--- a/{name}\n",
                f"+++ b/{name}\n",
                "@@ -1 +1,2 @@\n",
                " value = 1\n",
                f"+unsafe_{marker} = user.name\n",
            )
        )
    patch = repository / "change.patch"
    patch.write_text("".join(patch_lines), encoding="utf-8")
    return patch


def _config(
    repository: Path,
    *,
    context: bool = False,
    reflection: bool = False,
    max_workers: int = 1,
) -> Path:
    """外部通信を禁止した M3 設定を保存する."""
    config = repository / "m3.yaml"
    config.write_text(
        "schema_version: 1\n"
        "project_id: m3-e2e\n"
        "database:\n  path: .review/m3.db\n"
        "llm:\n  provider: dataset\n  dataset_path: datasets/m3.json\n"
        "review:\n"
        f"  context_tools_enabled: {str(context).lower()}\n"
        f"  reflection_enabled: {str(reflection).lower()}\n"
        f"  max_workers: {max_workers}\n"
        "security:\n  allow_external_source: false\n",
        encoding="utf-8",
    )
    return config


def _run(repository: Path, config: Path, patch: Path, output: Path) -> int:
    """M3 レビューを CLI 境界から実行する."""
    return main(
        [
            "review",
            "--repository",
            str(repository),
            "--config",
            str(config),
            "--diff-file",
            str(patch),
            "--output",
            str(output),
        ]
    )


def _write_agent_dataset(path: Path, *, is_b_valid: bool) -> None:
    """二つのレビュー単位用ツールターン固定応答を保存する."""
    b_final = _agent_final(None) if is_b_valid else {"schema_version": 1, "unknown": True}
    _write_dataset(
        path,
        [
            _case("a-final", ("src/a.py", "context-tool-result"), _agent_final(None)),
            _case("b-final", ("src/b.py", "context-tool-result"), b_final),
            _case("a-tool", ("src/a.py",), _tool_request("src/helper_a.py")),
            _case("b-tool", ("src/b.py",), _tool_request("src/helper_b.py")),
        ],
    )


def _write_dataset(path: Path, cases: list[dict[str, object]]) -> None:
    """ケース順で応答を選ぶネットワーク非接続データセットを保存する."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model": "local-m3-e2e-v1",
                "default_result": {
                    "payload": {"schema_version": 1, "findings": []},
                    "usage": _USAGE,
                },
                "cases": cases,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _case(case_id: str, markers: tuple[str, ...], payload: dict[str, object]) -> dict[str, object]:
    """固定応答ケースを作成する."""
    return {
        "case_id": case_id,
        "match_all": list(markers),
        "result": {"payload": payload, "usage": _USAGE},
    }


def _tool_request(path: str) -> dict[str, object]:
    """read_file を一回要求するエージェントターンを返す."""
    return {
        "schema_version": 1,
        "action": "tool-request",
        "tool_request": {
            "tool_name": "read_file",
            "path": path,
            "path_scope": [],
            "query": None,
            "start_line": 1,
            "end_line": 20,
        },
        "findings": [],
    }


def _agent_final(finding: dict[str, object] | None) -> dict[str, object]:
    """最終指摘を持つエージェントターンを返す."""
    return {
        "schema_version": 1,
        "action": "final",
        "tool_request": None,
        "findings": [finding] if finding is not None else [],
    }


def _reflection(finding_id: str, status: str, reason_code: str) -> dict[str, object]:
    """一件分の自己検証固定応答を返す."""
    return {
        "schema_version": 1,
        "results": [{"finding_id": finding_id, "status": status, "reason_code": reason_code}],
    }


def _finding(path: str) -> dict[str, object]:
    """追加行と整合する一件の指摘候補を返す."""
    marker = Path(path).stem
    return {
        "file": path,
        "start_line": 2,
        "end_line": 2,
        "category": "correctness",
        "issue_type": "null-dereference",
        "rule_id": "GENERAL.NULL_DEREFERENCE",
        "primary_entity": "user",
        "severity": "high",
        "title": "None の参照",
        "description": "None の可能性がある値を参照します。",
        "evidence": {"quote": f"unsafe_{marker} = user.name", "reason": "追加行です。"},
        "suggestion": "参照前に検証してください。",
        "needs_context": False,
    }
