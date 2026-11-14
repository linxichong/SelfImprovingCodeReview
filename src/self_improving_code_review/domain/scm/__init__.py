"""SCM 連携の純粋な領域モデルと状態規則."""

from self_improving_code_review.domain.scm.models import (
    ChangeRef,
    CommentEvent,
    CommentPosition,
    CreateComment,
    EventPage,
    PublicationKind,
    PublicationOperation,
    PublicationStatus,
    PublishedComment,
    ScmChange,
    ScmChangeState,
    ScmFeedbackEventStatus,
    ScmProvider,
    UpdateComment,
)
from self_improving_code_review.domain.scm.transitions import (
    allowed_source_statuses,
    transition_publication,
)

__all__ = [
    "ChangeRef",
    "CommentEvent",
    "CommentPosition",
    "CreateComment",
    "EventPage",
    "PublicationKind",
    "PublicationOperation",
    "PublicationStatus",
    "PublishedComment",
    "ScmChange",
    "ScmChangeState",
    "ScmFeedbackEventStatus",
    "ScmProvider",
    "UpdateComment",
    "allowed_source_statuses",
    "transition_publication",
]
