"""SCM 変更要求、コメントおよび同期イベントの領域モデル."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import NormalizedPath
from self_improving_code_review.messages import Messages

_SHA_LENGTH = 40


class ScmProvider(Enum):
    """対応する SCM 提供元."""

    GITHUB = "github"


class ScmChangeState(Enum):
    """SCM 変更要求の状態."""

    OPEN = "open"
    CLOSED = "closed"


class PublicationKind(Enum):
    """SCM へ公開するコメントの配置種別."""

    INLINE = "inline"
    SUMMARY = "summary"


class PublicationStatus(Enum):
    """単一コメント公開の永続状態."""

    PLANNED = "planned"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    UPDATING = "updating"
    FAILED = "failed"
    STALE = "stale"
    BLOCKED = "blocked"


class PublicationOperation(Enum):
    """単一公開計画に対して実際に行った操作の分類."""

    CREATED = "created"
    UPDATED = "updated"
    REUSED = "reused"
    RECOVERED = "recovered"
    FAILED = "failed"
    STALE = "stale"
    BLOCKED = "blocked"


class ScmFeedbackEventStatus(Enum):
    """SCM フィードバックイベントの取込状態."""

    PENDING = "pending"
    PROCESSED = "processed"
    IGNORED = "ignored"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ChangeRef:
    """提供元内で変更要求を一意に識別する参照."""

    provider: ScmProvider
    repository_external_id: str
    change_external_id: str

    def __post_init__(self) -> None:
        """空または曖昧な外部識別子を拒否する."""
        repository_parts = self.repository_external_id.split("/")
        if len(repository_parts) != 2 or not all(part.strip() for part in repository_parts):
            raise InvalidInputError(Messages.SCM_REPOSITORY_ID_FORMAT_INVALID)
        if not self.change_external_id.strip():
            raise InvalidInputError(Messages.SCM_CHANGE_ID_EMPTY)


@dataclass(frozen=True, slots=True)
class ScmChange:
    """レビュー入力を固定する SCM 変更要求のスナップショット."""

    ref: ChangeRef
    base_sha: str
    head_sha: str
    state: ScmChangeState
    is_draft: bool
    source_repository_external_id: str
    updated_at: datetime

    def __post_init__(self) -> None:
        """完全な Git SHA とタイムゾーン付き更新日時を検証する."""
        for revision in (self.base_sha, self.head_sha):
            if len(revision) != _SHA_LENGTH or any(
                char not in "0123456789abcdef" for char in revision
            ):
                raise InvalidInputError(Messages.SCM_REVISION_INVALID)
        if self.updated_at.tzinfo is None:
            raise InvalidInputError(Messages.SCM_CHANGE_TIME_NO_TIMEZONE)


@dataclass(frozen=True, slots=True)
class CommentPosition:
    """先端コミット上のインラインコメント位置."""

    path: NormalizedPath
    line: int
    head_sha: str
    side: str = "new"

    def __post_init__(self) -> None:
        """新しい側の正の行番号だけを許可する."""
        if self.line < 1:
            raise InvalidInputError(Messages.SCM_COMMENT_LINE_INVALID)
        if self.side != "new":
            raise InvalidInputError(Messages.SCM_COMMENT_SIDE_INVALID)
        if len(self.head_sha) != _SHA_LENGTH:
            raise InvalidInputError(Messages.SCM_COMMENT_REVISION_INVALID)


@dataclass(frozen=True, slots=True)
class CreateComment:
    """新規 SCM コメントの安全検査前要求."""

    kind: PublicationKind
    body: str
    position: CommentPosition | None = None

    def __post_init__(self) -> None:
        """配置種別と位置情報の組み合わせを検証する."""
        if not self.body.strip():
            raise InvalidInputError(Messages.SCM_COMMENT_BODY_EMPTY)
        if (self.kind is PublicationKind.INLINE) != (self.position is not None):
            raise InvalidInputError(Messages.SCM_COMMENT_POSITION_INVALID)


@dataclass(frozen=True, slots=True)
class UpdateComment:
    """既存 SCM コメントを更新する要求."""

    kind: PublicationKind
    comment_external_id: str
    body: str

    def __post_init__(self) -> None:
        """更新対象と本文が空でないことを検証する."""
        if not self.comment_external_id.strip() or not self.body.strip():
            raise InvalidInputError(Messages.SCM_COMMENT_UPDATE_INVALID)


@dataclass(frozen=True, slots=True)
class PublishedComment:
    """SCM が確定したコメント識別子と取得先."""

    comment_external_id: str
    url: str


@dataclass(frozen=True, slots=True)
class CommentEvent:
    """フィードバック同期で取得したコメントイベント."""

    event_external_id: str
    comment_external_id: str
    target_comment_external_id: str | None
    actor_external_id: str
    body: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class EventPage:
    """カーソルで継続可能なコメントイベントの一頁."""

    events: tuple[CommentEvent, ...]
    next_cursor: str | None
