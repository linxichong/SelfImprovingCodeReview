from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from self_improving_code_review.application.scm.models import (
    ScmFeedbackEventRecord,
    ScmFeedbackTarget,
)
from self_improving_code_review.domain.common.enums import FeedbackLabel
from self_improving_code_review.domain.common.errors import DatabaseError, InvalidInputError
from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.domain.scm import ChangeRef, ScmProvider
from self_improving_code_review.infrastructure.persistence.scm_feedback_repository import (
    SqliteScmFeedbackRepository,
)
from self_improving_code_review.infrastructure.persistence.sqlite import initialize_database
from self_improving_code_review.messages import Messages

_RUN_ID = "00000000-0000-4000-8000-000000000001"
_REF = ChangeRef(ScmProvider.GITHUB, "owner/repository", "42")


@pytest.mark.case("CT-M4-003")
def test_repository_matches_publication_and_atomically_saves_event_cursor(
    tmp_path: Path,
) -> None:
    database = tmp_path / "review.db"
    initialize_database(database)
    _insert_publication(database)
    _assign_change(database)
    repository = SqliteScmFeedbackRepository(database, ProjectId("project-a"))

    target = repository.find_target(_REF, "comment-10")

    assert target is not None
    assert target.run_id == _RUN_ID
    assert target.finding_id == "B001-F001"
    repository.save_event_and_cursor(
        _REF,
        ScmFeedbackEventRecord(
            event_external_id="review-comment:20",
            target=target,
            external_comment_id="comment-20",
            actor_id="reviewer-1",
            label=FeedbackLabel.TRUE_POSITIVE,
            payload_hash="a" * 64,
            mapped_feedback_id=None,
            status="processed",
            safe_error_code=None,
        ),
        "2026-09-25T00:00:00+00:00",
    )

    assert repository.has_event("github", "review-comment:20")
    assert repository.get_cursor(_REF) == "2026-09-25T00:00:00+00:00"
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute(
            "SELECT actor_id, label, payload_hash, status FROM scm_feedback_events"
        ).fetchone()
    assert row == ("reviewer-1", "true-positive", "a" * 64, "processed")
    other_project = SqliteScmFeedbackRepository(database, ProjectId("project-b"))
    for operation in (
        lambda: other_project.get_cursor(_REF),
        lambda: other_project.find_target(_REF, "comment-10"),
        lambda: other_project.save_cursor(_REF, "cursor"),
    ):
        with pytest.raises(InvalidInputError, match="NOT_FOUND"):
            operation()


