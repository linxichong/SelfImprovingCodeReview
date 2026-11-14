"""SCM フィードバックイベントとカーソルの SQLite 保存."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from self_improving_code_review.application.ports.scm_feedback import ScmFeedbackRepository
from self_improving_code_review.application.scm.models import (
    ScmFeedbackEventRecord,
    ScmFeedbackTarget,
)
from self_improving_code_review.domain.common.errors import DatabaseError, InvalidInputError
from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.domain.scm.models import ChangeRef
from self_improving_code_review.messages import Messages

__all__ = ["SqliteScmFeedbackRepository"]


_PROJECT_RESOURCE_NOT_FOUND = "NOT_FOUND"


class SqliteScmFeedbackRepository(ScmFeedbackRepository):
    """外部イベントを本文なしで監査保存し、カーソルと原子的に確定する."""

    def __init__(self, database_path: Path, project_id: ProjectId | None = None) -> None:
        """SQLite ファイルの保存先と任意のプロジェクト境界を保持する."""
        self._database_path = database_path
        self._project_id = project_id

    def get_cursor(self, ref: ChangeRef) -> str | None:
        """対象変更要求の確定済みカーソルを返す."""
        try:
            with closing(sqlite3.connect(self._database_path)) as connection:
                self._require_change(connection, ref)
                row = connection.execute(
                    "SELECT cursor FROM scm_event_cursors WHERE provider=? AND "
                    "repository_external_id=? AND change_external_id=?",
                    (
                        ref.provider.value,
                        ref.repository_external_id,
                        ref.change_external_id,
                    ),
                ).fetchone()
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.SCM_CURSOR_READ_FAILED) from exc
        return str(row[0]) if row is not None else None

    def has_event(self, provider: str, event_external_id: str) -> bool:
        """同じ外部イベント ID が監査保存済みの場合は True を返す."""
        try:
            with closing(sqlite3.connect(self._database_path)) as connection:
                if self._project_id is None:
                    row = connection.execute(
                        "SELECT 1 FROM scm_feedback_events "
                        "WHERE provider=? AND external_event_id=?",
                        (provider, event_external_id),
                    ).fetchone()
                else:
                    row = connection.execute(
                        "SELECT 1 FROM scm_feedback_events AS event "
                        "JOIN scm_publications AS publication "
                        "ON publication.publication_id=event.publication_id "
                        "JOIN project_scm_changes AS assignment "
                        "ON assignment.provider=publication.provider "
                        "AND assignment.repository_external_id="
                        "publication.repository_external_id "
                        "AND assignment.change_external_id=publication.change_external_id "
                        "WHERE event.provider=? AND event.external_event_id=? "
                        "AND assignment.project_id=?",
                        (provider, event_external_id, str(self._project_id)),
                    ).fetchone()
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.SCM_EVENT_LOOKUP_FAILED) from exc
        return row is not None

    def find_target(
        self,
        ref: ChangeRef,
        external_comment_id: str,
    ) -> ScmFeedbackTarget | None:
        """公開済み行コメントと一致する実行・指摘を返す."""
        try:
            with closing(sqlite3.connect(self._database_path)) as connection:
                self._require_change(connection, ref)
                row = connection.execute(
                    "SELECT publication_id, run_id, finding_id, external_comment_id "
                    "FROM scm_publications WHERE provider=? AND repository_external_id=? "
                    "AND change_external_id=? AND external_comment_id=? AND kind='inline' "
                    "AND status='published' AND finding_id IS NOT NULL",
                    (
                        ref.provider.value,
                        ref.repository_external_id,
                        ref.change_external_id,
                        external_comment_id,
                    ),
                ).fetchone()
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.SCM_PUBLICATION_LOOKUP_FAILED) from exc
        if row is None:
            return None
        return ScmFeedbackTarget(
            publication_id=str(row[0]),
            run_id=str(row[1]),
            finding_id=str(row[2]),
            external_comment_id=str(row[3]),
        )

    def _require_change(
        self,
        connection: sqlite3.Connection,
        ref: ChangeRef,
    ) -> None:
        """指定プロジェクトに所属する SCM 変更要求だけを参照可能にする."""
        if self._project_id is None:
            return
        row = connection.execute(
            "SELECT 1 FROM project_scm_changes "
            "WHERE project_id=? AND provider=? AND repository_external_id=? "
            "AND change_external_id=?",
            (
                str(self._project_id),
                ref.provider.value,
                ref.repository_external_id,
                ref.change_external_id,
            ),
        ).fetchone()
        if row is None:
            raise InvalidInputError(_PROJECT_RESOURCE_NOT_FOUND)

    def save_event_and_cursor(
        self,
        ref: ChangeRef,
        record: ScmFeedbackEventRecord,
        cursor: str,
    ) -> None:
        """イベントと次回カーソルを一つの短いトランザクションで保存する."""
        if record.target is None:
            message = Messages.SCM_FEEDBACK_EVENT_TARGET_REQUIRED
            raise DatabaseError(message)
        observed_at = datetime.now(UTC).isoformat()
        processed_at = observed_at if record.status in {"processed", "ignored", "failed"} else None
        try:
            with closing(sqlite3.connect(self._database_path)) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                self._require_change(connection, ref)
                connection.execute(
                    "INSERT OR IGNORE INTO scm_feedback_events("
                    "provider, external_event_id, publication_id, external_comment_id, "
                    "actor_id, label, payload_hash, mapped_feedback_id, status, "
                    "safe_error_code, observed_at, processed_at"
                    ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        ref.provider.value,
                        record.event_external_id,
                        record.target.publication_id,
                        record.target.external_comment_id,
                        record.actor_id,
                        record.label.value if record.label is not None else None,
                        record.payload_hash,
                        record.mapped_feedback_id,
                        record.status,
                        record.safe_error_code,
                        observed_at,
                        processed_at,
                    ),
                )
                _upsert_cursor(connection, ref, cursor, observed_at)
                connection.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.SCM_FEEDBACK_EVENT_SAVE_FAILED) from exc

    def save_cursor(self, ref: ChangeRef, cursor: str) -> None:
        """監査対象外または重複イベントの処理済み位置だけを保存する."""
        try:
            with closing(sqlite3.connect(self._database_path)) as connection:
                self._require_change(connection, ref)
                _upsert_cursor(connection, ref, cursor, datetime.now(UTC).isoformat())
                connection.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.SCM_CURSOR_SAVE_FAILED) from exc


def _upsert_cursor(
    connection: sqlite3.Connection,
    ref: ChangeRef,
    cursor: str,
    updated_at: str,
) -> None:
    """指定トランザクション内で変更要求のカーソルを挿入または更新する."""
    connection.execute(
        "INSERT INTO scm_event_cursors("
        "provider, repository_external_id, change_external_id, cursor, updated_at"
        ") VALUES(?, ?, ?, ?, ?) ON CONFLICT("
        "provider, repository_external_id, change_external_id"
        ") DO UPDATE SET cursor=excluded.cursor, updated_at=excluded.updated_at",
        (
            ref.provider.value,
            ref.repository_external_id,
            ref.change_external_id,
            cursor,
            updated_at,
        ),
    )
