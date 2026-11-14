"""SQLite接続、共有倉庫の構成と短時間トランザクションを管理する."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import TracebackType
from typing import cast

from self_improving_code_review.application.ports.unit_of_work import UnitOfWork
from self_improving_code_review.domain.common.errors import (
    DatabaseError,
)
from self_improving_code_review.infrastructure.persistence.bundle_repository import (
    SqliteBundleRepository,
)
from self_improving_code_review.infrastructure.persistence.extraction_repository import (
    SqliteExtractionRepository,
)
from self_improving_code_review.infrastructure.persistence.feedback_repository import (
    SqliteFeedbackRepository,
)
from self_improving_code_review.infrastructure.persistence.finding_repository import (
    SqliteFindingRepository,
)
from self_improving_code_review.infrastructure.persistence.llm_call_repository import (
    SqliteLlmCallRepository,
)
from self_improving_code_review.infrastructure.persistence.m3_audit_repository import (
    SqliteM3AuditRepository,
)
from self_improving_code_review.infrastructure.persistence.memory_repository import (
    SqliteMemoryRepository,
)
from self_improving_code_review.infrastructure.persistence.review_result_repository import (
    SqliteReviewResultRepository,
)
from self_improving_code_review.infrastructure.persistence.reviewer_audit_repository import (
    SqliteReviewerAuditRepository,
)
from self_improving_code_review.infrastructure.persistence.rule_snapshot_repository import (
    SqliteRuleSnapshotRepository,
)
from self_improving_code_review.infrastructure.persistence.run_repository import SqliteRunRepository
from self_improving_code_review.infrastructure.persistence.snapshot_repository import (
    SqliteMemorySnapshotRepository,
)
from self_improving_code_review.infrastructure.persistence.sqlite_initialization import (
    initialize_database,
)
from self_improving_code_review.messages import Messages

__all__ = [
    "SqliteUnitOfWork",
    "SqliteUnitOfWorkFactory",
    "initialize_database",
]


class SqliteUnitOfWork:
    """すべてのリポジトリを含む短時間の SQLite トランザクション."""

    runs: SqliteRunRepository
    bundles: SqliteBundleRepository
    findings: SqliteFindingRepository
    rule_snapshots: SqliteRuleSnapshotRepository
    llm_calls: SqliteLlmCallRepository
    results: SqliteReviewResultRepository
    feedback: SqliteFeedbackRepository
    extractions: SqliteExtractionRepository
    memories: SqliteMemoryRepository
    memory_snapshots: SqliteMemorySnapshotRepository
    m3_audit: SqliteM3AuditRepository

    def __init__(self, database_path: Path, *, busy_timeout_ms: int = 5000) -> None:
        """トランザクション開始までデータベースパスを保持する."""
        self._database_path = database_path
        self._busy_timeout_ms = busy_timeout_ms
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> SqliteUnitOfWork:
        """接続を開いてトランザクションを開始する."""
        try:
            connection = sqlite3.connect(self._database_path)
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(f"PRAGMA busy_timeout = {int(self._busy_timeout_ms)}")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN")
        except sqlite3.Error as exc:
            message = Messages.TRANSACTION_OPEN_FAILED
            raise DatabaseError(message) from exc
        self._connection = connection
        self.runs = SqliteRunRepository(connection)
        self.bundles = SqliteBundleRepository(connection)
        self.findings = SqliteFindingRepository(connection)
        self.rule_snapshots = SqliteRuleSnapshotRepository(connection)
        self.llm_calls = SqliteLlmCallRepository(connection)
        self.results = SqliteReviewResultRepository(connection)
        self.m3_audit = SqliteM3AuditRepository(connection)
        self.feedback = SqliteFeedbackRepository(connection)
        self.extractions = SqliteExtractionRepository(connection)
        self.memories = SqliteMemoryRepository(connection)
        self.memory_snapshots = SqliteMemorySnapshotRepository(connection)
        self.reviewer_audit = SqliteReviewerAuditRepository(connection)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """成功時はコミットし、失敗時はロールバックして閉じる."""
        connection = self._connection
        if connection is None:
            message = Messages.UNIT_OF_WORK_NOT_ACTIVE
            raise DatabaseError(message)
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self._connection = None
        connection.close()

    def commit(self) -> None:
        """実行中のトランザクションをコミットする."""
        if self._connection is None:
            message = Messages.UNIT_OF_WORK_NOT_ACTIVE
            raise DatabaseError(message)
        self._connection.commit()

    def rollback(self) -> None:
        """実行中のトランザクションをロールバックする."""
        if self._connection is not None:
            self._connection.rollback()


class SqliteUnitOfWorkFactory:
    """初期化済みの SQLite 作業単位を作成する."""

    def __init__(self, database_path: Path, *, busy_timeout_ms: int = 5000) -> None:
        """スキーマを初期化してパスを保持する."""
        initialize_database(database_path, busy_timeout_ms=busy_timeout_ms)
        self._database_path = database_path
        self._busy_timeout_ms = busy_timeout_ms

    def __call__(self) -> UnitOfWork:
        """新しい作業単位を返す."""
        return cast(
            UnitOfWork, SqliteUnitOfWork(self._database_path, busy_timeout_ms=self._busy_timeout_ms)
        )
