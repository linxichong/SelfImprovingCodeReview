"""M5 メモリ統制、競合、再検証のドメインモデル."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from self_improving_code_review.domain.common.enums import (
    ConflictResolution,
    ConflictStatus,
    MemoryStatus,
    RevalidationStatus,
)
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import (
    MemoryId,
    MemoryVersion,
    ProjectId,
)
from self_improving_code_review.messages import Messages

__all__ = [
    "MemoryConflict",
    "MemoryGovernanceResult",
    "MemoryRevalidation",
]


def _require_utc(name: str, value: datetime) -> None:
    """日時が UTC 対応であることを検証する."""
    if value.tzinfo is None or value.utcoffset() is None:
        message = f"{name} must be timezone aware UTC"
        raise InvalidInputError(message)


def _require_hash(name: str, value: str) -> None:
    """値が SHA-256 の十六進表現であることを検証する."""
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        message = f"{name} must be a SHA-256 hash"
        raise InvalidInputError(message)


@dataclass(frozen=True, slots=True)
class MemoryGovernanceResult:
    """メモリ統制操作後の安全な識別情報."""

    memory_id: MemoryId
    version: MemoryVersion
    status: MemoryStatus
    lock_version: int
    content_hash: str

    def __post_init__(self) -> None:
        """ロック版と内容ハッシュを検証する."""
        if self.lock_version < 1:
            message = Messages.MEMORY_GOVERNANCE_LOCK_INVALID
            raise InvalidInputError(message)
        _require_hash("content_hash", self.content_hash)


@dataclass(frozen=True, slots=True)
class MemoryConflict:
    """検出時の左右バージョンと内容ハッシュを固定した競合."""

    conflict_id: str
    project_id: ProjectId
    left_memory_id: MemoryId
    left_version: MemoryVersion
    left_content_hash: str
    right_memory_id: MemoryId
    right_version: MemoryVersion
    right_content_hash: str
    reason_code: str
    status: ConflictStatus
    lock_version: int
    detected_at: datetime
    resolution: ConflictResolution | None = None
    resolved_at: datetime | None = None

    def __post_init__(self) -> None:
        """正規順、ハッシュ、時刻および解決状態の整合性を検証する."""
        _require_hash("conflict_id", self.conflict_id)
        _require_hash("left_content_hash", self.left_content_hash)
        _require_hash("right_content_hash", self.right_content_hash)
        if self.left_memory_id.value >= self.right_memory_id.value:
            message = Messages.MEMORY_CONFLICT_ORDER_INVALID
            raise InvalidInputError(message)
        if not self.reason_code.strip() or self.lock_version < 1:
            message = Messages.MEMORY_CONFLICT_METADATA_INVALID
            raise InvalidInputError(message)
        _require_utc("detected_at", self.detected_at)
        if (self.status is ConflictStatus.RESOLVED) != (self.resolution is not None):
            message = Messages.MEMORY_CONFLICT_STATE_INCONSISTENT
            raise InvalidInputError(message)
        if self.resolved_at is not None:
            _require_utc("resolved_at", self.resolved_at)


@dataclass(frozen=True, slots=True)
class MemoryRevalidation:
    """期限到来または人間の再検証結果."""

    revalidation_id: str
    project_id: ProjectId
    memory_id: MemoryId
    source_version: MemoryVersion
    status: RevalidationStatus
    due_at: datetime
    created_at: datetime
    result_version: MemoryVersion | None = None
    next_review_after: datetime | None = None

    def __post_init__(self) -> None:
        """識別子と UTC 日時を検証する."""
        _require_hash("revalidation_id", self.revalidation_id)
        _require_utc("due_at", self.due_at)
        _require_utc("created_at", self.created_at)
        if self.next_review_after is not None:
            _require_utc("next_review_after", self.next_review_after)
