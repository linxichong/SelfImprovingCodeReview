from __future__ import annotations

from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from self_improving_code_review.application.config.settings import AppConfig, ScmConfig
from self_improving_code_review.application.ports.diff_provider import DiffSource
from self_improving_code_review.application.scm.models import (
    PublicationPlan,
    PublicationResult,
    ScmReviewOutcome,
)
from self_improving_code_review.cli import scm_commands
from self_improving_code_review.domain.common.enums import (
    DiffSourceKind,
    ReviewMode,
    RunStatus,
)
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import NormalizedPath
from self_improving_code_review.domain.scm import (
    ChangeRef,
    CommentPosition,
    CreateComment,
    PublicationKind,
    PublicationOperation,
    PublicationStatus,
    ScmChange,
    ScmChangeState,
    ScmProvider,
)


@pytest.mark.case("CT-M4-004")
def test_configured_executor_delegates_all_arguments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    expected = object()
    captured: list[tuple[object, ...]] = []

    def fake_run(*args: object, **kwargs: object) -> object:
        captured.append((*args, kwargs))
        return expected

    monkeypatch.setattr(scm_commands, "run_configured_review", fake_run)
    source = DiffSource(DiffSourceKind.RANGE, "1" * 40, "2" * 40)
    config = AppConfig()

    result = scm_commands._ConfiguredReviewExecutor().execute(
        tmp_path,
        config,
        source,
        ReviewMode.RULES,
        config_path=tmp_path / "review.yaml",
        execution_nonce="nonce",
    )

    assert result is expected
    assert captured[0][0:4] == (tmp_path, config, source, ReviewMode.RULES)
    assert captured[0][4] == {
        "config_path": tmp_path / "review.yaml",
        "execution_nonce": "nonce",
    }


def test_scm_command_requires_environment_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(scm_commands, "load_config", lambda _path: AppConfig())
    monkeypatch.setattr(scm_commands, "read_scm_token", lambda: None)

    with pytest.raises(InvalidInputError):
        scm_commands.execute_scm_command(_arguments(tmp_path, RunStatus.SUCCESS))


