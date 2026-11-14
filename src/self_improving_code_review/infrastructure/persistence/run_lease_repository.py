"""SQLite の短時間トランザクションで実行リースを管理する."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from pathlib import Path
from time import sleep
from typing import TypeVar

from self_improving_code_review.application.ports.run_lease import RunLeaseIdentity
from self_improving_code_review.domain.common.errors import DatabaseError
from self_improving_code_review.domain.common.values import RunId
from self_improving_code_review.infrastructure.persistence.sqlite import initialize_database
from self_improving_code_review.messages import Messages

__all__ = ["SqliteRunLeaseRepository"]

_Result = TypeVar("_Result")
_RETRY_EXHAUSTED = "run lease retry loop exhausted"


class SqliteRunLeaseRepository:
    """接続を共有せず、条件付き単一 SQL で実行リースを更新する."""

    def __init__(
        self,
        database_path: Path,
        *,
        busy_timeout_ms: int = 5000,
        max_locked_retries: int = 2,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        """DB パス、ロック待機上限および試行境界を保持する."""
        initialize_database(database_path, busy_timeout_ms=busy_timeout_ms)
        self._database_path = database_path
        self._busy_timeout_ms = busy_timeout_ms
        self._max_locked_retries = max_locked_retries
        self._sleeper = sleeper

    def acquire(
        self,
        run_id: RunId,
        owner: str,
        now: datetime,
        expires_at: datetime,
    ) -> RunLeaseIdentity | None:
        """空きまたは期限切れの実行リースを取得し、囲い込み番号を増やす."""

        def operation(connection: sqlite3.Connection) -> RunLeaseIdentity | None:
            """取得条件付き SQL を一つの短時間トランザクションで実行する."""
            row = connection.execute(
                """
                INSERT INTO run_leases(
                    run_id, lease_owner, lease_expires_at, heartbeat_at, fencing_token
                ) VALUES(?,?,?,?,1)
                ON CONFLICT(run_id) DO UPDATE SET
                    lease_owner = excluded.lease_owner,
                    lease_expires_at = excluded.lease_expires_at,
                    heartbeat_at = excluded.heartbeat_at,
                    fencing_token = run_leases.fencing_token + 1
                WHERE run_leases.lease_expires_at <= excluded.heartbeat_at
                RETURNING fencing_token
                """,
                (str(run_id), owner, expires_at.isoformat(), now.isoformat()),
            ).fetchone()
            if row is None:
                return None
            return RunLeaseIdentity(run_id, owner, int(row[0]))

        return self._execute(operation)

    def heartbeat(
        self,
        identity: RunLeaseIdentity,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        """所有者、囲い込み番号、現在期限が有効な行だけを延長する."""

        def operation(connection: sqlite3.Connection) -> bool:
            """現在も所有中のリースだけを条件付きで延長する."""
            cursor = connection.execute(
                """
                UPDATE run_leases
                SET lease_expires_at = ?, heartbeat_at = ?
                WHERE run_id = ? AND lease_owner = ? AND fencing_token = ?
                  AND lease_expires_at > ?
                """,
                (
                    expires_at.isoformat(),
                    now.isoformat(),
                    str(identity.run_id),
                    identity.owner,
                    identity.fencing_token,
                    now.isoformat(),
                ),
            )
            return cursor.rowcount == 1

        return self._execute(operation)

    def is_owned(self, identity: RunLeaseIdentity, now: datetime) -> bool:
        """所有者、囲い込み番号および期限がすべて有効か確認する."""

        def operation(connection: sqlite3.Connection) -> bool:
            """対象時刻で有効な所有権が一件存在するか確認する."""
            row = connection.execute(
                """
                SELECT 1 FROM run_leases
                WHERE run_id = ? AND lease_owner = ? AND fencing_token = ?
                  AND lease_expires_at > ?
                """,
                (
                    str(identity.run_id),
                    identity.owner,
                    identity.fencing_token,
                    now.isoformat(),
                ),
            ).fetchone()
            return row is not None

        return self._execute(operation)

    def release(self, identity: RunLeaseIdentity) -> None:
        """古い実行者が新所有者の行を消さない条件でリースを解放する."""

        def operation(connection: sqlite3.Connection) -> None:
            """所有者と囲い込み番号が一致するリースだけを削除する."""
            connection.execute(
                """
                DELETE FROM run_leases
                WHERE run_id = ? AND lease_owner = ? AND fencing_token = ?
                """,
                (str(identity.run_id), identity.owner, identity.fencing_token),
            )

        self._execute(operation)

    def _execute(self, operation: Callable[[sqlite3.Connection], _Result]) -> _Result:
        """SQLite ロックだけを上限内で再試行し、他の障害を変換する."""
        for attempt in range(self._max_locked_retries + 1):
            try:
                with closing(
                    sqlite3.connect(
                        self._database_path,
                        timeout=self._busy_timeout_ms / 1000,
                    )
                ) as connection:
                    connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
                    connection.execute("PRAGMA foreign_keys = ON")
                    connection.execute("PRAGMA journal_mode = WAL")
                    result = operation(connection)
                    connection.commit()
                    return result
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt >= self._max_locked_retries:
                    raise DatabaseError(Messages.RUN_LEASE_UPDATE_FAILED) from exc
                self._sleeper(0.01 * (attempt + 1))
            except sqlite3.Error as exc:
                raise DatabaseError(Messages.RUN_LEASE_UPDATE_FAILED) from exc
        raise AssertionError(_RETRY_EXHAUSTED)
