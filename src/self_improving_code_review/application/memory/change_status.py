"""メモリ状態遷移ユースケース.

承認と却下は人間の統制操作である. 実行者を特定できない場合は失敗させ、
適用範囲を変更する承認は現在バージョンを上書きせず新バージョンを作る.
"""

from __future__ import annotations

from dataclasses import dataclass

from self_improving_code_review.application.ports.authorization import ActorAuthorizer
from self_improving_code_review.application.ports.clock import Clock
from self_improving_code_review.application.ports.memory_persistence import MemoryRepository
from self_improving_code_review.domain.common.enums import MemoryStatus, Permission
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import MemoryId, MemoryVersion
from self_improving_code_review.domain.feedback.validators import normalize_free_text
from self_improving_code_review.domain.memory.conflicts import find_conflicts
from self_improving_code_review.domain.memory.models import (
    MemoryContent,
    MemoryRecord,
    MemoryTransition,
)
from self_improving_code_review.domain.memory.scope import MemoryScope
from self_improving_code_review.domain.memory.transitions import (
    require_allowed_transition,
    require_lock_version,
)
from self_improving_code_review.messages import Messages

__all__ = ["ApproveMemory", "MemoryStatusChange", "RejectMemory"]


@dataclass(frozen=True, slots=True)
class MemoryStatusChange:
    """状態遷移の結果.

    競合を検出した場合は ``CONFLICT`` を返し、既存の有効メモリを変更しない.
    """

    memory_id: MemoryId
    version: MemoryVersion
    status: MemoryStatus
    conflict_reason_codes: tuple[str, ...] = ()


class ApproveMemory:
    """候補メモリを有効化する."""

    def __init__(
        self,
        *,
        memory_repository: MemoryRepository,
        authorizer: ActorAuthorizer,
        clock: Clock,
    ) -> None:
        """リポジトリ、認可処理、時刻取得処理を保持する."""
        self._repository = memory_repository
        self._authorizer = authorizer
        self._clock = clock

    def execute(
        self,
        memory_id: MemoryId,
        *,
        actor_id: str,
        reason: str,
        expected_lock_version: int,
        narrowed_scope: MemoryScope | None = None,
    ) -> MemoryStatusChange:
        """候補を検証して有効化する.

        Args:
            memory_id: 対象メモリの論理識別子.
            actor_id: 承認を実行する保守担当者.
            reason: 承認理由. 監査記録として必須とする.
            expected_lock_version: 楽観ロックで想定する現在のロックバージョン.
            narrowed_scope: 適用範囲を縮小する場合の新しい適用範囲.

        Returns:
            遷移後の状態と、競合を検出した場合はその理由コード.

        Raises:
            AuthorizationError: 実行者に承認権限がない場合.
            InvalidInputError: メモリが存在しない、または理由が不正な場合.
            ConcurrencyConflictError: 同じバージョンから並行更新された場合.
            InvalidStateTransitionError: 現在状態から有効化できない場合.
        """
        self._authorizer.require(actor_id, Permission.APPROVE_MEMORY)
        normalized_reason = normalize_free_text("reason", reason)
        record, content = _require_memory(self._repository, memory_id)
        require_lock_version(expected_lock_version, record.lock_version)
        require_allowed_transition(record.current_status, MemoryStatus.ACTIVE)

        target_version = content.version
        narrowed_content: MemoryContent | None = None
        if narrowed_scope is not None and narrowed_scope != content.scope:
            target_version = content.version.next()
            narrowed_content = _with_scope(
                content, narrowed_scope, target_version, actor_id, self._clock
            )

        active = self._repository.list_by_status(record.project_id, MemoryStatus.ACTIVE)
        conflicts = find_conflicts(
            _with_version(content, target_version, narrowed_scope),
            tuple(item[1] for item in active),
        )
        target_status = MemoryStatus.CONFLICT if conflicts else MemoryStatus.ACTIVE
        require_allowed_transition(record.current_status, target_status)

        transition = MemoryTransition(
            memory_id=memory_id,
            memory_version=target_version,
            from_status=record.current_status,
            to_status=target_status,
            actor_id=actor_id.strip(),
            reason=normalized_reason,
            created_at=self._clock.now_utc(),
        )
        if narrowed_content is None:
            self._repository.change_status(transition, expected_lock_version)
        else:
            self._repository.add_version_and_change_status(
                memory_id,
                narrowed_content,
                transition,
                expected_lock_version,
            )
        return MemoryStatusChange(
            memory_id=memory_id,
            version=target_version,
            status=target_status,
            conflict_reason_codes=tuple(item.reason_code for item in conflicts),
        )


