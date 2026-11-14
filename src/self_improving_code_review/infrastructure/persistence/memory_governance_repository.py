"""M5 メモリ統制を原子的に保存する SQLite アダプター."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from self_improving_code_review.application.ports.memory_governance import (
    MemoryGovernanceRepository,
)
from self_improving_code_review.domain.common.enums import (
    ConflictResolution,
    ConflictStatus,
    MemoryStatus,
    RevalidationStatus,
)
from self_improving_code_review.domain.common.errors import (
    ConcurrencyConflictError,
    DatabaseError,
    InvalidInputError,
)
from self_improving_code_review.domain.common.hashing import hash_lines
from self_improving_code_review.domain.common.values import (
    MemoryId,
    MemoryVersion,
    ProjectId,
)
from self_improving_code_review.domain.governance.memory_models import (
    MemoryConflict,
    MemoryGovernanceResult,
    MemoryRevalidation,
)
from self_improving_code_review.domain.memory.conflicts import ConflictCandidate, find_conflicts
from self_improving_code_review.domain.memory.models import (
    MemoryContent,
    MemoryRecord,
    MemoryTransition,
)
from self_improving_code_review.domain.memory.scope import MemoryScope
from self_improving_code_review.infrastructure.persistence.memory_repository import (
    SqliteMemoryRepository,
)
from self_improving_code_review.infrastructure.persistence.sqlite import initialize_database
from self_improving_code_review.messages import Messages

__all__ = ["SqliteMemoryGovernanceRepository"]


def _raise_invalid_state() -> None:
    """現在状態では許可されない操作を失敗させる."""
    raise InvalidInputError(Messages.MEMORY_GOVERNANCE_INVALID_STATE)


def _raise_conflict_concurrent() -> None:
    """競合行の楽観ロック不一致を失敗させる."""
    raise ConcurrencyConflictError(Messages.MEMORY_CONFLICT_MODIFIED_CONCURRENTLY)


class SqliteMemoryGovernanceRepository(MemoryGovernanceRepository):
    """接続を操作ごとに閉じる M5 メモリ統制リポジトリ."""

    def __init__(self, database_path: Path, *, busy_timeout_ms: int = 5000) -> None:
        """データベースを初期化して接続設定を保持する."""
        initialize_database(database_path)
        self._database_path = database_path
        self._busy_timeout_ms = busy_timeout_ms

    def record_detected_conflicts(
        self,
        project_id: ProjectId,
        memory_id: MemoryId,
        *,
        detected_at: datetime,
    ) -> tuple[MemoryConflict, ...]:
        """競合状態の候補と有効メモリの競合スナップショットを冪等に保存する."""
        try:
            with closing(self._connection()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                record, candidate = self._require_memory(connection, project_id, memory_id)
                if record.current_status is not MemoryStatus.CONFLICT:
                    _raise_invalid_state()
                active = SqliteMemoryRepository(connection).list_by_status(
                    project_id, MemoryStatus.ACTIVE
                )
                conflicts = find_conflicts(candidate, tuple(item[1] for item in active))
                self._insert_conflicts(connection, project_id, candidate, conflicts, detected_at)
                rows = connection.execute(
                    "SELECT conflict_id, project_id, left_memory_id, left_version, "
                    "left_content_hash, right_memory_id, right_version, right_content_hash, "
                    "reason_code, status, lock_version, detected_at, resolution, resolved_at "
                    "FROM memory_conflicts WHERE project_id = ? AND status = 'open' "
                    "AND (left_memory_id = ? OR right_memory_id = ?) ORDER BY conflict_id",
                    (str(project_id), str(memory_id), str(memory_id)),
                ).fetchall()
                return tuple(self._conflict_from_row(row) for row in rows)
        except InvalidInputError, ConcurrencyConflictError:
            raise
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.MEMORY_GOVERNANCE_PERSIST_FAILED) from exc

    def archive(
        self,
        project_id: ProjectId,
        memory_id: MemoryId,
        *,
        expected_lock_version: int,
        actor_id: str,
        reason_hash: str,
        occurred_at: datetime,
    ) -> MemoryGovernanceResult:
        """有効メモリを保管する."""
        return self._change_single_status(
            project_id,
            memory_id,
            expected_lock_version=expected_lock_version,
            expected_status=MemoryStatus.ACTIVE,
            target_status=MemoryStatus.ARCHIVED,
            actor_id=actor_id,
            reason_hash=reason_hash,
            occurred_at=occurred_at,
        )

    def restore(
        self,
        project_id: ProjectId,
        memory_id: MemoryId,
        *,
        expected_lock_version: int,
        actor_id: str,
        reason_hash: str,
        occurred_at: datetime,
    ) -> MemoryGovernanceResult:
        """保管メモリを複製した次版として承認待ちへ戻す."""
        try:
            with closing(self._connection()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                record, content = self._require_memory(connection, project_id, memory_id)
                self._require_state(record, MemoryStatus.ARCHIVED, expected_lock_version)
                restored = replace(
                    content,
                    version=content.version.next(),
                    created_by=actor_id,
                    created_at=occurred_at,
                    valid_from=None,
                    review_after=None,
                )
                transition = self._transition(
                    record,
                    restored.version,
                    MemoryStatus.PENDING,
                    actor_id,
                    reason_hash,
                    occurred_at,
                )
                SqliteMemoryRepository(connection).add_version_and_change_status(
                    memory_id, restored, transition, expected_lock_version
                )
                self._insert_relation(
                    connection,
                    memory_id,
                    content.version,
                    memory_id,
                    restored.version,
                    "restored-from",
                    actor_id,
                    reason_hash,
                    occurred_at,
                )
                return self._result(
                    memory_id,
                    restored.version,
                    MemoryStatus.PENDING,
                    expected_lock_version + 2,
                    restored.content_hash,
                )
        except InvalidInputError, ConcurrencyConflictError:
            raise
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.MEMORY_GOVERNANCE_PERSIST_FAILED) from exc

    def supersede(
        self,
        project_id: ProjectId,
        memory_id: MemoryId,
        replacement_id: MemoryId,
        *,
        expected_lock_version: int,
        actor_id: str,
        reason_hash: str,
        occurred_at: datetime,
    ) -> MemoryGovernanceResult:
        """有効メモリを同じプロジェクトの有効メモリで置換する."""
        if memory_id == replacement_id:
            raise InvalidInputError(Messages.MEMORY_GOVERNANCE_INVALID_STATE)
        try:
            with closing(self._connection()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                record, content = self._require_memory(connection, project_id, memory_id)
                replacement_record, replacement = self._require_memory(
                    connection, project_id, replacement_id
                )
                self._require_state(record, MemoryStatus.ACTIVE, expected_lock_version)
                if replacement_record.current_status is not MemoryStatus.ACTIVE:
                    _raise_invalid_state()
                transition = self._transition(
                    record,
                    content.version,
                    MemoryStatus.SUPERSEDED,
                    actor_id,
                    reason_hash,
                    occurred_at,
                )
                SqliteMemoryRepository(connection).change_status(transition, expected_lock_version)
                self._insert_relation(
                    connection,
                    memory_id,
                    content.version,
                    replacement_id,
                    replacement.version,
                    "supersedes",
                    actor_id,
                    reason_hash,
                    occurred_at,
                )
                return self._result(
                    memory_id,
                    content.version,
                    MemoryStatus.SUPERSEDED,
                    expected_lock_version + 1,
                    content.content_hash,
                )
        except InvalidInputError, ConcurrencyConflictError:
            raise
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.MEMORY_GOVERNANCE_PERSIST_FAILED) from exc

    def list_conflicts(
        self,
        project_id: ProjectId,
        status: ConflictStatus,
    ) -> tuple[MemoryConflict, ...]:
        """プロジェクト内の競合を識別子順で返す."""
        try:
            with closing(self._connection()) as connection:
                rows = connection.execute(
                    "SELECT conflict_id, project_id, left_memory_id, left_version, "
                    "left_content_hash, right_memory_id, right_version, right_content_hash, "
                    "reason_code, status, lock_version, detected_at, resolution, resolved_at "
                    "FROM memory_conflicts WHERE project_id = ? AND status = ? "
                    "ORDER BY conflict_id",
                    (str(project_id), status.value),
                ).fetchall()
            return tuple(self._conflict_from_row(row) for row in rows)
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.MEMORY_GOVERNANCE_PERSIST_FAILED) from exc

    def resolve_conflict(
        self,
        project_id: ProjectId,
        conflict_id: str,
        resolution: ConflictResolution,
        *,
        expected_lock_version: int,
        actor_id: str,
        reason_hash: str,
        evidence_hash: str,
        occurred_at: datetime,
        narrowed_scope: MemoryScope | None,
    ) -> tuple[MemoryConflict, MemoryGovernanceResult]:
        """選択した競合解決を関連状態と同一トランザクションで保存する."""
        try:
            with closing(self._connection()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                conflict = self._require_conflict(connection, project_id, conflict_id)
                if (
                    conflict.status is not ConflictStatus.OPEN
                    or conflict.lock_version != expected_lock_version
                ):
                    _raise_conflict_concurrent()
                result = self._apply_resolution(
                    connection,
                    conflict,
                    resolution,
                    actor_id,
                    reason_hash,
                    occurred_at,
                    narrowed_scope,
                )
                connection.execute(
                    "UPDATE memory_conflicts SET status = 'resolved', resolution = ?, "
                    "resolved_by = ?, reason_hash = ?, evidence_hash = ?, resolved_at = ?, "
                    "lock_version = lock_version + 1 "
                    "WHERE conflict_id = ? AND lock_version = ? AND status = 'open'",
                    (
                        resolution.value,
                        actor_id,
                        reason_hash,
                        evidence_hash,
                        occurred_at.isoformat(),
                        conflict_id,
                        expected_lock_version,
                    ),
                )
                resolved = replace(
                    conflict,
                    status=ConflictStatus.RESOLVED,
                    resolution=resolution,
                    resolved_at=occurred_at,
                    lock_version=expected_lock_version + 1,
                )
                return resolved, result
        except InvalidInputError, ConcurrencyConflictError:
            raise
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.MEMORY_GOVERNANCE_PERSIST_FAILED) from exc

    def list_due(
        self,
        project_id: ProjectId,
        now: datetime,
    ) -> tuple[MemoryRevalidation, ...]:
        """期限に達した有効版を列挙し、期限到来記録を冪等に固定する."""
        try:
            with closing(self._connection()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    "SELECT memories.memory_id, memories.current_version, "
                    "memory_versions.review_after FROM memories "
                    "JOIN memory_versions ON memory_versions.memory_id = memories.memory_id "
                    "AND memory_versions.version = memories.current_version "
                    "WHERE memories.project_id = ? AND memories.current_status = 'active' "
                    "AND memory_versions.review_after <= ? ORDER BY memories.memory_id",
                    (str(project_id), now.isoformat()),
                ).fetchall()
                results = []
                for memory_value, version_value, due_value in rows:
                    due_at = datetime.fromisoformat(str(due_value))
                    revalidation_id = hash_lines(
                        (str(project_id), str(memory_value), str(version_value), "due")
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO memory_revalidations("
                        "revalidation_id, project_id, memory_id, source_version, status, "
                        "due_at, created_at) VALUES(?, ?, ?, ?, 'due', ?, ?)",
                        (
                            revalidation_id,
                            str(project_id),
                            str(memory_value),
                            int(str(version_value)),
                            due_at.isoformat(),
                            now.isoformat(),
                        ),
                    )
                    results.append(
                        MemoryRevalidation(
                            revalidation_id=revalidation_id,
                            project_id=project_id,
                            memory_id=MemoryId(str(memory_value)),
                            source_version=MemoryVersion(int(str(version_value))),
                            status=RevalidationStatus.DUE,
                            due_at=due_at,
                            created_at=now,
                        )
                    )
                return tuple(results)
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.MEMORY_GOVERNANCE_PERSIST_FAILED) from exc

    def confirm_revalidation(
        self,
        project_id: ProjectId,
        memory_id: MemoryId,
        *,
        expected_lock_version: int,
        actor_id: str,
        reason_hash: str,
        evidence_hash: str,
        occurred_at: datetime,
        next_review_after: datetime,
    ) -> MemoryGovernanceResult:
        """同一内容の次版を有効状態のまま作成して期限を更新する."""
        if next_review_after <= occurred_at:
            raise InvalidInputError(Messages.MEMORY_NEXT_REVIEW_NOT_LATER)
        try:
            with closing(self._connection()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                record, content = self._require_memory(connection, project_id, memory_id)
                self._require_state(record, MemoryStatus.ACTIVE, expected_lock_version)
                confirmed = replace(
                    content,
                    version=content.version.next(),
                    created_by=actor_id,
                    created_at=occurred_at,
                    valid_from=occurred_at,
                    review_after=next_review_after,
                )
                SqliteMemoryRepository(connection).add_version(
                    memory_id, confirmed, expected_lock_version
                )
                connection.execute(
                    "UPDATE memories SET last_confirmed_at = ? WHERE memory_id = ?",
                    (occurred_at.isoformat(), str(memory_id)),
                )
                self._insert_revalidation(
                    connection,
                    project_id,
                    memory_id,
                    content.version,
                    confirmed.version,
                    RevalidationStatus.CONFIRMED,
                    content.review_after or occurred_at,
                    next_review_after,
                    actor_id,
                    reason_hash,
                    evidence_hash,
                    occurred_at,
                )
                return self._result(
                    memory_id,
                    confirmed.version,
                    MemoryStatus.ACTIVE,
                    expected_lock_version + 1,
                    confirmed.content_hash,
                )
        except InvalidInputError, ConcurrencyConflictError:
            raise
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.MEMORY_GOVERNANCE_PERSIST_FAILED) from exc

    def invalidate_revalidation(
        self,
        project_id: ProjectId,
        memory_id: MemoryId,
        *,
        expected_lock_version: int,
        actor_id: str,
        reason_hash: str,
        evidence_hash: str,
        occurred_at: datetime,
    ) -> MemoryGovernanceResult:
        """再検証で無効と判定した現版を保管する."""
        try:
            with closing(self._connection()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                record, content = self._require_memory(connection, project_id, memory_id)
                self._require_state(record, MemoryStatus.ACTIVE, expected_lock_version)
                transition = self._transition(
                    record,
                    content.version,
                    MemoryStatus.ARCHIVED,
                    actor_id,
                    reason_hash,
                    occurred_at,
                )
                SqliteMemoryRepository(connection).change_status(transition, expected_lock_version)
                self._insert_revalidation(
                    connection,
                    project_id,
                    memory_id,
                    content.version,
                    None,
                    RevalidationStatus.INVALIDATED,
                    content.review_after or occurred_at,
                    None,
                    actor_id,
                    reason_hash,
                    evidence_hash,
                    occurred_at,
                )
                return self._result(
                    memory_id,
                    content.version,
                    MemoryStatus.ARCHIVED,
                    expected_lock_version + 1,
                    content.content_hash,
                )
        except InvalidInputError, ConcurrencyConflictError:
            raise
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.MEMORY_GOVERNANCE_PERSIST_FAILED) from exc

    def _change_single_status(
        self,
        project_id: ProjectId,
        memory_id: MemoryId,
        *,
        expected_lock_version: int,
        expected_status: MemoryStatus,
        target_status: MemoryStatus,
        actor_id: str,
        reason_hash: str,
        occurred_at: datetime,
    ) -> MemoryGovernanceResult:
        """単一メモリの状態を短いトランザクションで変更する."""
        try:
            with closing(self._connection()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                record, content = self._require_memory(connection, project_id, memory_id)
                self._require_state(record, expected_status, expected_lock_version)
                transition = self._transition(
                    record, content.version, target_status, actor_id, reason_hash, occurred_at
                )
                SqliteMemoryRepository(connection).change_status(transition, expected_lock_version)
                return self._result(
                    memory_id,
                    content.version,
                    target_status,
                    expected_lock_version + 1,
                    content.content_hash,
                )
        except InvalidInputError, ConcurrencyConflictError:
            raise
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.MEMORY_GOVERNANCE_PERSIST_FAILED) from exc

    def _apply_resolution(
        self,
        connection: sqlite3.Connection,
        conflict: MemoryConflict,
        resolution: ConflictResolution,
        actor_id: str,
        reason_hash: str,
        occurred_at: datetime,
        narrowed_scope: MemoryScope | None,
    ) -> MemoryGovernanceResult:
        """競合解決方法に対応するメモリ状態変更を適用する."""
        left_record, left = self._require_memory(
            connection, conflict.project_id, conflict.left_memory_id
        )
        right_record, right = self._require_memory(
            connection, conflict.project_id, conflict.right_memory_id
        )
        if resolution is ConflictResolution.NARROW_SCOPE:
            if narrowed_scope is None:
                raise InvalidInputError(Messages.MEMORY_NARROW_SCOPE_REQUIRED)
            candidate_record, candidate = self._candidate_pair(
                left_record, left, right_record, right
            )
            narrowed = replace(
                candidate,
                version=candidate.version.next(),
                scope=narrowed_scope,
                created_by=actor_id,
                created_at=occurred_at,
            )
            active = SqliteMemoryRepository(connection).list_by_status(
                conflict.project_id, MemoryStatus.ACTIVE
            )
            remaining = find_conflicts(narrowed, tuple(item[1] for item in active))
            target = MemoryStatus.CONFLICT if remaining else MemoryStatus.ACTIVE
            transition = self._transition(
                candidate_record, narrowed.version, target, actor_id, reason_hash, occurred_at
            )
            SqliteMemoryRepository(connection).add_version_and_change_status(
                candidate.memory_id,
                narrowed,
                transition,
                candidate_record.lock_version,
            )
            self._insert_conflicts(
                connection, conflict.project_id, narrowed, remaining, occurred_at
            )
            return self._result(
                candidate.memory_id,
                narrowed.version,
                target,
                candidate_record.lock_version + 2,
                narrowed.content_hash,
            )
        if narrowed_scope is not None:
            raise InvalidInputError(Messages.MEMORY_SCOPE_UNEXPECTED)
        if resolution is ConflictResolution.REJECT_CANDIDATE:
            candidate_record, candidate = self._candidate_pair(
                left_record, left, right_record, right
            )
            transition = self._transition(
                candidate_record,
                candidate.version,
                MemoryStatus.REJECTED,
                actor_id,
                reason_hash,
                occurred_at,
            )
            SqliteMemoryRepository(connection).change_status(
                transition, candidate_record.lock_version
            )
            return self._result(
                candidate.memory_id,
                candidate.version,
                MemoryStatus.REJECTED,
                candidate_record.lock_version + 1,
                candidate.content_hash,
            )

        loser_record, loser, winner_record, winner = (
            (left_record, left, right_record, right)
            if resolution is ConflictResolution.SUPERSEDE_LEFT
            else (right_record, right, left_record, left)
        )
        if (
            loser_record.current_status is not MemoryStatus.ACTIVE
            or winner_record.current_status is not MemoryStatus.CONFLICT
        ):
            _raise_invalid_state()
        loser_transition = self._transition(
            loser_record,
            loser.version,
            MemoryStatus.SUPERSEDED,
            actor_id,
            reason_hash,
            occurred_at,
        )
        winner_transition = self._transition(
            winner_record,
            winner.version,
            MemoryStatus.ACTIVE,
            actor_id,
            reason_hash,
            occurred_at,
        )
        memory_repository = SqliteMemoryRepository(connection)
        memory_repository.change_status(loser_transition, loser_record.lock_version)
        memory_repository.change_status(winner_transition, winner_record.lock_version)
        self._insert_relation(
            connection,
            loser.memory_id,
            loser.version,
            winner.memory_id,
            winner.version,
            "supersedes",
            actor_id,
            reason_hash,
            occurred_at,
        )
        return self._result(
            winner.memory_id,
            winner.version,
            MemoryStatus.ACTIVE,
            winner_record.lock_version + 1,
            winner.content_hash,
        )

    @staticmethod
    def _candidate_pair(
        left_record: MemoryRecord,
        left: MemoryContent,
        right_record: MemoryRecord,
        right: MemoryContent,
    ) -> tuple[MemoryRecord, MemoryContent]:
        """左右から競合状態の候補を一意に返す."""
        pairs = tuple(
            pair
            for pair in ((left_record, left), (right_record, right))
            if pair[0].current_status is MemoryStatus.CONFLICT
        )
        if len(pairs) != 1:
            raise InvalidInputError(Messages.MEMORY_GOVERNANCE_INVALID_STATE)
        return pairs[0]

    @staticmethod
    def _require_state(
        record: MemoryRecord,
        expected_status: MemoryStatus,
        expected_lock_version: int,
    ) -> None:
        """状態と楽観ロック版を検証する."""
        if record.lock_version != expected_lock_version:
            raise ConcurrencyConflictError(Messages.MEMORY_MODIFIED_CONCURRENTLY)
        if record.current_status is not expected_status:
            raise InvalidInputError(Messages.MEMORY_GOVERNANCE_INVALID_STATE)

    @staticmethod
    def _transition(
        record: MemoryRecord,
        version: MemoryVersion,
        target: MemoryStatus,
        actor_id: str,
        reason_hash: str,
        occurred_at: datetime,
    ) -> MemoryTransition:
        """ハッシュ化済み理由を持つ状態遷移を作成する."""
        return MemoryTransition(
            memory_id=record.memory_id,
            memory_version=version,
            from_status=record.current_status,
            to_status=target,
            actor_id=actor_id,
            reason=reason_hash,
            created_at=occurred_at,
        )

    @staticmethod
    def _result(
        memory_id: MemoryId,
        version: MemoryVersion,
        status: MemoryStatus,
        lock_version: int,
        content_hash: str,
    ) -> MemoryGovernanceResult:
        """永続化結果を安全な公開モデルへ変換する."""
        return MemoryGovernanceResult(
            memory_id=memory_id,
            version=version,
            status=status,
            lock_version=lock_version,
            content_hash=content_hash,
        )

    @staticmethod
    def _require_memory(
        connection: sqlite3.Connection,
        project_id: ProjectId,
        memory_id: MemoryId,
    ) -> tuple[MemoryRecord, MemoryContent]:
        """同じプロジェクトに属するメモリだけを返す."""
        found = SqliteMemoryRepository(connection).find(memory_id)
        if found is None or found[0].project_id != project_id:
            raise InvalidInputError(Messages.MEMORY_GOVERNANCE_TARGET_NOT_FOUND)
        return found

    @staticmethod
    def _insert_relation(
        connection: sqlite3.Connection,
        source_id: MemoryId,
        source_version: MemoryVersion,
        target_id: MemoryId,
        target_version: MemoryVersion,
        relation: str,
        actor_id: str,
        reason_hash: str,
        occurred_at: datetime,
    ) -> None:
        """不変のメモリ関係を一件保存する."""
        connection.execute(
            "INSERT OR IGNORE INTO memory_relations("
            "source_memory_id, source_version, target_memory_id, target_version, "
            "relation, actor_id, reason_hash, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(source_id),
                source_version.value,
                str(target_id),
                target_version.value,
                relation,
                actor_id,
                reason_hash,
                occurred_at.isoformat(),
            ),
        )

    @staticmethod
    def _insert_conflicts(
        connection: sqlite3.Connection,
        project_id: ProjectId,
        candidate: MemoryContent,
        conflicts: tuple[ConflictCandidate, ...],
        occurred_at: datetime,
    ) -> None:
        """正規化した競合候補を冪等に保存する."""
        for item in conflicts:
            left, right = sorted(
                (candidate, item.existing), key=lambda content: content.memory_id.value
            )
            conflict_id = hash_lines(
                (
                    str(project_id),
                    str(left.memory_id),
                    str(left.version),
                    str(right.memory_id),
                    str(right.version),
                    item.reason_code,
                )
            )
            connection.execute(
                "INSERT OR IGNORE INTO memory_conflicts("
                "conflict_id, project_id, left_memory_id, left_version, left_content_hash, "
                "right_memory_id, right_version, right_content_hash, reason_code, status, "
                "detected_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
                (
                    conflict_id,
                    str(project_id),
                    str(left.memory_id),
                    left.version.value,
                    left.content_hash,
                    str(right.memory_id),
                    right.version.value,
                    right.content_hash,
                    item.reason_code,
                    occurred_at.isoformat(),
                ),
            )

    @staticmethod
    def _insert_revalidation(
        connection: sqlite3.Connection,
        project_id: ProjectId,
        memory_id: MemoryId,
        source_version: MemoryVersion,
        result_version: MemoryVersion | None,
        status: RevalidationStatus,
        due_at: datetime,
        next_review_after: datetime | None,
        actor_id: str,
        reason_hash: str,
        evidence_hash: str,
        occurred_at: datetime,
    ) -> None:
        """再検証結果を決定的な識別子で保存する."""
        revalidation_id = hash_lines(
            (
                str(project_id),
                str(memory_id),
                str(source_version),
                status.value,
                next_review_after.isoformat() if next_review_after is not None else "",
                actor_id,
            )
        )
        connection.execute(
            "INSERT OR IGNORE INTO memory_revalidations("
            "revalidation_id, project_id, memory_id, source_version, result_version, "
            "status, due_at, next_review_after, actor_id, reason_hash, evidence_hash, "
            "created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revalidation_id,
                str(project_id),
                str(memory_id),
                source_version.value,
                result_version.value if result_version is not None else None,
                status.value,
                due_at.isoformat(),
                next_review_after.isoformat() if next_review_after is not None else None,
                actor_id,
                reason_hash,
                evidence_hash,
                occurred_at.isoformat(),
            ),
        )

    def _require_conflict(
        self,
        connection: sqlite3.Connection,
        project_id: ProjectId,
        conflict_id: str,
    ) -> MemoryConflict:
        """同じプロジェクトの競合を返す."""
        row = connection.execute(
            "SELECT conflict_id, project_id, left_memory_id, left_version, "
            "left_content_hash, right_memory_id, right_version, right_content_hash, "
            "reason_code, status, lock_version, detected_at, resolution, resolved_at "
            "FROM memory_conflicts WHERE conflict_id = ? AND project_id = ?",
            (conflict_id, str(project_id)),
        ).fetchone()
        if row is None:
            raise InvalidInputError(Messages.MEMORY_CONFLICT_NOT_FOUND)
        return self._conflict_from_row(row)

    @staticmethod
    def _conflict_from_row(row: tuple[object, ...]) -> MemoryConflict:
        """SQLite 行を競合モデルへ変換する."""
        return MemoryConflict(
            conflict_id=str(row[0]),
            project_id=ProjectId(str(row[1])),
            left_memory_id=MemoryId(str(row[2])),
            left_version=MemoryVersion(int(str(row[3]))),
            left_content_hash=str(row[4]),
            right_memory_id=MemoryId(str(row[5])),
            right_version=MemoryVersion(int(str(row[6]))),
            right_content_hash=str(row[7]),
            reason_code=str(row[8]),
            status=ConflictStatus(str(row[9])),
            lock_version=int(str(row[10])),
            detected_at=datetime.fromisoformat(str(row[11])),
            resolution=ConflictResolution(str(row[12])) if row[12] is not None else None,
            resolved_at=datetime.fromisoformat(str(row[13])) if row[13] is not None else None,
        )

    def _connection(self) -> sqlite3.Connection:
        """外部キーとビジー待機を設定した短命接続を返す."""
        connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        return connection
