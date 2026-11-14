from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from self_improving_code_review.application.scm.models import PublicationPlan
from self_improving_code_review.domain.common.errors import (
    DatabaseError,
    InvalidInputError,
    InvalidStateTransitionError,
    ScmClientError,
)
from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.domain.scm import (
    ChangeRef,
    CreateComment,
    PublicationKind,
    PublicationOperation,
    PublicationStatus,
    PublishedComment,
    ScmChange,
    ScmChangeState,
    ScmProvider,
    UpdateComment,
)
from self_improving_code_review.infrastructure.persistence.scm_publisher import (
    SqliteScmPublicationPublisher,
)
from self_improving_code_review.infrastructure.persistence.sqlite import initialize_database
from self_improving_code_review.messages import Messages

_RUN_ID = "00000000-0000-4000-8000-000000000001"
_NOW = datetime(2026, 9, 25, tzinfo=UTC)
_RECONCILIATION_ERROR = "reconciliation failed"


@pytest.mark.case("CT-M4-003", "CT-M4-004")
def test_publisher_creates_reuses_and_updates_comment(tmp_path: Path) -> None:
    database = _database(tmp_path)
    client = _Client()
    publisher = _publisher(database, client)
    plan = _plan()

    created = publisher.publish(_change(), (plan,))
    reused = publisher.publish(_change(), (plan,))
    updated = publisher.publish(
        _change(),
        (replace(plan, request_hash="changed", request=replace(plan.request, body="更新")),),
    )

    assert created[0].status is PublicationStatus.PUBLISHED
    assert created[0].external_comment_id == "comment-1"
    assert created[0].operation is PublicationOperation.CREATED
    assert reused[0].operation is PublicationOperation.REUSED
    assert reused[0].external_comment_id == created[0].external_comment_id
    assert updated[0].status is PublicationStatus.PUBLISHED
    assert updated[0].operation is PublicationOperation.UPDATED
    assert client.created == 1
    assert client.updated == 1
    assert client.last_update == UpdateComment(
        kind=PublicationKind.SUMMARY,
        comment_external_id="comment-1",
        body="更新",
    )
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute(
            "SELECT status, attempt, request_hash, lease_until FROM scm_publications"
        ).fetchone()
    assert row == ("published", 2, "changed", None)


