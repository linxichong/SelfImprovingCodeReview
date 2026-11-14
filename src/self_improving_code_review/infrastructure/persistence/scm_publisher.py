"""短い SQLite トランザクションで SCM コメントを冪等に公開する."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from self_improving_code_review.application.ports.scm_client import ScmClient
from self_improving_code_review.application.ports.scm_publication import PublicationPublisher
from self_improving_code_review.application.scm.models import (
    PublicationPlan,
    PublicationResult,
)
from self_improving_code_review.domain.common.errors import (
    DatabaseError,
    InvalidInputError,
    InvalidStateTransitionError,
    ScmClientError,
)
from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.domain.scm.models import (
    PublicationOperation,
    PublicationStatus,
    PublishedComment,
    ScmChange,
    ScmChangeState,
    UpdateComment,
)
from self_improving_code_review.domain.scm.transitions import allowed_source_statuses
from self_improving_code_review.messages import Messages

__all__ = ["SqliteScmPublicationPublisher"]

_LEASE_SECONDS = 60
# 永続化する安全な理由コードであり、機密情報そのものではない。
_SECRET_DETECTED = "SECRET_DETECTED"  # nosec B105

_PROJECT_RESOURCE_NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True, slots=True)
class _StoredPublication:
    """公開処理の再開判断に必要な保存済み列."""

    publication_id: str
    external_comment_id: str | None
    external_comment_url: str | None
    request_hash: str
    status: PublicationStatus


class SqliteScmPublicationPublisher(PublicationPublisher):
    """外部呼び出し中に DB トランザクションを保持しない公開実装."""

    def __init__(
        self,
        database_path: Path,
        scm_client: ScmClient,
        *,
        project_id: ProjectId | None = None,
        now_utc: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """DB、SCM ポートおよび注入可能な時刻を保持する."""
        self._database_path = database_path
        self._project_id = project_id
        self._scm_client = scm_client
        self._now_utc = now_utc

    def publish(
        self,
        change: ScmChange,
        plans: tuple[PublicationPlan, ...],
    ) -> tuple[PublicationResult, ...]:
        """各計画を再利用、更新または新規作成して結果を安定順で返す."""
        self._save_change(change, plans[0].run_id if plans else None)
        return tuple(self._publish_one(change, plan) for plan in plans)

    def save_plans(
        self,
        change: ScmChange,
        plans: tuple[PublicationPlan, ...],
    ) -> None:
        """変更要求と投稿計画を保存し、SCM API は呼び出さない."""
        self._save_change(change, plans[0].run_id if plans else None)
        for plan in plans:
            self._prepare(change, plan)

    def block_plans(
        self,
        change: ScmChange,
        plans: tuple[PublicationPlan, ...],
    ) -> None:
        """機密情報検出時に本文を保存せず全計画を遮断状態へ確定する."""
        self._save_change(change, plans[0].run_id if plans else None)
        for plan in plans:
            stored = self._prepare(change, plan)
            if stored.status is PublicationStatus.STALE:
                continue
            self._finish(
                stored.publication_id,
                PublicationStatus.BLOCKED,
                None,
                _SECRET_DETECTED,
            )

    def _publish_one(
        self,
        change: ScmChange,
        plan: PublicationPlan,
    ) -> PublicationResult:
        """単一計画を取得または作成し、外部 API 呼び出しを一回実行する."""
        stored = self._prepare(change, plan)
        if (
            stored.status is PublicationStatus.PUBLISHED
            and stored.request_hash == plan.request_hash
        ):
            return _result(stored, plan.fingerprint_hash)
        if stored.status in {PublicationStatus.STALE, PublicationStatus.BLOCKED}:
            return PublicationResult(
                fingerprint_hash=plan.fingerprint_hash,
                status=stored.status,
                operation=(
                    PublicationOperation.STALE
                    if stored.status is PublicationStatus.STALE
                    else PublicationOperation.BLOCKED
                ),
                external_comment_id=stored.external_comment_id,
                external_comment_url=stored.external_comment_url,
            )
        latest = self._scm_client.get_change(change.ref)
        if latest.head_sha != change.head_sha:
            self._finish(stored.publication_id, PublicationStatus.STALE, None, "HEAD_CHANGED")
            return PublicationResult(
                fingerprint_hash=plan.fingerprint_hash,
                status=PublicationStatus.STALE,
                operation=PublicationOperation.STALE,
                safe_error_code="HEAD_CHANGED",
            )
        if latest.state is not ScmChangeState.OPEN:
            self._finish(stored.publication_id, PublicationStatus.STALE, None, "CHANGE_CLOSED")
            return PublicationResult(
                fingerprint_hash=plan.fingerprint_hash,
                status=PublicationStatus.STALE,
                operation=PublicationOperation.STALE,
                safe_error_code="CHANGE_CLOSED",
            )
        if stored.external_comment_id is None and stored.status in {
            PublicationStatus.FAILED,
            PublicationStatus.PUBLISHING,
        }:
            try:
                recovered = self._scm_client.find_created_comment(change.ref, plan.request)
            except ScmClientError:
                self._finish(
                    stored.publication_id,
                    PublicationStatus.FAILED,
                    None,
                    "RECONCILIATION_FAILED",
                )
                return PublicationResult(
                    fingerprint_hash=plan.fingerprint_hash,
                    status=PublicationStatus.FAILED,
                    operation=PublicationOperation.FAILED,
                    safe_error_code="RECONCILIATION_FAILED",
                )
            if recovered is not None:
                return self._finish_recovered(stored.publication_id, plan, recovered)
        target = (
            PublicationStatus.UPDATING
            if stored.external_comment_id is not None
            else PublicationStatus.PUBLISHING
        )
        self._acquire(stored.publication_id, target)
        try:
            if stored.external_comment_id is None:
                published = self._scm_client.create_comment(change.ref, plan.request)
            else:
                published = self._scm_client.update_comment(
                    change.ref,
                    UpdateComment(
                        kind=plan.kind,
                        comment_external_id=stored.external_comment_id,
                        body=plan.request.body,
                    ),
                )
        except ScmClientError as exc:
            if stored.external_comment_id is None and exc.is_retryable:
                try:
                    recovered = self._scm_client.find_created_comment(change.ref, plan.request)
                except ScmClientError:
                    recovered = None
                if recovered is not None:
                    return self._finish_recovered(stored.publication_id, plan, recovered)
            error_code = (
                f"HTTP_{exc.status_code}" if exc.status_code is not None else type(exc).__name__
            )
            self._finish(stored.publication_id, PublicationStatus.FAILED, None, error_code)
            return PublicationResult(
                fingerprint_hash=plan.fingerprint_hash,
                status=PublicationStatus.FAILED,
                operation=PublicationOperation.FAILED,
                external_comment_id=stored.external_comment_id,
                external_comment_url=stored.external_comment_url,
                safe_error_code=error_code,
            )
        self._finish(
            stored.publication_id,
            PublicationStatus.PUBLISHED,
            (published.comment_external_id, published.url),
            None,
        )
        return PublicationResult(
            fingerprint_hash=plan.fingerprint_hash,
            status=PublicationStatus.PUBLISHED,
            operation=(
                PublicationOperation.CREATED
                if stored.external_comment_id is None
                else PublicationOperation.UPDATED
            ),
            external_comment_id=published.comment_external_id,
            external_comment_url=published.url,
        )

    def _finish_recovered(
        self,
        publication_id: str,
        plan: PublicationPlan,
        published: PublishedComment,
    ) -> PublicationResult:
        """応答を失った作成済みコメントを出版成功として確定する."""
        external_id = published.comment_external_id
        external_url = published.url
        self._finish(
            publication_id,
            PublicationStatus.PUBLISHED,
            (external_id, external_url),
            None,
        )
        return PublicationResult(
            fingerprint_hash=plan.fingerprint_hash,
            status=PublicationStatus.PUBLISHED,
            operation=PublicationOperation.RECOVERED,
            external_comment_id=external_id,
            external_comment_url=external_url,
        )

    def _save_change(self, change: ScmChange, run_id: str | None) -> None:
        """観測した変更要求を最新スナップショットとして保存する."""
        observed_at = self._now_utc().isoformat()
        statement = (
            "INSERT INTO scm_changes("
            "provider, repository_external_id, change_external_id, base_sha, head_sha, "
            "state, is_draft, source_repository_external_id, latest_run_id, updated_at, "
            "observed_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider, repository_external_id, change_external_id) DO UPDATE SET "
            "base_sha=excluded.base_sha, head_sha=excluded.head_sha, state=excluded.state, "
            "is_draft=excluded.is_draft, "
            "source_repository_external_id=excluded.source_repository_external_id, "
            "latest_run_id=excluded.latest_run_id, updated_at=excluded.updated_at, "
            "observed_at=excluded.observed_at"
        )
        parameters = (
            change.ref.provider.value,
            change.ref.repository_external_id,
            change.ref.change_external_id,
            change.base_sha,
            change.head_sha,
            change.state.value,
            int(change.is_draft),
            change.source_repository_external_id,
            run_id,
            change.updated_at.isoformat(),
            observed_at,
        )
        if self._project_id is None:
            self._execute(statement, parameters)
            return
        self._save_project_change(self._project_id, change, statement, parameters, observed_at)

    def _save_project_change(
        self,
        project_id: ProjectId,
        change: ScmChange,
        statement: str,
        parameters: tuple[object, ...],
        observed_at: str,
    ) -> None:
        """SCM 変更要求とプロジェクト所属を同じトランザクションで保存する."""
        key = (
            change.ref.provider.value,
            change.ref.repository_external_id,
            change.ref.change_external_id,
        )
        try:
            with closing(sqlite3.connect(self._database_path)) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("BEGIN IMMEDIATE")
                membership = connection.execute(
                    "SELECT project_id FROM project_scm_changes "
                    "WHERE provider=? AND repository_external_id=? "
                    "AND change_external_id=?",
                    key,
                ).fetchone()
                exists = connection.execute(
                    "SELECT 1 FROM scm_changes WHERE provider=? "
                    "AND repository_external_id=? AND change_external_id=?",
                    key,
                ).fetchone()
                _validate_project_change_membership(membership, exists, project_id)
                connection.execute(statement, parameters)
                if membership is None:
                    connection.execute(
                        "INSERT INTO project_scm_changes("
                        "project_id, provider, repository_external_id, "
                        "change_external_id, adopted_at) VALUES(?, ?, ?, ?, ?)",
                        (str(project_id), *key, observed_at),
                    )
                connection.commit()
        except InvalidInputError:
            raise
        except sqlite3.Error as exc:
            message = Messages.SCM_PROJECT_ASSIGNMENT_FAILED
            raise DatabaseError(message) from exc

    def _prepare(self, change: ScmChange, plan: PublicationPlan) -> _StoredPublication:
        """一意な投稿単位を取得し、存在しなければ計画状態で作成する."""
        now = self._now_utc().isoformat()
        try:
            with closing(sqlite3.connect(self._database_path)) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                row = connection.execute(
                    "SELECT publication_id, external_comment_id, external_comment_url, "
                    "request_hash, status FROM scm_publications WHERE provider=? AND "
                    "repository_external_id=? AND change_external_id=? AND head_sha=? "
                    "AND kind=? AND fingerprint_hash=?",
                    (
                        change.ref.provider.value,
                        change.ref.repository_external_id,
                        change.ref.change_external_id,
                        change.head_sha,
                        plan.kind.value,
                        plan.fingerprint_hash,
                    ),
                ).fetchone()
                if row is None:
                    publication_id = plan.publication_id
                    connection.execute(
                        "INSERT INTO scm_publications("
                        "publication_id, run_id, finding_id, provider, "
                        "repository_external_id, change_external_id, head_sha, "
                        "fingerprint_hash, kind, request_hash, status, created_at, updated_at"
                        ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?)",
                        (
                            publication_id,
                            plan.run_id,
                            plan.finding_id,
                            change.ref.provider.value,
                            change.ref.repository_external_id,
                            change.ref.change_external_id,
                            change.head_sha,
                            plan.fingerprint_hash,
                            plan.kind.value,
                            plan.request_hash,
                            now,
                            now,
                        ),
                    )
                    connection.commit()
                    return _StoredPublication(
                        publication_id=publication_id,
                        external_comment_id=None,
                        external_comment_url=None,
                        request_hash=plan.request_hash,
                        status=PublicationStatus.PLANNED,
                    )
                stored = _stored_publication(row)
                if stored.request_hash != plan.request_hash:
                    if stored.status in {
                        PublicationStatus.STALE,
                        PublicationStatus.BLOCKED,
                    }:
                        return stored
                    cursor = connection.execute(
                        "UPDATE scm_publications SET run_id=?, finding_id=?, request_hash=?, "
                        "status='planned', lease_until=NULL, safe_error_code=NULL, "
                        "updated_at=? WHERE publication_id=? AND "
                        "status IN (SELECT value FROM json_each(?))",
                        (
                            plan.run_id,
                            plan.finding_id,
                            plan.request_hash,
                            now,
                            stored.publication_id,
                            _source_statuses_json(PublicationStatus.PLANNED),
                        ),
                    )
                    _require_transition(cursor.rowcount, PublicationStatus.PLANNED)
                    connection.commit()
                    return _StoredPublication(
                        publication_id=stored.publication_id,
                        external_comment_id=stored.external_comment_id,
                        external_comment_url=stored.external_comment_url,
                        # 更新要否は保存前のハッシュとの差で判定する。
                        request_hash=stored.request_hash,
                        status=PublicationStatus.PLANNED,
                    )
                return stored
        except sqlite3.Error as exc:
            message = Messages.SCM_PUBLICATION_PREPARATION_FAILED
            raise DatabaseError(message) from exc

    def _acquire(self, publication_id: str, target: PublicationStatus) -> None:
        """期限付きリースを取得し、外部呼び出し前の状態へ遷移する."""
        now = self._now_utc()
        lease_until = (now + timedelta(seconds=_LEASE_SECONDS)).isoformat()
        try:
            with closing(sqlite3.connect(self._database_path)) as connection:
                cursor = connection.execute(
                    "UPDATE scm_publications SET status=?, attempt=attempt+1, lease_until=?, "
                    "safe_error_code=NULL, updated_at=? WHERE publication_id=? AND "
                    "(lease_until IS NULL OR lease_until < ?) AND "
                    "status IN (SELECT value FROM json_each(?))",
                    (
                        target.value,
                        lease_until,
                        now.isoformat(),
                        publication_id,
                        now.isoformat(),
                        _source_statuses_json(target),
                    ),
                )
                connection.commit()
                if cursor.rowcount != 1:
                    message = Messages.SCM_PUBLICATION_LEASE_HELD
                    raise DatabaseError(message)
        except sqlite3.Error as exc:
            message = Messages.SCM_PUBLICATION_LEASE_UPDATE_FAILED
            raise DatabaseError(message) from exc

    def _finish(
        self,
        publication_id: str,
        status: PublicationStatus,
        external: tuple[str, str] | None,
        error_code: str | None,
    ) -> None:
        """外部呼び出し結果を確定し、リースを解放する."""
        external_id = external[0] if external is not None else None
        external_url = external[1] if external is not None else None
        try:
            with closing(sqlite3.connect(self._database_path)) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                cursor = connection.execute(
                    "UPDATE scm_publications SET status=?, "
                    "external_comment_id=COALESCE(?, external_comment_id), "
                    "external_comment_url=COALESCE(?, external_comment_url), "
                    "lease_until=NULL, safe_error_code=?, updated_at=? "
                    "WHERE publication_id=? AND status IN (SELECT value FROM json_each(?))",
                    (
                        status.value,
                        external_id,
                        external_url,
                        error_code,
                        self._now_utc().isoformat(),
                        publication_id,
                        _source_statuses_json(status),
                    ),
                )
                _require_transition(cursor.rowcount, status)
                connection.commit()
        except sqlite3.Error as exc:
            message = Messages.SCM_PERSISTENCE_FAILED
            raise DatabaseError(message) from exc

    def _execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        """単一の更新文を短いトランザクションで確定する."""
        try:
            with closing(sqlite3.connect(self._database_path)) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute(statement, parameters)
                connection.commit()
        except sqlite3.Error as exc:
            message = Messages.SCM_PERSISTENCE_FAILED
            raise DatabaseError(message) from exc


def _source_statuses_json(target: PublicationStatus) -> str:
    """``target`` へ遷移できる元状態を SQL パラメーター用 JSON 配列で返す."""
    return json.dumps([item.value for item in allowed_source_statuses(target)])


def _require_transition(rowcount: int, target: PublicationStatus) -> None:
    """条件付き更新が 1 行に適用されたことを確認する."""
    if rowcount != 1:
        message = Messages.scm_publication_transition_rejected(target.value)
        raise InvalidStateTransitionError(message)


def _validate_project_change_membership(
    membership: tuple[object, ...] | None,
    existing_change: tuple[object, ...] | None,
    project_id: ProjectId,
) -> None:
    """既存 SCM 変更要求が指定プロジェクトに所属することを検証する."""
    if membership is None and existing_change is not None:
        raise InvalidInputError(_PROJECT_RESOURCE_NOT_FOUND)
    if membership is not None and str(membership[0]) != str(project_id):
        raise InvalidInputError(_PROJECT_RESOURCE_NOT_FOUND)


def _stored_publication(row: tuple[object, ...]) -> _StoredPublication:
    """SQLite 行を保存済み公開物へ変換する."""
    return _StoredPublication(
        publication_id=str(row[0]),
        external_comment_id=str(row[1]) if row[1] is not None else None,
        external_comment_url=str(row[2]) if row[2] is not None else None,
        request_hash=str(row[3]),
        status=PublicationStatus(str(row[4])),
    )


def _result(
    stored: _StoredPublication,
    fingerprint_hash: str,
) -> PublicationResult:
    """保存済み公開物をアプリケーション結果へ変換する."""
    return PublicationResult(
        fingerprint_hash=fingerprint_hash,
        status=stored.status,
        operation=PublicationOperation.REUSED,
        external_comment_id=stored.external_comment_id,
        external_comment_url=stored.external_comment_url,
    )
