from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from self_improving_code_review.application.config.settings import (
    AppConfig,
    DatabaseConfig,
    GovernanceConfig,
    LlmConfig,
    ScmConfig,
    load_config,
)
from self_improving_code_review.application.governance.run_lease import (
    ManagedRunLeaseSessionFactory,
)
from self_improving_code_review.application.ports.diff_provider import DiffSource
from self_improving_code_review.cli import review_commands
from self_improving_code_review.cli.governance_commands import _database_path
from self_improving_code_review.cli.review_commands import (
    _build_llm_client,
    _build_run_lease_sessions,
    _diff_source,
    _validate_project_context,
    _within_repository,
)
from self_improving_code_review.domain.common.enums import DiffSourceKind, ReviewMode
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.infrastructure.git import (
    RepositoryIdentity,
    RepositoryIdentityResolver,
)
from self_improving_code_review.infrastructure.llm import AiGatewayLlmClient, DatasetLlmClient
from self_improving_code_review.infrastructure.system import SystemClock, Uuid4IdGenerator
from self_improving_code_review.messages import Messages


@pytest.mark.case("UT-M1-002")
def test_workspace_source_is_selected_for_git_diff() -> None:
    source = _diff_source(_arguments(git_diff=True))

    assert source.kind is DiffSourceKind.WORKSPACE
    assert source.base_revision is None


@pytest.mark.case("UT-M1-002")
def test_range_source_carries_base_and_head() -> None:
    source = _diff_source(_arguments(range_revisions=["main", "feature"]))

    assert source.kind is DiffSourceKind.RANGE
    assert source.base_revision == "main"
    assert source.head_revision == "feature"


@pytest.mark.case("UT-M1-002")
def test_commit_source_carries_the_revision() -> None:
    source = _diff_source(_arguments(commit="abc1234"))

    assert source.kind is DiffSourceKind.COMMIT
    assert source.commit == "abc1234"


@pytest.mark.case("UT-M1-002")
def test_file_source_carries_the_patch_path() -> None:
    source = _diff_source(_arguments(diff_file="change.patch"))

    assert source.kind is DiffSourceKind.FILE
    assert source.patch_path == Path("change.patch")


@pytest.mark.case("UT-M1-002")
def test_missing_diff_source_is_rejected() -> None:
    with pytest.raises(InvalidInputError, match=Messages.DIFF_SOURCE_REQUIRED):
        _diff_source(_arguments())


def test_relative_configured_path_resolves_inside_the_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    resolved = _within_repository(repository, Path(".review/review.db"), "データベースのパス")

    assert resolved == repository / ".review/review.db"


