"""単位実行の中断結果、失敗分類、使用量を構築する."""

from __future__ import annotations

from decimal import Decimal

from self_improving_code_review.application.ports.context_source import (
    SourceFingerprint,
)
from self_improving_code_review.application.ports.llm_client import (
    LlmUsage,
)
from self_improving_code_review.application.ports.unit_of_work import (
    LlmCallRecord,
)
from self_improving_code_review.application.review.bundle_review_models import (
    BundleReview,
    BundleTask,
)
from self_improving_code_review.application.review.context_tools import (
    ContextToolOutcome,
)
from self_improving_code_review.application.review.models import (
    BundleOutcome,
    ReviewBundle,
)
from self_improving_code_review.domain.common.enums import (
    BundleStatus,
)
from self_improving_code_review.domain.common.errors import (
    InvalidLlmOutputError,
    LlmClientError,
)
from self_improving_code_review.messages import Messages


def add_usage(current: LlmUsage, additional: LlmUsage) -> LlmUsage:
    """2 件の LLM 使用量を合算する."""
    return LlmUsage(
        input_tokens=current.input_tokens + additional.input_tokens,
        output_tokens=current.output_tokens + additional.output_tokens,
        latency_ms=current.latency_ms + additional.latency_ms,
        cost_usd=current.cost_usd + additional.cost_usd,
    )


def add_error_usage(
    current: LlmUsage,
    error: InvalidLlmOutputError,
) -> LlmUsage:
    """不正応答から取得できた使用量を既存の合計へ加える."""
    return add_usage(
        current,
        LlmUsage(
            input_tokens=error.input_tokens,
            output_tokens=error.output_tokens,
            latency_ms=error.latency_ms,
            cost_usd=error.cost_usd,
        ),
    )


def review_error_code(error: LlmClientError | InvalidLlmOutputError) -> str:
    """再開可能性を失わずに LLM 失敗を分類する."""
    if isinstance(error, LlmClientError) and not error.is_retryable:
        return "NonRetryableLlmClientError"
    return type(error).__name__


def outcome_from_records(
    bundle: ReviewBundle,
    records: tuple[LlmCallRecord, ...],
    *,
    status: BundleStatus,
    error_code: str,
    error_message: str,
) -> BundleOutcome:
    """安全な呼出測定値だけを集計したレビュー単位結果を返す."""
    return BundleOutcome(
        bundle_id=bundle.bundle_id,
        status=status,
        findings=(),
        input_tokens=sum(record.input_tokens for record in records),
        output_tokens=sum(record.output_tokens for record in records),
        latency_ms=sum(record.latency_ms for record in records),
        cost_usd=sum((record.cost_usd for record in records), Decimal("0")),
        error_code=error_code,
        error_message=error_message,
    )


def cancelled_review(
    task: BundleTask,
    records: tuple[LlmCallRecord, ...] = (),
    tool_outcomes: tuple[ContextToolOutcome, ...] = (),
    source_fingerprints: tuple[SourceFingerprint, ...] = (),
) -> BundleReview:
    """別レビュー単位の遮断を受けた安全な中断結果を返す."""
    return BundleReview(
        task=task,
        outcome=outcome_from_records(
            task.bundle,
            records,
            status=BundleStatus.SKIPPED,
            error_code="RUN_BLOCKED",
            error_message=Messages.REVIEW_CANCELLED_BY_BLOCKED_BUNDLE,
        ),
        llm_records=records,
        tool_outcomes=tool_outcomes,
        source_fingerprints=source_fingerprints,
    )
