"""M5 メモリ統制永続化ポート."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from self_improving_code_review.domain.common.enums import (
    ConflictResolution,
    ConflictStatus,
)
from self_improving_code_review.domain.common.values import MemoryId, ProjectId
from self_improving_code_review.domain.governance.memory_models import (
    MemoryConflict,
    MemoryGovernanceResult,
    MemoryRevalidation,
)
from self_improving_code_review.domain.memory.scope import MemoryScope

__all__ = ["MemoryGovernanceRepository"]


class MemoryGovernanceRepository(Protocol):
    """メモリ統制操作を短い原子トランザクションで保存する."""

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
        """有効メモリを履歴を残して保管する."""
        ...

    def record_detected_conflicts(
        self,
        project_id: ProjectId,
        memory_id: MemoryId,
        *,
        detected_at: datetime,
    ) -> tuple[MemoryConflict, ...]:
        """競合状態へ遷移済みの候補と有効メモリの競合を冪等に保存する."""
        ...

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
        """保管メモリを新しい承認待ち版として復元する."""
        ...

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
        """有効メモリを別の有効メモリで置換する."""
        ...

    def list_conflicts(
        self,
        project_id: ProjectId,
        status: ConflictStatus,
    ) -> tuple[MemoryConflict, ...]:
        """プロジェクト内の競合を安定順で返す."""
        ...

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
        """競合と関連メモリを一つのトランザクションで更新する."""
        ...

    def list_due(
        self,
        project_id: ProjectId,
        now: datetime,
    ) -> tuple[MemoryRevalidation, ...]:
        """期限に達した有効メモリを返して期限到来記録を固定する."""
        ...

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
        """同一内容の次版を作成して再確認する."""
        ...

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
        """現版を保管し、無効判定を保存する."""
        ...
