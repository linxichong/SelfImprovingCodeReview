"""実行登録、再開と囲い込み付き最終確定を同一接続で行う."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from self_improving_code_review.application.ports.run_lease import RunLeaseIdentity
from self_improving_code_review.application.review.models import (
    ReviewMetrics,
    RunRecord,
)
from self_improving_code_review.domain.common.enums import (
    RunStatus,
)
from self_improving_code_review.domain.common.errors import (
    ConcurrencyConflictError,
    DatabaseError,
)
from self_improving_code_review.domain.common.values import (
    RunId,
)
from self_improving_code_review.infrastructure.persistence.review_metrics_codec import (
    metrics_document,
)
from self_improving_code_review.infrastructure.persistence.review_row_mapper import row_to_run
from self_improving_code_review.messages import Messages

_ACTIVE_RUN_CONFLICT_FRAGMENT = "review_runs.idempotency_key"


class SqliteRunRepository:
    """実行リポジトリの SQLite 実装."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """同一作業単位で使用する SQLite 接続を保持する."""
        self._connection = connection

    def create(self, run: RunRecord) -> None:
        """実行を登録する."""
        try:
            self._create(run)
        except sqlite3.IntegrityError as exc:
            if _ACTIVE_RUN_CONFLICT_FRAGMENT in str(exc):
                raise ConcurrencyConflictError(Messages.RUN_LEASE_CONFLICT) from exc
            raise

    def _create(self, run: RunRecord) -> None:
        """有効実行の一意制約を例外変換できる境界で INSERT する."""
        self._connection.execute(
            """
            INSERT INTO review_runs(
                run_id, project_id, mode, status, source_revision, model,
                package_version, config_hash, prompt_version, prompt_hash,
                rule_snapshot_id, rule_set_hash, secret_engine_version,
                idempotency_key, started_at, finished_at, memory_snapshot_id,
                pipeline_config_hash, agent_prompt_version, agent_prompt_hash,
                reflection_prompt_version, reflection_prompt_hash, resume_count
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(run.run_id),
                str(run.project_id),
                run.mode.value,
                run.status.value,
                run.source_revision,
                run.model,
                run.versions.package_version,
                run.versions.pipeline_config_hash or run.versions.config_hash,
                run.versions.prompt_version,
                run.versions.prompt_hash,
                run.versions.rule_snapshot_id,
                run.versions.rule_set_hash,
                run.versions.secret_engine_version,
                run.idempotency_key,
                run.started_at.isoformat(),
                run.finished_at.isoformat() if run.finished_at else None,
                run.versions.memory_snapshot_id,
                run.versions.config_hash,
                run.versions.agent_prompt_version,
                run.versions.agent_prompt_hash,
                run.versions.reflection_prompt_version,
                run.versions.reflection_prompt_hash,
                run.resume_count,
            ),
        )

    def finalize(
        self,
        run_id: RunId,
        status: RunStatus,
        metrics: ReviewMetrics,
        finished_at: datetime,
        memory_snapshot_id: str | None = None,
        *,
        lease_identity: RunLeaseIdentity | None = None,
        lease_checked_at: datetime | None = None,
    ) -> None:
        """実行を完了し、集約測定値を永続化する."""
        parameters: tuple[object, ...] = (
            status.value,
            json.dumps(metrics_document(metrics), sort_keys=True),
            finished_at.isoformat(),
            memory_snapshot_id,
            str(run_id),
        )
        if lease_identity is None:
            cursor = self._connection.execute(
                """
                UPDATE review_runs
                SET status = ?, metrics_json = ?, finished_at = ?, memory_snapshot_id = ?
                WHERE run_id = ?
                """,
                parameters,
            )
        else:
            checked_at = lease_checked_at or finished_at
            cursor = self._connection.execute(
                """
                UPDATE review_runs
                SET status = ?, metrics_json = ?, finished_at = ?, memory_snapshot_id = ?
                WHERE run_id = ? AND EXISTS(
                    SELECT 1 FROM run_leases
                    WHERE run_id = ? AND lease_owner = ? AND fencing_token = ?
                      AND lease_expires_at > ?
                )
                """,
                (
                    *parameters,
                    str(run_id),
                    lease_identity.owner,
                    lease_identity.fencing_token,
                    checked_at.isoformat(),
                ),
            )
        if cursor.rowcount != 1:
            if lease_identity is not None:
                raise ConcurrencyConflictError(Messages.RUN_LEASE_LOST)
            message = Messages.run_not_found_during_finalize(str(run_id))
            raise DatabaseError(message)
        if lease_identity is not None:
            self._connection.execute(
                """
                DELETE FROM run_leases
                WHERE run_id = ? AND lease_owner = ? AND fencing_token = ?
                """,
                (str(run_id), lease_identity.owner, lease_identity.fencing_token),
            )

    def find_completed_by_idempotency_key(self, key: str) -> RunRecord | None:
        """`key` に一致する終了済み実行を返す."""
        row = self._connection.execute(
            """
            SELECT run_id, project_id, mode, status, source_revision, model,
                   package_version, config_hash, prompt_version, prompt_hash,
                   rule_snapshot_id, rule_set_hash, secret_engine_version,
                   memory_snapshot_id, idempotency_key, started_at, finished_at,
                   agent_prompt_version, agent_prompt_hash,
                   reflection_prompt_version, reflection_prompt_hash, resume_count
            FROM review_runs
            WHERE idempotency_key = ? AND status = 'success'
            """,
            (key,),
        ).fetchone()
        if row is None:
            return None
        return row_to_run(row)

    def acquire_resume(
        self,
        run_id: RunId,
        *,
        lease_identity: RunLeaseIdentity | None = None,
        lease_checked_at: datetime | None = None,
    ) -> bool:
        """再開可能状態またはリース取得済みの中断実行を実行中へ変更する."""
        lease_owner = lease_identity.owner if lease_identity is not None else None
        fencing_token = lease_identity.fencing_token if lease_identity is not None else None
        checked_at = lease_checked_at.isoformat() if lease_checked_at is not None else None
        cursor = self._connection.execute(
            """
            UPDATE review_runs
            SET status = 'running', finished_at = NULL, resume_count = resume_count + 1
            WHERE run_id = ? AND (
                status IN ('partial-success', 'failed')
                OR (
                    status = 'running'
                    AND ? IS NOT NULL
                    AND ? IS NOT NULL
                    AND EXISTS (
                        SELECT 1 FROM run_leases
                        WHERE run_leases.run_id = review_runs.run_id
                          AND lease_owner = ?
                          AND fencing_token = ?
                          AND lease_expires_at > ?
                    )
                )
            )
            """,
            (
                str(run_id),
                lease_owner,
                checked_at,
                lease_owner,
                fencing_token,
                checked_at,
            ),
        )
        return cursor.rowcount == 1