def test_absolute_configured_path_inside_the_repository_is_accepted(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    absolute = repository / "data" / "review.db"

    assert _within_repository(repository, absolute, "データベースのパス") == absolute


@pytest.mark.parametrize(
    "configured",
    [Path("../outside.db"), Path("/tmp/outside.db")],
)
def test_configured_path_outside_the_repository_is_rejected(
    tmp_path: Path,
    configured: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    with pytest.raises(InvalidInputError, match="リポジトリ内を指定してください"):
        _within_repository(repository, configured, "データベースのパス")


def test_dataset_provider_requires_a_dataset_path() -> None:
    config = AppConfig(llm=LlmConfig(provider="dataset", dataset_path=None))

    with pytest.raises(InvalidInputError, match=Messages.DATASET_PATH_REQUIRED):
        _build_llm_client(config, Path.cwd())


def test_dataset_provider_builds_an_offline_client() -> None:
    config = load_config(Path("config/test.yaml"), environ={})

    client = _build_llm_client(config, Path.cwd())

    assert isinstance(client, DatasetLlmClient)
    assert client.model == "local-test-dataset-v1"


def test_dataset_path_outside_the_repository_is_rejected(tmp_path: Path) -> None:
    config = AppConfig(
        llm=LlmConfig(provider="dataset", dataset_path=Path("../outside/cases.json"))
    )

    with pytest.raises(InvalidInputError, match="リポジトリ内を指定してください"):
        _build_llm_client(config, tmp_path)


def test_gateway_provider_requires_a_resolved_model() -> None:
    config = AppConfig()

    with pytest.raises(InvalidInputError, match=Messages.MODEL_REQUIRED):
        _build_llm_client(config, Path.cwd())


def test_gateway_provider_requires_external_source_permission() -> None:
    config = AppConfig(llm=LlmConfig(model="claude-fixed-v1"))

    with pytest.raises(InvalidInputError, match="allow_external_source"):
        _build_llm_client(config, Path.cwd())


def test_gateway_provider_requires_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REVIEW_LLM_API_KEY", raising=False)
    config = AppConfig.model_validate(
        {
            "llm": {"model": "claude-fixed-v1"},
            "security": {"allow_external_source": True},
        }
    )

    with pytest.raises(InvalidInputError, match=Messages.API_KEY_REQUIRED):
        _build_llm_client(config, Path.cwd())


def test_gateway_client_is_built_when_all_requirements_are_met(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REVIEW_LLM_API_KEY", "test-only")
    config = AppConfig.model_validate(
        {
            "llm": {"model": "claude-fixed-v1"},
            "security": {"allow_external_source": True},
        }
    )

    client = _build_llm_client(config, Path.cwd())

    assert isinstance(client, AiGatewayLlmClient)
    assert client.model == "claude-fixed-v1"
    client.close()


def test_llm_initialization_failure_closes_telemetry_and_preserves_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 初期化失敗時も遠隔測定を閉じ、元の入力例外を維持する."""

    class RecordingTelemetry:
        """終了処理の呼出有無だけを記録する試験用遠隔測定."""

        def __init__(self) -> None:
            """未終了の状態で初期化する."""
            self.is_closed = False

        def close(self) -> None:
            """終了処理が呼び出されたことを記録する."""
            self.is_closed = True

    repository = tmp_path / "repository"
    repository.mkdir()
    telemetry = RecordingTelemetry()
    expected_error = InvalidInputError(Messages.DATASET_PATH_REQUIRED)
    config = AppConfig(llm=LlmConfig(provider="dataset", dataset_path=None))
    monkeypatch.setattr(review_commands, "load_prompt_template", lambda *_args: object())
    monkeypatch.setattr(review_commands, "_build_telemetry", lambda _config: telemetry)

    def fail_to_build_llm(_config: AppConfig, _repository: Path) -> None:
        """確認対象の LLM 初期化エラーを送出する."""
        raise expected_error

    monkeypatch.setattr(review_commands, "_build_llm_client", fail_to_build_llm)

    with pytest.raises(InvalidInputError) as captured:
        review_commands.run_configured_review(
            repository,
            config,
            DiffSource(kind=DiffSourceKind.WORKSPACE),
            ReviewMode.RULES,
        )

    assert captured.value is expected_error
    assert telemetry.is_closed is True


def test_run_lease_factory_is_created_only_when_enabled(tmp_path: Path) -> None:
    clock = SystemClock()
    generator = Uuid4IdGenerator()
    assert (
        _build_run_lease_sessions(AppConfig(), tmp_path / "disabled.db", clock, generator) is None
    )
    config = AppConfig(governance=GovernanceConfig(enabled=True, execution_leases_enabled=True))

    factory = _build_run_lease_sessions(config, tmp_path / "enabled.db", clock, generator)

    assert isinstance(factory, ManagedRunLeaseSessionFactory)


def _arguments(
    *,
    git_diff: bool = False,
    range_revisions: list[str] | None = None,
    commit: str | None = None,
    diff_file: str | None = None,
) -> Namespace:
    return Namespace(
        git_diff=git_diff,
        range_revisions=range_revisions,
        commit=commit,
        diff_file=diff_file,
    )


def test_governance_database_path_outside_repository_is_rejected(tmp_path: Path) -> None:
    """統制 DB もリポジトリ外へ保存できないことを確認する."""
    repository = tmp_path / "repository"
    repository.mkdir()
    config = AppConfig(database=DatabaseConfig(path=Path("../outside.db")))

    with pytest.raises(InvalidInputError, match="リポジトリ内を指定してください"):
        _database_path(repository, config)


@pytest.mark.parametrize("configured_identity", [True, False])
def test_enabled_governance_validates_project_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured_identity: bool,
) -> None:
    """M5 有効時に設定値または解決値でプロジェクト所属を検証する."""
    repository = tmp_path / "repository"
    repository.mkdir()
    config = AppConfig(
        project_id="project-a",
        database=DatabaseConfig(path=Path(".review/review.db")),
        scm=ScmConfig(repository_external_id="owner/repository" if configured_identity else ""),
        governance=GovernanceConfig(enabled=True),
    )
    calls: list[tuple[str, str]] = []

    def record_validation(
        _validator: object,
        _project_id: object,
        *,
        provider: str,
        repository_external_id: str,
    ) -> None:
        """検証に渡されたリポジトリ識別子を記録する."""
        calls.append((provider, repository_external_id))

    monkeypatch.setattr(
        "self_improving_code_review.cli.review_commands.ProjectContextValidator.validate",
        record_validation,
    )
    if not configured_identity:
        monkeypatch.setattr(
            RepositoryIdentityResolver,
            "resolve",
            lambda _resolver, _repository: RepositoryIdentity("local", "local:fixed"),
        )

    _validate_project_context(repository, config, repository / ".review/review.db")

    assert calls == [
        ("github", "owner/repository") if configured_identity else ("local", "local:fixed")
    ]
