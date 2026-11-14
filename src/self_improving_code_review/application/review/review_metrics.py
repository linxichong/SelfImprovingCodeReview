"""レビュー結果の状態と担当別使用量を決定的に集約する."""

from __future__ import annotations

from decimal import Decimal

from self_improving_code_review.application.review.models import (
    BundleOutcome,
    ReviewerProfileMetrics,
    ReviewMetrics,
)
from self_improving_code_review.application.review.reviewer_merge import (
    ReviewerExecutionSummary,
)
from self_improving_code_review.domain.common.enums import (
    BundleStatus,
    ReviewerType,
    RunStatus,
)


def run_status(*, outcomes: tuple[BundleOutcome, ...], was_blocked: bool) -> RunStatus:
    """実行全体の状態を返す."""
    if was_blocked:
        return RunStatus.BLOCKED
    incomplete = sum(
        outcome.status in {BundleStatus.FAILED, BundleStatus.PARTIAL_SUCCESS}
        for outcome in outcomes
    )
    if incomplete == 0:
        return RunStatus.SUCCESS
    if all(outcome.status is BundleStatus.FAILED for outcome in outcomes):
        return RunStatus.FAILED
    return RunStatus.PARTIAL_SUCCESS


def build_metrics(
    *,
    outcomes: tuple[BundleOutcome, ...],
    candidate_count: int,
    main_count: int,
    reference_count: int,
    invalid_position_count: int,
    invalid_evidence_count: int,
    duplicate_count: int,
    memory_truncated_count: int = 0,
    rejection_reason_counts: tuple[tuple[str, int], ...] = (),
    tool_call_count: int = 0,
    tool_success_count: int = 0,
    tool_latency_ms: int = 0,
    tool_reason_counts: tuple[tuple[str, int], ...] = (),
    dynamic_context_tokens: int = 0,
    reflection_call_count: int = 0,
    reflection_supported_count: int = 0,
    reflection_contradicted_count: int = 0,
    reflection_needs_context_count: int = 0,
    resumed_bundle_count: int = 0,
    reexecuted_bundle_count: int = 0,
    is_m3_enabled: bool = False,
    reviewer_executions: tuple[ReviewerExecutionSummary, ...] = (),
) -> ReviewMetrics:
    """レビュー単位の測定値と検証件数を集計する."""
    return ReviewMetrics(
        input_tokens=sum(item.input_tokens for item in outcomes),
        output_tokens=sum(item.output_tokens for item in outcomes),
        latency_ms=sum(item.latency_ms for item in outcomes),
        cost_usd=sum((item.cost_usd for item in outcomes), Decimal("0")),
        candidate_count=candidate_count,
        main_finding_count=main_count,
        reference_finding_count=reference_count,
        invalid_position_count=invalid_position_count,
        invalid_evidence_count=invalid_evidence_count,
        duplicate_count=duplicate_count,
        bundle_count=len(outcomes),
        failed_bundle_count=sum(
            item.status in {BundleStatus.FAILED, BundleStatus.PARTIAL_SUCCESS} for item in outcomes
        ),
        memory_truncated_count=memory_truncated_count,
        rejection_reason_counts=rejection_reason_counts,
        tool_call_count=tool_call_count,
        tool_success_count=tool_success_count,
        tool_latency_ms=tool_latency_ms,
        tool_reason_counts=tool_reason_counts,
        dynamic_context_tokens=dynamic_context_tokens,
        reflection_call_count=reflection_call_count,
        reflection_supported_count=reflection_supported_count,
        reflection_contradicted_count=reflection_contradicted_count,
        reflection_needs_context_count=reflection_needs_context_count,
        resumed_bundle_count=resumed_bundle_count,
        reexecuted_bundle_count=reexecuted_bundle_count,
        is_m3_enabled=is_m3_enabled,
        reviewer_profile_metrics=aggregate_reviewer_metrics(reviewer_executions),
    )


def aggregate_reviewer_metrics(
    executions: tuple[ReviewerExecutionSummary, ...],
) -> tuple[ReviewerProfileMetrics, ...]:
    """担当別実行測定値を担当種別ごとの安定順へ集約する."""
    grouped: dict[ReviewerType, list[ReviewerExecutionSummary]] = {}
    for execution in executions:
        grouped.setdefault(execution.reviewer_type, []).append(execution)
    return tuple(
        ReviewerProfileMetrics(
            reviewer_type=reviewer_type,
            input_tokens=sum(item.input_tokens for item in grouped[reviewer_type]),
            output_tokens=sum(item.output_tokens for item in grouped[reviewer_type]),
            latency_ms=sum(item.latency_ms for item in grouped[reviewer_type]),
            cost_usd=sum(
                (item.cost_usd for item in grouped[reviewer_type]),
                Decimal("0"),
            ),
            valid_finding_count=sum(item.valid_finding_count for item in grouped[reviewer_type]),
            duplicate_count=sum(item.duplicate_count for item in grouped[reviewer_type]),
        )
        for reviewer_type in ReviewerType
        if reviewer_type in grouped
    )
