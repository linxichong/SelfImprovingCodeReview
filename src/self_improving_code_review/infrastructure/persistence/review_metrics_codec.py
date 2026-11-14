"""保存測定値のJSON変換と既存の境界検証を集約する."""

from __future__ import annotations

from decimal import Decimal

from self_improving_code_review.application.review.models import (
    ReviewerProfileMetrics,
    ReviewMetrics,
)
from self_improving_code_review.domain.common.enums import (
    ReviewerType,
)
from self_improving_code_review.domain.common.errors import (
    DatabaseError,
)
from self_improving_code_review.messages import Messages


def metrics_document(metrics: ReviewMetrics) -> dict[str, object]:
    """JSON で安全に扱える測定値を返す."""
    document: dict[str, object] = {
        "input_tokens": metrics.input_tokens,
        "output_tokens": metrics.output_tokens,
        "latency_ms": metrics.latency_ms,
        "cost_usd": str(metrics.cost_usd),
        "candidate_count": metrics.candidate_count,
        "accepted_candidate_count": metrics.accepted_candidate_count,
        "rejected_candidate_count": metrics.rejected_candidate_count,
        "rejection_reasons": dict(metrics.rejection_reason_counts),
        "main_finding_count": metrics.main_finding_count,
        "reference_finding_count": metrics.reference_finding_count,
        "invalid_position_count": metrics.invalid_position_count,
        "invalid_evidence_count": metrics.invalid_evidence_count,
        "duplicate_count": metrics.duplicate_count,
        "bundle_count": metrics.bundle_count,
        "failed_bundle_count": metrics.failed_bundle_count,
        "validation_pass_rate": metrics.validation_pass_rate,
        "tool_call_count": metrics.tool_call_count,
        "tool_success_count": metrics.tool_success_count,
        "tool_latency_ms": metrics.tool_latency_ms,
        "tool_reason_counts": dict(metrics.tool_reason_counts),
        "dynamic_context_tokens": metrics.dynamic_context_tokens,
        "reflection_call_count": metrics.reflection_call_count,
        "reflection_supported_count": metrics.reflection_supported_count,
        "reflection_contradicted_count": metrics.reflection_contradicted_count,
        "reflection_needs_context_count": metrics.reflection_needs_context_count,
        "resumed_bundle_count": metrics.resumed_bundle_count,
        "reexecuted_bundle_count": metrics.reexecuted_bundle_count,
        "is_m3_enabled": metrics.is_m3_enabled,
    }
    if metrics.reviewer_profile_metrics:
        document["reviewer_profile_metrics"] = [
            {
                "profile": item.reviewer_type.value,
                "input_tokens": item.input_tokens,
                "output_tokens": item.output_tokens,
                "latency_ms": item.latency_ms,
                "cost_usd": str(item.cost_usd),
                "valid_finding_count": item.valid_finding_count,
                "duplicate_count": item.duplicate_count,
            }
            for item in metrics.reviewer_profile_metrics
        ]
    return document


def metrics_from_document(document: object) -> ReviewMetrics:
    """JSON 表現から集約測定値を再構築する."""
    if not isinstance(document, dict):
        message = Messages.STORED_REVIEW_METRICS_INVALID
        raise DatabaseError(message)
    return ReviewMetrics(
        input_tokens=int(document["input_tokens"]),
        output_tokens=int(document["output_tokens"]),
        latency_ms=int(document["latency_ms"]),
        cost_usd=Decimal(str(document["cost_usd"])),
        candidate_count=int(document["candidate_count"]),
        main_finding_count=int(document["main_finding_count"]),
        reference_finding_count=int(document["reference_finding_count"]),
        invalid_position_count=int(document["invalid_position_count"]),
        invalid_evidence_count=int(document["invalid_evidence_count"]),
        duplicate_count=int(document["duplicate_count"]),
        bundle_count=int(document["bundle_count"]),
        failed_bundle_count=int(document["failed_bundle_count"]),
        rejection_reason_counts=_rejection_reason_counts(document),
        tool_call_count=int(document.get("tool_call_count", 0)),
        tool_success_count=int(document.get("tool_success_count", 0)),
        tool_latency_ms=int(document.get("tool_latency_ms", 0)),
        tool_reason_counts=_safe_reason_counts(document, "tool_reason_counts"),
        dynamic_context_tokens=int(document.get("dynamic_context_tokens", 0)),
        reflection_call_count=int(document.get("reflection_call_count", 0)),
        reflection_supported_count=int(document.get("reflection_supported_count", 0)),
        reflection_contradicted_count=int(document.get("reflection_contradicted_count", 0)),
        reflection_needs_context_count=int(document.get("reflection_needs_context_count", 0)),
        resumed_bundle_count=int(document.get("resumed_bundle_count", 0)),
        reexecuted_bundle_count=int(document.get("reexecuted_bundle_count", 0)),
        is_m3_enabled=bool(document.get("is_m3_enabled", False)),
        reviewer_profile_metrics=_reviewer_profile_metrics(document),
    )


def _reviewer_profile_metrics(
    document: dict[object, object],
) -> tuple[ReviewerProfileMetrics, ...]:
    """保存済み測定値から担当別の安全な集計を復元する."""
    raw_metrics = document.get("reviewer_profile_metrics", [])
    if not isinstance(raw_metrics, list):
        raise DatabaseError(Messages.STORED_REVIEWER_METRICS_INVALID)
    try:
        metrics = [_reviewer_profile_metric(item) for item in raw_metrics]
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise DatabaseError(Messages.STORED_REVIEWER_METRICS_INVALID) from exc
    return tuple(metrics)


def _reviewer_profile_metric(item: object) -> ReviewerProfileMetrics:
    """保存済み一担当分の測定値を検証して復元する."""
    if not isinstance(item, dict):
        raise TypeError
    return ReviewerProfileMetrics(
        reviewer_type=ReviewerType(str(item["profile"])),
        input_tokens=_non_negative_int(item["input_tokens"]),
        output_tokens=_non_negative_int(item["output_tokens"]),
        latency_ms=_non_negative_int(item["latency_ms"]),
        cost_usd=Decimal(str(item["cost_usd"])),
        valid_finding_count=_non_negative_int(item["valid_finding_count"]),
        duplicate_count=_non_negative_int(item["duplicate_count"]),
    )


def _non_negative_int(value: object) -> int:
    """真偽値を除く非負整数だけを返す."""
    if type(value) is not int or value < 0:
        raise ValueError
    return value


def _rejection_reason_counts(document: dict[object, object]) -> tuple[tuple[str, int], ...]:
    """保存済み測定値から安全な候補除外理由集計を復元する."""
    return _safe_reason_counts(document, "rejection_reasons")


def _safe_reason_counts(
    document: dict[object, object],
    field_name: str,
) -> tuple[tuple[str, int], ...]:
    """保存済み測定値から安全な理由コード集計を復元する."""
    raw_reasons = document.get(field_name, {})
    if not isinstance(raw_reasons, dict):
        message = Messages.STORED_REJECTION_COUNTS_INVALID
        raise DatabaseError(message)
    counts: list[tuple[str, int]] = []
    for reason, count in raw_reasons.items():
        if not isinstance(reason, str) or type(count) is not int or count < 0:
            message = Messages.STORED_REJECTION_COUNTS_INVALID
            raise DatabaseError(message)
        counts.append((reason, count))
    return tuple(sorted(counts))
