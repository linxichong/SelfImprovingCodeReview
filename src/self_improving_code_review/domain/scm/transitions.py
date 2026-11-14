"""SCM コメント公開状態の遷移規則."""

from __future__ import annotations

from self_improving_code_review.domain.common.errors import InvalidStateTransitionError
from self_improving_code_review.domain.scm.models import PublicationStatus
from self_improving_code_review.messages import Messages

_ALLOWED_TRANSITIONS: dict[PublicationStatus, frozenset[PublicationStatus]] = {
    PublicationStatus.PLANNED: frozenset(
        {
            PublicationStatus.PLANNED,
            PublicationStatus.PUBLISHING,
            PublicationStatus.UPDATING,
            PublicationStatus.STALE,
            PublicationStatus.BLOCKED,
        }
    ),
    PublicationStatus.PUBLISHING: frozenset(
        {
            PublicationStatus.PLANNED,
            PublicationStatus.PUBLISHING,
            PublicationStatus.PUBLISHED,
            PublicationStatus.FAILED,
            PublicationStatus.STALE,
            PublicationStatus.BLOCKED,
        }
    ),
    PublicationStatus.PUBLISHED: frozenset(
        {
            PublicationStatus.PLANNED,
            PublicationStatus.UPDATING,
            PublicationStatus.STALE,
            PublicationStatus.BLOCKED,
        }
    ),
    PublicationStatus.UPDATING: frozenset(
        {
            PublicationStatus.PLANNED,
            PublicationStatus.UPDATING,
            PublicationStatus.PUBLISHED,
            PublicationStatus.FAILED,
            PublicationStatus.STALE,
            PublicationStatus.BLOCKED,
        }
    ),
    PublicationStatus.FAILED: frozenset(
        {
            PublicationStatus.PLANNED,
            PublicationStatus.PUBLISHING,
            PublicationStatus.UPDATING,
            PublicationStatus.PUBLISHED,
            PublicationStatus.FAILED,
            PublicationStatus.STALE,
            PublicationStatus.BLOCKED,
        }
    ),
    PublicationStatus.STALE: frozenset(),
    PublicationStatus.BLOCKED: frozenset(),
}


def transition_publication(
    current: PublicationStatus,
    target: PublicationStatus,
) -> PublicationStatus:
    """許可された公開状態遷移を適用し、許可されない遷移を拒否する."""
    if target not in _ALLOWED_TRANSITIONS[current]:
        message = (
            f"{Messages.SCM_PUBLICATION_TRANSITION_INVALID}: {current.value} -> {target.value}"
        )
        raise InvalidStateTransitionError(message)
    return target


def allowed_source_statuses(target: PublicationStatus) -> tuple[PublicationStatus, ...]:
    """``target`` へ遷移できる元状態を安定順で返す."""
    return tuple(
        current for current in PublicationStatus if target in _ALLOWED_TRANSITIONS[current]
    )
