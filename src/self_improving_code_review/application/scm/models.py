"""SCM アプリケーション処理の入出力モデル."""

from __future__ import annotations

from dataclasses import dataclass

from self_improving_code_review.application.review.models import ReviewResult
from self_improving_code_review.domain.common.enums import FeedbackLabel
from self_improving_code_review.domain.scm.models import (
    CreateComment,
    PublicationKind,
    PublicationOperation,
    PublicationStatus,
    ScmChange,
)


@dataclass(frozen=True, slots=True)
class PublicationPlan:
    """単一コメントの決定的な公開計画."""

    publication_id: str
    run_id: str
    finding_id: str | None
    fingerprint_hash: str
    request_hash: str
    request: CreateComment

    @property
    def kind(self) -> PublicationKind:
        """コメント要求の配置種別を返す."""
        return self.request.kind


@dataclass(frozen=True, slots=True)
class PublicationResult:
    """単一公開計画の外部反映結果."""

    fingerprint_hash: str
    status: PublicationStatus
    operation: PublicationOperation
    external_comment_id: str | None = None
    external_comment_url: str | None = None
    safe_error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ScmReviewMetrics:
    """一回の SCM レビューと公開処理の集計値."""

    api_call_count: int = 0
    api_retry_count: int = 0
    api_retry_wait_ms: int = 0
    planned_inline_count: int = 0
    planned_summary_count: int = 0
    created_count: int = 0
    updated_count: int = 0
    reused_count: int = 0
    recovered_count: int = 0
    failed_count: int = 0
    stale_count: int = 0
    blocked_count: int = 0
    head_change_abort_count: int = 0


@dataclass(frozen=True, slots=True)
class ScmReviewOutcome:
    """SCM レビュー、公開計画および任意の反映結果."""

    change: ScmChange
    review: ReviewResult
    plans: tuple[PublicationPlan, ...]
    publications: tuple[PublicationResult, ...] = ()
    metrics: ScmReviewMetrics = ScmReviewMetrics()


@dataclass(frozen=True, slots=True)
class ParsedFeedbackCommand:
    """SCM コメントから厳密に解析した M2 フィードバック入力."""

    label: FeedbackLabel
    reason: str


@dataclass(frozen=True, slots=True)
class ScmFeedbackTarget:
    """出版物から確定した M2 の実行と指摘."""

    publication_id: str
    run_id: str
    finding_id: str
    external_comment_id: str


@dataclass(frozen=True, slots=True)
class ScmFeedbackEventRecord:
    """本文を含まない SCM イベント監査レコード."""

    event_external_id: str
    target: ScmFeedbackTarget | None
    external_comment_id: str
    actor_id: str | None
    label: FeedbackLabel | None
    payload_hash: str
    mapped_feedback_id: str | None
    status: str
    safe_error_code: str | None


@dataclass(frozen=True, slots=True)
class ScmFeedbackSyncOutcome:
    """一回のフィードバック同期集計."""

    fetched_count: int
    processed_count: int
    ignored_count: int
    failed_count: int
    duplicate_count: int
    next_cursor: str | None