@pytest.mark.parametrize(
    ("status", "publish", "expected"),
    [
        (RunStatus.SUCCESS, False, 0),
        (RunStatus.FAILED, True, 2),
        (RunStatus.PARTIAL_SUCCESS, True, 2),
        (RunStatus.BLOCKED, True, 4),
    ],
)
def test_scm_review_command_maps_status_and_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: RunStatus,
    publish: bool,
    expected: int,
) -> None:
    config = AppConfig(
        scm=ScmConfig(
            enabled=True,
            repository_external_id="owner/repository",
            bot_actor_external_id="review-bot",
            publish_enabled=True,
        )
    )
    outcome = SimpleNamespace(
        review=SimpleNamespace(run=SimpleNamespace(status=status)),
        publications=(),
    )
    dry_runs: list[bool] = []

    class Coordinator:
        """固定結果を返す CLI 構成代替."""

        def __init__(self, **kwargs: object) -> None:
            """構成引数を受け入れる."""
            del kwargs

        def execute(self, *args: object, **kwargs: object) -> object:
            """固定結果を返す."""
            del args, kwargs
            return outcome

    monkeypatch.setattr(scm_commands, "load_config", lambda _path: config)
    monkeypatch.setattr(scm_commands, "read_scm_token", lambda: "token")
    monkeypatch.setattr(scm_commands, "GitHubScmClient", lambda *_args: object())
    monkeypatch.setattr(scm_commands, "initialize_database", lambda _path: None)
    monkeypatch.setattr(
        scm_commands, "SqliteScmPublicationPublisher", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(scm_commands, "ScmReviewCoordinator", Coordinator)
    monkeypatch.setattr(
        scm_commands,
        "_outcome_document",
        lambda _value, *, is_dry_run: dry_runs.append(is_dry_run) or {"ok": True},
    )
    monkeypatch.setattr(scm_commands, "write_document", lambda *_args: None)
    arguments = _arguments(tmp_path, status)
    arguments.publish = publish

    assert scm_commands.execute_scm_command(arguments) == expected
    assert dry_runs == [not publish]


@pytest.mark.parametrize(
    "publication_status",
    [PublicationStatus.FAILED, PublicationStatus.STALE, PublicationStatus.BLOCKED],
)
def test_scm_review_command_returns_partial_failure_for_unsuccessful_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    publication_status: PublicationStatus,
) -> None:
    config = AppConfig(
        scm=ScmConfig(
            enabled=True,
            repository_external_id="owner/repository",
            bot_actor_external_id="review-bot",
            publish_enabled=True,
        )
    )
    outcome = SimpleNamespace(
        review=SimpleNamespace(run=SimpleNamespace(status=RunStatus.SUCCESS)),
        publications=(
            PublicationResult(
                fingerprint_hash="fingerprint",
                status=publication_status,
                operation=PublicationOperation.FAILED,
                safe_error_code="TEST_FAILURE",
            ),
        ),
    )

    class Coordinator:
        """出版失敗を含む固定結果を返す."""

        def __init__(self, **kwargs: object) -> None:
            """構成引数を受け入れる."""
            del kwargs

        def execute(self, *args: object, **kwargs: object) -> object:
            """固定結果を返す."""
            del args, kwargs
            return outcome

    monkeypatch.setattr(scm_commands, "load_config", lambda _path: config)
    monkeypatch.setattr(scm_commands, "read_scm_token", lambda: "token")
    monkeypatch.setattr(scm_commands, "GitHubScmClient", lambda *_args: object())
    monkeypatch.setattr(scm_commands, "initialize_database", lambda _path: None)
    monkeypatch.setattr(
        scm_commands, "SqliteScmPublicationPublisher", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(scm_commands, "ScmReviewCoordinator", Coordinator)
    monkeypatch.setattr(scm_commands, "_outcome_document", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(scm_commands, "write_document", lambda *_args: None)
    arguments = _arguments(tmp_path, RunStatus.SUCCESS)
    arguments.publish = True

    assert scm_commands.execute_scm_command(arguments) == 2


def test_scm_review_command_returns_success_when_all_publications_succeed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = AppConfig(
        scm=ScmConfig(
            enabled=True,
            repository_external_id="owner/repository",
            bot_actor_external_id="review-bot",
            publish_enabled=True,
        )
    )
    outcome = SimpleNamespace(
        review=SimpleNamespace(run=SimpleNamespace(status=RunStatus.SUCCESS)),
        publications=(
            PublicationResult(
                fingerprint_hash="fingerprint",
                status=PublicationStatus.PUBLISHED,
                operation=PublicationOperation.CREATED,
                external_comment_id="10",
            ),
        ),
    )

    class Coordinator:
        """出版成功を含む固定結果を返す."""

        def __init__(self, **kwargs: object) -> None:
            """構成引数を受け入れる."""
            del kwargs

        def execute(self, *args: object, **kwargs: object) -> object:
            """固定結果を返す."""
            del args, kwargs
            return outcome

    monkeypatch.setattr(scm_commands, "load_config", lambda _path: config)
    monkeypatch.setattr(scm_commands, "read_scm_token", lambda: "token")
    monkeypatch.setattr(scm_commands, "GitHubScmClient", lambda *_args: object())
    monkeypatch.setattr(scm_commands, "initialize_database", lambda _path: None)
    monkeypatch.setattr(
        scm_commands, "SqliteScmPublicationPublisher", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(scm_commands, "ScmReviewCoordinator", Coordinator)
    monkeypatch.setattr(scm_commands, "_outcome_document", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(scm_commands, "write_document", lambda *_args: None)
    arguments = _arguments(tmp_path, RunStatus.SUCCESS)
    arguments.publish = True

    assert scm_commands.execute_scm_command(arguments) == 0


def test_outcome_document_contains_positions_and_publications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scm_commands, "result_to_document", lambda _review: {"schema_version": 1})
    inline = PublicationPlan(
        publication_id="publication-inline",
        run_id="run",
        finding_id="B001-F001",
        fingerprint_hash="fingerprint-inline",
        request_hash="request-inline",
        request=CreateComment(
            PublicationKind.INLINE,
            "指摘",
            CommentPosition(NormalizedPath("src/app.py"), 12, "2" * 40),
        ),
    )
    summary = PublicationPlan(
        publication_id="publication-summary",
        run_id="run",
        finding_id=None,
        fingerprint_hash="fingerprint-summary",
        request_hash="request-summary",
        request=CreateComment(PublicationKind.SUMMARY, "概要"),
    )
    publication = PublicationResult(
        fingerprint_hash="fingerprint-inline",
        status=PublicationStatus.FAILED,
        operation=PublicationOperation.FAILED,
        external_comment_id="10",
        external_comment_url="https://example.test/10",
        safe_error_code="TEST",
    )
    outcome = ScmReviewOutcome(
        change=_change(),
        review=object(),
        plans=(inline, summary),
        publications=(publication,),
    )

    document = scm_commands._outcome_document(outcome, is_dry_run=False)
    scm = document["scm"]

    assert isinstance(scm, dict)
    assert scm["plans"][0]["file"] == "src/app.py"
    assert scm["plans"][0]["line"] == 12
    assert scm["plans"][1]["file"] is None
    assert scm["publications"][0]["safe_error_code"] == "TEST"
    assert scm["publications"][0]["operation"] == "failed"
    assert scm["metrics"]["api_call_count"] == 0


def test_database_path_must_stay_inside_repository(tmp_path: Path) -> None:
    with pytest.raises(InvalidInputError):
        scm_commands._within_repository(tmp_path / "repository", tmp_path / "outside.db")


def _arguments(repository: Path, status: RunStatus) -> Namespace:
    """SCM review 用の標準 CLI 引数を返す."""
    del status
    return Namespace(
        repository=str(repository),
        config=None,
        change="42",
        scm_command="review",
        mode="rules",
        publish=False,
        output=None,
    )


def _change() -> ScmChange:
    """出力整形用の変更要求を返す."""
    return ScmChange(
        ref=ChangeRef(ScmProvider.GITHUB, "owner/repository", "42"),
        base_sha="1" * 40,
        head_sha="2" * 40,
        state=ScmChangeState.OPEN,
        is_draft=False,
        source_repository_external_id="owner/repository",
        updated_at=datetime(2026, 9, 25, tzinfo=UTC),
    )
