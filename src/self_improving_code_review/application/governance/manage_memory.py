"""M5 メモリ生命周期、競合解決、再検証のユースケース."""

from __future__ import annotations

from datetime import timedelta

from self_improving_code_review.application.ports.authorization import ActorAuthorizer
from self_improving_code_review.application.ports.clock import Clock
from self_improving_code_review.application.ports.memory_governance import (
    MemoryGovernanceRepository,
)
from self_improving_code_review.domain.common.enums import (
    ConflictResolution,
    ConflictStatus,
    Permission,
)
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.common.values import MemoryId, ProjectId
from self_improving_code_review.domain.feedback.validators import normalize_free_text
from self_improving_code_review.domain.governance.memory_models import (
    MemoryConflict,
    MemoryGovernanceResult,
    MemoryRevalidation,
)
from self_improving_code_review.domain.memory.scope import MemoryScope

__all__ = ["ManageMemoryLifecycle", "RevalidateMemory"]


def _audit_hash(name: str, value: str) -> str:
    """統制用自由記述を検証してハッシュだけを返す."""
    return hash_text(normalize_free_text(name, value))


class ManageMemoryLifecycle:
    """保管、復元、置換および競合解決を調停する."""

    def __init__(
        self,
        repository: MemoryGovernanceRepository,
        authorizer: ActorAuthorizer,
        clock: Clock,
    ) -> None:
        """統制リポジトリ、認可処理、時刻取得処理を保持する."""
        self._repository = repository
        self._authorizer = authorizer
        self._clock = clock

    def archive(
        self,
        project_id: ProjectId,
        memory_id: MemoryId,
        *,
        expected_lock_version: int,
        actor_id: str,
        reason: str,
    ) -> MemoryGovernanceResult:
        """有効メモリを保管する."""
        self._authorizer.require(actor_id, Permission.GOVERN_MEMORY)
        return self._repository.archive(
            project_id,
            memory_id,
            expected_lock_version=expected_lock_version,
            actor_id=actor_id.strip(),
            reason_hash=_audit_hash("reason", reason),
            occurred_at=self._clock.now_utc(),
        )

    def restore(
        self,
        project_id: ProjectId,
        memory_id: MemoryId,
        *,
        expected_lock_version: int,
        actor_id: str,
        reason: str,
    ) -> MemoryGovernanceResult:
        """保管版を次の承認待ち版として復元する."""
        self._authorizer.require(actor_id, Permission.GOVERN_MEMORY)
        return self._repository.restore(
            project_id,
            memory_id,
            expected_lock_version=expected_lock_version,
            actor_id=actor_id.strip(),
            reason_hash=_audit_hash("reason", reason),
            occurred_at=self._clock.now_utc(),
        )

    def supersede(
        self,
        project_id: ProjectId,
        memory_id: MemoryId,
        replacement_id: MemoryId,
        *,
        expected_lock_version: int,
        actor_id: str,
        reason: str,
    ) -> MemoryGovernanceResult:
        """有効メモリを別の有効メモリで置換する."""
        self._authorizer.require(actor_id, Permission.GOVERN_MEMORY)
        return self._repository.supersede(
            project_id,
            memory_id,
            replacement_id,
            expected_lock_version=expected_lock_version,
            actor_id=actor_id.strip(),
            reason_hash=_audit_hash("reason", reason),
            occurred_at=self._clock.now_utc(),
        )

    def list_conflicts(
        self,
        project_id: ProjectId,
        status: ConflictStatus,
    ) -> tuple[MemoryConflict, ...]:
        """プロジェクト内の競合を返す."""
        return self._repository.list_conflicts(project_id, status)

    def resolve_conflict(
        self,
        project_id: ProjectId,
        conflict_id: str,
        resolution: ConflictResolution,
        *,
        expected_lock_version: int,
        actor_id: str,
        reason: str,
        evidence: str,
        narrowed_scope: MemoryScope | None = None,
    ) -> tuple[MemoryConflict, MemoryGovernanceResult]:
        """人間が選んだ競合解決を原子的に適用する."""
        self._authorizer.require(actor_id, Permission.GOVERN_MEMORY)
        return self._repository.resolve_conflict(
            project_id,
            conflict_id,
            resolution,
            expected_lock_version=expected_lock_version,
            actor_id=actor_id.strip(),
            reason_hash=_audit_hash("reason", reason),
            evidence_hash=_audit_hash("evidence", evidence),
            occurred_at=self._clock.now_utc(),
            narrowed_scope=narrowed_scope,
        )


class RevalidateMemory:
    """期限到来メモリの列挙、再確認および無効化を調停する."""

    def __init__(
        self,
        repository: MemoryGovernanceRepository,
        authorizer: ActorAuthorizer,
        clock: Clock,
        *,
        default_days: int,
    ) -> None:
        """依存処理と次回期限の既定日数を保持する."""
        self._repository = repository
        self._authorizer = authorizer
        self._clock = clock
        self._default_days = default_days

    def list_due(self, project_id: ProjectId) -> tuple[MemoryRevalidation, ...]:
        """現在時刻までに期限へ到達したメモリを返す."""
        return self._repository.list_due(project_id, self._clock.now_utc())

    def confirm(
        self,
        project_id: ProjectId,
        memory_id: MemoryId,
        *,
        expected_lock_version: int,
        actor_id: str,
        reason: str,
        evidence: str,
    ) -> MemoryGovernanceResult:
        """同一内容の次版を作成して有効期限を更新する."""
        self._authorizer.require(actor_id, Permission.REVALIDATE_MEMORY)
        now = self._clock.now_utc()
        return self._repository.confirm_revalidation(
            project_id,
            memory_id,
            expected_lock_version=expected_lock_version,
            actor_id=actor_id.strip(),
            reason_hash=_audit_hash("reason", reason),
            evidence_hash=_audit_hash("evidence", evidence),
            occurred_at=now,
            next_review_after=now + timedelta(days=self._default_days),
        )

    def invalidate(
        self,
        project_id: ProjectId,
        memory_id: MemoryId,
        *,
        expected_lock_version: int,
        actor_id: str,
        reason: str,
        evidence: str,
    ) -> MemoryGovernanceResult:
        """現版を保管して再検証の無効判定を保存する."""
        self._authorizer.require(actor_id, Permission.REVALIDATE_MEMORY)
        return self._repository.invalidate_revalidation(
            project_id,
            memory_id,
            expected_lock_version=expected_lock_version,
            actor_id=actor_id.strip(),
            reason_hash=_audit_hash("reason", reason),
            evidence_hash=_audit_hash("evidence", evidence),
            occurred_at=self._clock.now_utc(),
        )