@pytest.mark.case("CT-M4-003")
def test_publisher_saves_change_without_plans(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    initialize_database(database)

    result = _publisher(database, _Client()).publish(_change(), ())

    assert result == ()
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute("SELECT latest_run_id, head_sha FROM scm_changes").fetchone()
    assert row == (None, "2" * 40)


@pytest.mark.case("CT-M4-003")
def test_publisher_saves_dry_run_plan_without_scm_api(tmp_path: Path) -> None:
    database = _database(tmp_path)
    client = _Client()

    _publisher(database, client).save_plans(_change(), (_plan(),))

    assert client.change_calls == 0
    assert client.created == 0
    assert client.updated == 0
    with closing(sqlite3.connect(database)) as connection:
        change_row = connection.execute(
            "SELECT latest_run_id, head_sha FROM scm_changes"
        ).fetchone()
        publication_row = connection.execute(
            "SELECT publication_id, run_id, request_hash, status, attempt FROM scm_publications"
        ).fetchone()
    assert change_row == (_RUN_ID, "2" * 40)
    assert publication_row == (
        "publication-test",
        _RUN_ID,
        "request",
        "planned",
        0,
    )


@pytest.mark.case("CT-M4-003")
def test_changed_dry_run_plan_is_updated_on_later_publication(tmp_path: Path) -> None:
    database = _database(tmp_path)
    client = _Client()
    publisher = _publisher(database, client)
    original = _plan()
    changed = replace(
        original,
        request_hash="changed",
        request=replace(original.request, body="更新予定"),
    )
    publisher.publish(_change(), (original,))

    publisher.save_plans(_change(), (changed,))

    assert client.created == 1
    assert client.updated == 0
    with closing(sqlite3.connect(database)) as connection:
        staged = connection.execute(
            "SELECT request_hash, status, external_comment_id FROM scm_publications"
        ).fetchone()
    assert staged == ("changed", "planned", "comment-1")

    result = publisher.publish(_change(), (changed,))

    assert result[0].status is PublicationStatus.PUBLISHED
    assert client.updated == 1


@pytest.mark.case("CT-M4-003")
@pytest.mark.parametrize("status", [PublicationStatus.STALE, PublicationStatus.BLOCKED])
def test_changed_dry_run_does_not_reopen_terminal_publication(
    tmp_path: Path,
    status: PublicationStatus,
) -> None:
    database = _database(tmp_path)
    _insert_publication(database, status=status)
    changed = replace(_plan(), request_hash="changed")

    _publisher(database, _Client()).save_plans(_change(), (changed,))

    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute("SELECT request_hash, status FROM scm_publications").fetchone()
    assert row == ("request", status.value)


@pytest.mark.case("CT-M4-003")
def test_publisher_saves_blocked_plans_without_body_or_scm_api(tmp_path: Path) -> None:
    database = _database(tmp_path)
    client = _Client()
    plan = _plan()

    _publisher(database, client).block_plans(_change(), (plan,))

    assert client.change_calls == 0
    assert client.created == 0
    assert client.updated == 0
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute(
            "SELECT request_hash, status, safe_error_code, lease_until FROM scm_publications"
        ).fetchone()
        columns = {
            item[1] for item in connection.execute("PRAGMA table_info(scm_publications)").fetchall()
        }
    assert row == ("request", "blocked", "SECRET_DETECTED", None)
    assert "body" not in columns


@pytest.mark.case("CT-M4-003")
def test_blocking_does_not_reopen_stale_publication(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _insert_publication(database, status=PublicationStatus.STALE)

    _publisher(database, _Client()).block_plans(_change(), (_plan(),))

    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute("SELECT status, safe_error_code FROM scm_publications").fetchone()
    assert row == ("stale", None)


@pytest.mark.case("CT-M4-003")
def test_publisher_can_save_empty_blocked_plan_set(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    initialize_database(database)

    _publisher(database, _Client()).block_plans(_change(), ())

    with closing(sqlite3.connect(database)) as connection:
        change_count = connection.execute("SELECT COUNT(*) FROM scm_changes").fetchone()
        publication_count = connection.execute("SELECT COUNT(*) FROM scm_publications").fetchone()
    assert change_count == (1,)
    assert publication_count == (0,)


@pytest.mark.case("CT-M4-003")
@pytest.mark.parametrize("status", [PublicationStatus.STALE, PublicationStatus.BLOCKED])
def test_publisher_returns_terminal_publication_without_api(
    tmp_path: Path,
    status: PublicationStatus,
) -> None:
    database = _database(tmp_path)
    _insert_publication(database, status=status)
    client = _Client()

    result = _publisher(database, client).publish(_change(), (_plan(),))

    assert result[0].status is status
    assert result[0].external_comment_id == "comment-old"
    assert client.created == 0
    assert client.change_calls == 0


@pytest.mark.case("CT-M4-003", "CT-M4-004")
def test_publisher_marks_stale_when_head_changes(tmp_path: Path) -> None:
    database = _database(tmp_path)
    client = _Client(latest=replace(_change(), head_sha="3" * 40))

    result = _publisher(database, client).publish(_change(), (_plan(),))

    assert result[0].status is PublicationStatus.STALE
    assert result[0].safe_error_code == "HEAD_CHANGED"
    assert client.created == 0


@pytest.mark.case("CT-M4-003")
def test_publisher_marks_stale_when_change_closes_before_publication(tmp_path: Path) -> None:
    database = _database(tmp_path)
    client = _Client(latest=replace(_change(), state=ScmChangeState.CLOSED))

    result = _publisher(database, client).publish(_change(), (_plan(),))

    assert result[0].status is PublicationStatus.STALE
    assert result[0].safe_error_code == "CHANGE_CLOSED"
    assert client.created == 0
    assert client.updated == 0
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute(
            "SELECT status, safe_error_code, lease_until FROM scm_publications"
        ).fetchone()
    assert row == ("stale", "CHANGE_CLOSED", None)


@pytest.mark.case("CT-M4-003")
@pytest.mark.parametrize("is_update", [False, True])
def test_publisher_records_safe_failure_for_scm_error(
    tmp_path: Path,
    is_update: bool,
) -> None:
    database = _database(tmp_path)
    if is_update:
        _insert_publication(database, status=PublicationStatus.FAILED)
    client = _Client(should_fail=True)

    result = _publisher(database, client).publish(_change(), (_plan(),))

    assert result[0].status is PublicationStatus.FAILED
    assert result[0].safe_error_code == "ScmClientError"
    assert result[0].external_comment_id == ("comment-old" if is_update else None)


@pytest.mark.case("CT-M4-003", "CT-M4-004")
def test_publisher_recovers_comment_after_create_response_is_lost(tmp_path: Path) -> None:
    database = _database(tmp_path)
    recovered = PublishedComment("comment-recovered", "https://example.test/recovered")
    client = _Client(
        should_fail=True,
        failure_retryable=True,
        recovered_comment=recovered,
    )

    result = _publisher(database, client).publish(_change(), (_plan(),))

    assert result[0].status is PublicationStatus.PUBLISHED
    assert result[0].external_comment_id == "comment-recovered"
    assert client.created == 1
    assert client.reconciliations == 1
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute(
            "SELECT status, external_comment_id, safe_error_code FROM scm_publications"
        ).fetchone()
    assert row == ("published", "comment-recovered", None)


@pytest.mark.case("CT-M4-003")
def test_publisher_reconciles_failed_creation_before_retry(tmp_path: Path) -> None:
    database = _database(tmp_path)
    client = _Client(should_fail=True)
    publisher = _publisher(database, client)
    first = publisher.publish(_change(), (_plan(),))
    assert first[0].status is PublicationStatus.FAILED
    client.should_fail = False
    client.recovered_comment = PublishedComment(
        "comment-existing",
        "https://example.test/existing",
    )

    second = publisher.publish(_change(), (_plan(),))

    assert second[0].status is PublicationStatus.PUBLISHED
    assert second[0].external_comment_id == "comment-existing"
    assert client.created == 1
    assert client.reconciliations == 1


@pytest.mark.case("CT-M4-003")
def test_publisher_keeps_failed_when_reconciliation_request_fails(tmp_path: Path) -> None:
    database = _database(tmp_path)
    client = _Client(should_fail=True)
    publisher = _publisher(database, client)
    publisher.publish(_change(), (_plan(),))
    client.should_fail = False
    client.reconciliation_should_fail = True

    result = publisher.publish(_change(), (_plan(),))

    assert result[0].status is PublicationStatus.FAILED
    assert result[0].safe_error_code == "RECONCILIATION_FAILED"
    assert client.created == 1


@pytest.mark.case("CT-M4-003")
def test_publisher_retries_creation_after_failed_record_has_no_match(tmp_path: Path) -> None:
    database = _database(tmp_path)
    client = _Client(should_fail=True)
    publisher = _publisher(database, client)
    publisher.publish(_change(), (_plan(),))
    client.should_fail = False

    result = publisher.publish(_change(), (_plan(),))

    assert result[0].status is PublicationStatus.PUBLISHED
    assert result[0].external_comment_id == "comment-1"
    assert client.reconciliations == 1
    assert client.created == 2


@pytest.mark.case("CT-M4-003")
@pytest.mark.parametrize("reconciliation_should_fail", [False, True])
def test_publisher_records_failure_when_lost_response_cannot_be_reconciled(
    tmp_path: Path,
    reconciliation_should_fail: bool,
) -> None:
    database = _database(tmp_path)
    client = _Client(
        should_fail=True,
        failure_retryable=True,
        reconciliation_should_fail=reconciliation_should_fail,
    )

    result = _publisher(database, client).publish(_change(), (_plan(),))

    assert result[0].status is PublicationStatus.FAILED
    assert result[0].safe_error_code == "ScmClientError"
    assert client.created == 1
    assert client.reconciliations == 1


@pytest.mark.case("CT-M4-003", "UT-M4-007")
def test_publisher_rejects_active_lease(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _insert_publication(database, status=PublicationStatus.PLANNED, has_lease=True)

    with pytest.raises(DatabaseError, match=re.escape(Messages.SCM_PUBLICATION_LEASE_HELD)):
        _publisher(database, _Client()).publish(_change(), (_plan(),))


@pytest.mark.case("CT-M4-003")
def test_publisher_maps_change_save_database_error(tmp_path: Path) -> None:
    publisher = _publisher(tmp_path, _Client())

    with pytest.raises(DatabaseError, match=re.escape(Messages.SCM_PERSISTENCE_FAILED)):
        publisher.publish(_change(), ())


@pytest.mark.case("CT-M4-003")
def test_publisher_does_not_overwrite_terminal_state_set_during_api_call(
    tmp_path: Path,
) -> None:
    """外部呼出中に終端状態へ遷移した出版物を成功で上書きしない。"""
    database = _database(tmp_path)

    @dataclass
    class _StalingClient(_Client):
        def create_comment(self, ref: ChangeRef, request: CreateComment) -> PublishedComment:
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("UPDATE scm_publications SET status='stale'")
                connection.commit()
            return super().create_comment(ref, request)

    with pytest.raises(InvalidStateTransitionError, match="published"):
        _publisher(database, _StalingClient()).publish(_change(), (_plan(),))

    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT status FROM scm_publications").fetchone() == ("stale",)


@pytest.mark.case("CT-M4-003")
def test_publisher_rejects_acquire_from_terminal_state(tmp_path: Path) -> None:
    """リース取得も遷移規則に従い、終端状態から送信状態へ戻さない。"""
    database = _database(tmp_path)
    _insert_publication(database, status=PublicationStatus.STALE)

    with pytest.raises(DatabaseError, match=re.escape(Messages.SCM_PUBLICATION_LEASE_HELD)):
        _publisher(database, _Client())._acquire("publication-old", PublicationStatus.UPDATING)


@pytest.mark.case("CT-M4-003")
def test_publisher_maps_finish_database_error(tmp_path: Path) -> None:
    database = _database(tmp_path)

    @dataclass
    class _BreakingClient(_Client):
        def create_comment(self, ref: ChangeRef, request: CreateComment) -> PublishedComment:
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("DROP TABLE scm_publications")
                connection.commit()
            return super().create_comment(ref, request)

    with pytest.raises(DatabaseError, match=re.escape(Messages.SCM_PERSISTENCE_FAILED)):
        _publisher(database, _BreakingClient()).publish(_change(), (_plan(),))


@pytest.mark.case("CT-M4-003")
def test_publisher_maps_prepare_database_error(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("DROP TABLE scm_publications")
        connection.commit()

    with pytest.raises(DatabaseError, match=re.escape(Messages.SCM_PUBLICATION_PREPARATION_FAILED)):
        _publisher(database, _Client()).publish(_change(), (_plan(),))


@pytest.mark.case("CT-M4-003")
def test_publisher_maps_acquire_database_error(tmp_path: Path) -> None:
    database = _database(tmp_path)
    client = _Client(database_to_break=database)

    with pytest.raises(
        DatabaseError, match=re.escape(Messages.SCM_PUBLICATION_LEASE_UPDATE_FAILED)
    ):
        _publisher(database, client).publish(_change(), (_plan(),))


@dataclass
class _Client:
    """公開状態機械からの外部 API 呼出を記録する."""

    latest: ScmChange | None = None
    should_fail: bool = False
    failure_retryable: bool = False
    recovered_comment: PublishedComment | None = None
    reconciliation_should_fail: bool = False
    database_to_break: Path | None = None
    created: int = 0
    updated: int = 0
    change_calls: int = 0
    reconciliations: int = 0
    last_update: UpdateComment | None = None

    def get_change(self, ref: ChangeRef) -> ScmChange:
        """最新変更要求を返し、指定時は取得後に出版表を破損させる."""
        del ref
        self.change_calls += 1
        if self.database_to_break is not None:
            with closing(sqlite3.connect(self.database_to_break)) as connection:
                connection.execute("DROP TABLE scm_publications")
                connection.commit()
        return self.latest or _change()

    def create_comment(
        self,
        ref: ChangeRef,
        request: CreateComment,
    ) -> PublishedComment:
        """新規作成を記録する."""
        del ref, request
        self.created += 1
        if self.should_fail:
            raise ScmClientError("safe", is_retryable=self.failure_retryable)
        return PublishedComment("comment-1", "https://example.test/comments/1")

    def find_created_comment(
        self,
        ref: ChangeRef,
        request: CreateComment,
    ) -> PublishedComment | None:
        """作成結果を失ったコメントの照合結果を返す."""
        del ref, request
        self.reconciliations += 1
        if self.reconciliation_should_fail:
            raise ScmClientError(_RECONCILIATION_ERROR, is_retryable=True)
        return self.recovered_comment

    def update_comment(
        self,
        ref: ChangeRef,
        request: UpdateComment,
    ) -> PublishedComment:
        """更新を記録する."""
        del ref
        self.updated += 1
        self.last_update = request
        if self.should_fail:
            raise ScmClientError("safe")
        return PublishedComment(request.comment_external_id, "https://example.test/comments/1")


def _publisher(database: Path, client: _Client) -> SqliteScmPublicationPublisher:
    """決定的な時刻と識別子で公開器を構築する."""
    return SqliteScmPublicationPublisher(
        database,
        client,
        now_utc=lambda: _NOW,
    )


def _change() -> ScmChange:
    """標準の変更要求を返す."""
    return ScmChange(
        ref=ChangeRef(ScmProvider.GITHUB, "owner/repository", "42"),
        base_sha="1" * 40,
        head_sha="2" * 40,
        state=ScmChangeState.OPEN,
        is_draft=False,
        source_repository_external_id="owner/repository",
        updated_at=_NOW,
    )


def _plan() -> PublicationPlan:
    """標準の要約コメント計画を返す."""
    return PublicationPlan(
        publication_id="publication-test",
        run_id=_RUN_ID,
        finding_id=None,
        fingerprint_hash="fingerprint",
        request_hash="request",
        request=CreateComment(PublicationKind.SUMMARY, "レビュー結果"),
    )


def _database(tmp_path: Path) -> Path:
    """移行済み DB に参照先レビュー実行を作成する."""
    database = tmp_path / "review.db"
    initialize_database(database)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO rule_snapshots(rule_snapshot_id, git_sha, rule_set_hash, created_at) "
            "VALUES('rules', NULL, 'hash', ?)",
            (_NOW.isoformat(),),
        )
        connection.execute(
            "INSERT INTO review_runs("
            "run_id, project_id, mode, status, source_revision, model, package_version, "
            "config_hash, prompt_version, prompt_hash, rule_snapshot_id, rule_set_hash, "
            "secret_engine_version, idempotency_key, started_at"
            ") VALUES(?, 'project', 'rules', 'success', ?, 'dataset', '0.1.0', "
            "'config', 'v1', 'prompt', 'rules', 'hash', '1.5.0', 'key', ?)",
            (_RUN_ID, "2" * 40, _NOW.isoformat()),
        )
        connection.commit()
    return database


def _insert_publication(
    database: Path,
    *,
    status: PublicationStatus,
    has_lease: bool = False,
) -> None:
    """既存出版物を任意状態で作成する."""
    lease = "2099-01-01T00:00:00+00:00" if has_lease else None
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO scm_publications("
            "publication_id, run_id, finding_id, provider, repository_external_id, "
            "change_external_id, head_sha, fingerprint_hash, kind, external_comment_id, "
            "external_comment_url, request_hash, status, lease_until, created_at, updated_at"
            ") VALUES('publication-old', ?, NULL, 'github', 'owner/repository', '42', ?, "
            "'fingerprint', 'summary', 'comment-old', 'https://example.test/old', "
            "'request', ?, ?, ?, ?)",
            (_RUN_ID, "2" * 40, status.value, lease, _NOW.isoformat(), _NOW.isoformat()),
        )
        connection.commit()


@pytest.mark.case("CT-M4-003")
def test_publisher_creates_change_assignment_and_hides_it_from_other_project(
    tmp_path: Path,
) -> None:
    """SCM 変更要求と所属を原子的に保存し、別プロジェクトから隠す。"""
    database = tmp_path / "review.db"
    initialize_database(database)
    _insert_isolation_projects(database)
    project_a = ProjectId("project-a")
    change = _change()
    publisher = SqliteScmPublicationPublisher(
        database,
        _Client(),
        now_utc=lambda: _NOW,
        project_id=project_a,
    )

    publisher.save_plans(change, ())
    publisher.save_plans(change, ())

    with closing(sqlite3.connect(database)) as connection:
        assignment = connection.execute(
            "SELECT project_id FROM project_scm_changes "
            "WHERE provider='github' AND repository_external_id='owner/repository' "
            "AND change_external_id='42'"
        ).fetchone()
    assert assignment == (str(project_a),)
    with pytest.raises(InvalidInputError, match="NOT_FOUND"):
        SqliteScmPublicationPublisher(
            database,
            _Client(),
            now_utc=lambda: _NOW,
            project_id=ProjectId("project-b"),
        ).save_plans(change, ())


@pytest.mark.case("CT-M4-003")
def test_publisher_rejects_unassigned_change_and_rolls_back_failed_assignment(
    tmp_path: Path,
) -> None:
    """所属なし資源を暗黙採用せず、所属保存失敗時は本体も残さない。"""
    database = tmp_path / "review.db"
    initialize_database(database)
    _insert_isolation_projects(database)
    unassigned = replace(
        _change(),
        ref=ChangeRef(ScmProvider.GITHUB, "owner/repository", "43"),
    )
    _insert_unassigned_change(database, unassigned)
    publisher = SqliteScmPublicationPublisher(
        database,
        _Client(),
        now_utc=lambda: _NOW,
        project_id=ProjectId("project-a"),
    )

    with pytest.raises(InvalidInputError, match="NOT_FOUND"):
        publisher.save_plans(unassigned, ())

    missing_project_change = replace(
        unassigned,
        ref=ChangeRef(ScmProvider.GITHUB, "owner/repository", "44"),
    )
    with pytest.raises(DatabaseError):
        SqliteScmPublicationPublisher(
            database,
            _Client(),
            now_utc=lambda: _NOW,
            project_id=ProjectId("missing-project"),
        ).save_plans(missing_project_change, ())

    with closing(sqlite3.connect(database)) as connection:
        leaked = connection.execute(
            "SELECT COUNT(*) FROM scm_changes WHERE change_external_id='44'"
        ).fetchone()
    assert leaked == (0,)


def _insert_isolation_projects(database: Path) -> None:
    """SCM 分離テスト用の二つのプロジェクトを保存する。"""
    with closing(sqlite3.connect(database)) as connection:
        connection.executemany(
            "INSERT INTO projects VALUES(?,?,?,?,?,?,?)",
            [
                ("project-a", "A", "policy-a", "active", 1, _NOW.isoformat(), _NOW.isoformat()),
                ("project-b", "B", "policy-b", "active", 1, _NOW.isoformat(), _NOW.isoformat()),
            ],
        )
        connection.commit()


def _insert_unassigned_change(database: Path, change: ScmChange) -> None:
    """プロジェクト所属を持たない既存 SCM 変更要求を保存する。"""
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO scm_changes VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                change.ref.provider.value,
                change.ref.repository_external_id,
                change.ref.change_external_id,
                change.base_sha,
                change.head_sha,
                change.state.value,
                int(change.is_draft),
                change.source_repository_external_id,
                None,
                change.updated_at.isoformat(),
                _NOW.isoformat(),
            ),
        )
        connection.commit()