class RejectMemory:
    """候補メモリを却下する."""

    def __init__(
        self,
        *,
        memory_repository: MemoryRepository,
        authorizer: ActorAuthorizer,
        clock: Clock,
    ) -> None:
        """リポジトリ、認可処理、時刻取得処理を保持する."""
        self._repository = memory_repository
        self._authorizer = authorizer
        self._clock = clock

    def execute(
        self,
        memory_id: MemoryId,
        *,
        actor_id: str,
        reason: str,
        expected_lock_version: int,
    ) -> MemoryStatusChange:
        """候補を却下する.

        Args:
            memory_id: 対象メモリの論理識別子.
            actor_id: 却下を実行する保守担当者.
            reason: 却下理由. 監査記録として必須とする.
            expected_lock_version: 楽観ロックで想定する現在のロックバージョン.

        Returns:
            遷移後の状態.

        Raises:
            AuthorizationError: 実行者に承認権限がない場合.
            InvalidInputError: メモリが存在しない、または理由が不正な場合.
            ConcurrencyConflictError: 同じバージョンから並行更新された場合.
            InvalidStateTransitionError: 現在状態から却下できない場合.
        """
        self._authorizer.require(actor_id, Permission.APPROVE_MEMORY)
        normalized_reason = normalize_free_text("reason", reason)
        record, content = _require_memory(self._repository, memory_id)
        require_lock_version(expected_lock_version, record.lock_version)
        require_allowed_transition(record.current_status, MemoryStatus.REJECTED)

        self._repository.change_status(
            MemoryTransition(
                memory_id=memory_id,
                memory_version=content.version,
                from_status=record.current_status,
                to_status=MemoryStatus.REJECTED,
                actor_id=actor_id.strip(),
                reason=normalized_reason,
                created_at=self._clock.now_utc(),
            ),
            expected_lock_version,
        )
        return MemoryStatusChange(
            memory_id=memory_id,
            version=content.version,
            status=MemoryStatus.REJECTED,
        )


def _require_memory(
    repository: MemoryRepository,
    memory_id: MemoryId,
) -> tuple[MemoryRecord, MemoryContent]:
    """メモリと現在バージョンを返す.

    Raises:
        InvalidInputError: メモリが存在しない場合.
    """
    found = repository.find(memory_id)
    if found is None:
        message = Messages.memory_not_found(str(memory_id))
        raise InvalidInputError(message)
    return found


def _with_scope(
    content: MemoryContent,
    scope: MemoryScope,
    version: MemoryVersion,
    actor_id: str,
    clock: Clock,
) -> MemoryContent:
    """適用範囲を縮小した新しい内容バージョンを返す."""
    return MemoryContent(
        memory_id=content.memory_id,
        version=version,
        category=content.category,
        scope=scope,
        fact=content.fact,
        review_instruction=content.review_instruction,
        polarity=content.polarity,
        confidence=content.confidence,
        evidence_summary=content.evidence_summary,
        created_by=actor_id.strip(),
        created_at=clock.now_utc(),
    )


def _with_version(
    content: MemoryContent,
    version: MemoryVersion,
    scope: MemoryScope | None,
) -> MemoryContent:
    """競合判定に使用する、バージョンと適用範囲を反映した内容を返す."""
    if version == content.version and scope is None:
        return content
    return MemoryContent(
        memory_id=content.memory_id,
        version=version,
        category=content.category,
        scope=scope or content.scope,
        fact=content.fact,
        review_instruction=content.review_instruction,
        polarity=content.polarity,
        confidence=content.confidence,
        evidence_summary=content.evidence_summary,
        created_by=content.created_by,
        created_at=content.created_at,
    )