@pytest.mark.case("CT-M4-003")
def test_repository_does_not_match_summary_or_unknown_comment(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    initialize_database(database)
    _insert_publication(database, kind="summary", finding_id=None)
    repository = SqliteScmFeedbackRepository(database)

    assert repository.find_target(_REF, "comment-10") is None
    assert repository.find_target(_REF, "unknown") is None
    assert repository.get_cursor(_REF) is None
    assert not repository.has_event("github", "unknown")


@pytest.mark.case("CT-M4-003")
def test_repository_inserts_and_updates_cursor_without_event(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    initialize_database(database)
    repository = SqliteScmFeedbackRepository(database)

    repository.save_cursor(_REF, "2026-09-25T00:00:00+00:00")
    repository.save_cursor(_REF, "2026-09-26T00:00:00+00:00")

    assert repository.get_cursor(_REF) == "2026-09-26T00:00:00+00:00"
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM scm_feedback_events").fetchone() == (0,)


@pytest.mark.case("CT-M4-003")
@pytest.mark.parametrize("operation", ["cursor", "event", "target"])
def test_repository_maps_sqlite_read_errors(tmp_path: Path, operation: str) -> None:
    repository = SqliteScmFeedbackRepository(tmp_path)

    def action() -> object:
        """指定された読み取り操作を一つだけ実行する."""
        if operation == "cursor":
            return repository.get_cursor(_REF)
        if operation == "event":
            return repository.has_event("github", "event")
        return repository.find_target(_REF, "comment")

    with pytest.raises(DatabaseError):
        action()


@pytest.mark.case("CT-M4-003")
def test_repository_requires_event_target(tmp_path: Path) -> None:
    repository = SqliteScmFeedbackRepository(tmp_path / "review.db")
    record = ScmFeedbackEventRecord(
        event_external_id="event",
        target=None,
        external_comment_id="comment",
        actor_id=None,
        label=None,
        payload_hash="a" * 64,
        mapped_feedback_id=None,
        status="ignored",
        safe_error_code="NO_TARGET",
    )

    with pytest.raises(DatabaseError, match=re.escape(Messages.SCM_FEEDBACK_EVENT_TARGET_REQUIRED)):
        repository.save_event_and_cursor(_REF, record, "cursor")


@pytest.mark.case("CT-M4-003")
def test_repository_maps_sqlite_save_error(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    initialize_database(database)
    target = ScmFeedbackTarget("missing", _RUN_ID, "B001-F001", "comment")
    record = ScmFeedbackEventRecord(
        event_external_id="event",
        target=target,
        external_comment_id="comment",
        actor_id=None,
        label=None,
        payload_hash="a" * 64,
        mapped_feedback_id=None,
        status="ignored",
        safe_error_code="TEST",
    )

    with pytest.raises(DatabaseError, match=re.escape(Messages.SCM_FEEDBACK_EVENT_SAVE_FAILED)):
        SqliteScmFeedbackRepository(database).save_event_and_cursor(_REF, record, "cursor")


@pytest.mark.case("CT-M4-003")
def test_repository_maps_cursor_only_save_error(tmp_path: Path) -> None:
    with pytest.raises(DatabaseError, match=re.escape(Messages.SCM_CURSOR_SAVE_FAILED)):
        SqliteScmFeedbackRepository(tmp_path).save_cursor(_REF, "cursor")


def _insert_publication(
    database: Path,
    *,
    kind: str = "inline",
    finding_id: str | None = "B001-F001",
) -> None:
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO rule_snapshots("
            "rule_snapshot_id, git_sha, rule_set_hash, created_at"
            ") VALUES('rules', NULL, 'hash', '2026-09-25T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO review_runs("
            "run_id, project_id, mode, status, source_revision, model, package_version, "
            "config_hash, prompt_version, prompt_hash, rule_snapshot_id, rule_set_hash, "
            "secret_engine_version, idempotency_key, started_at"
            ") VALUES(?, 'project', 'rules', 'success', ?, 'dataset', '0.1.0', "
            "'config', 'v1', 'prompt', 'rules', 'hash', '1.5.0', 'key', "
            "'2026-09-25T00:00:00+00:00')",
            (_RUN_ID, "2" * 40),
        )
        connection.execute(
            "INSERT INTO scm_publications("
            "publication_id, run_id, finding_id, provider, repository_external_id, "
            "change_external_id, head_sha, fingerprint_hash, kind, external_comment_id, "
            "request_hash, status, created_at, updated_at"
            ") VALUES('publication-1', ?, ?, 'github', 'owner/repository', '42', ?, "
            "'fingerprint', ?, 'comment-10', 'request', 'published', "
            "'2026-09-25T00:00:00+00:00', '2026-09-25T00:00:00+00:00')",
            (_RUN_ID, finding_id, "2" * 40, kind),
        )
        connection.commit()


def _assign_change(database: Path) -> None:
    """SCM 変更要求を project-a に所属させる。"""
    observed_at = "2026-09-25T00:00:00+00:00"
    with closing(sqlite3.connect(database)) as connection:
        connection.executemany(
            "INSERT INTO projects VALUES(?,?,?,?,?,?,?)",
            [
                ("project-a", "A", "policy-a", "active", 1, observed_at, observed_at),
                ("project-b", "B", "policy-b", "active", 1, observed_at, observed_at),
            ],
        )
        connection.execute(
            "INSERT INTO scm_changes("
            "provider, repository_external_id, change_external_id, base_sha, head_sha, "
            "state, is_draft, source_repository_external_id, latest_run_id, updated_at, "
            "observed_at) VALUES('github', 'owner/repository', '42', ?, ?, 'open', 0, "
            "'owner/repository', ?, ?, ?)",
            ("1" * 40, "2" * 40, _RUN_ID, observed_at, observed_at),
        )
        connection.execute(
            "INSERT INTO project_scm_changes VALUES(?, ?, ?, ?, ?)",
            ("project-a", "github", "owner/repository", "42", observed_at),
        )
        connection.commit()
