"""メモリ状態遷移の方針.

遷移は表で明示した組み合わせだけを許可する. 全遷移に実行者、
UTC 時刻、理由、前後バージョンを記録する.
"""

from __future__ import annotations

from self_improving_code_review.domain.common.enums import MemoryStatus
from self_improving_code_review.domain.common.errors import (
    ConcurrencyConflictError,
    InvalidStateTransitionError,
)
from self_improving_code_review.messages import Messages

__all__ = [
    "ALLOWED_TRANSITIONS",
    "require_allowed_transition",
    "require_lock_version",
]

ALLOWED_TRANSITIONS: dict[MemoryStatus, frozenset[MemoryStatus]] = {
    MemoryStatus.PENDING: frozenset(
        {
            MemoryStatus.ACTIVE,
            MemoryStatus.REJECTED,
            MemoryStatus.CONFLICT,
        }
    ),
    MemoryStatus.CONFLICT: frozenset(
        {
            MemoryStatus.ACTIVE,
            MemoryStatus.REJECTED,
        }
    ),
    MemoryStatus.ACTIVE: frozenset(
        {
            MemoryStatus.SUPERSEDED,
            MemoryStatus.ARCHIVED,
        }
    ),
    MemoryStatus.REJECTED: frozenset(),
    MemoryStatus.SUPERSEDED: frozenset(),
    MemoryStatus.ARCHIVED: frozenset(),
}


def require_allowed_transition(
    current_status: MemoryStatus,
    target_status: MemoryStatus,
) -> None:
    """``current_status`` から ``target_status`` への遷移を検証する.

    Raises:
        InvalidStateTransitionError: 遷移表に存在しない組み合わせの場合.
    """
    if target_status not in ALLOWED_TRANSITIONS[current_status]:
        message = (
            f"{Messages.MEMORY_TRANSITION_NOT_ALLOWED}: "
            f"{current_status.value} -> {target_status.value}"
        )
        raise InvalidStateTransitionError(message)


def require_lock_version(expected: int, actual: int) -> None:
    """楽観ロックのバージョン一致を検証する.

    Raises:
        ConcurrencyConflictError: 想定するロックバージョンと一致しない場合.
    """
    if expected != actual:
        message = f"{Messages.MEMORY_LOCK_VERSION_MISMATCH}: 期待値 {expected}、実際値 {actual}"
        raise ConcurrencyConflictError(message)
