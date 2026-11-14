"""担当別結果の統合、版混在判定、遮断時の結果清理を行う."""

from __future__ import annotations

from dataclasses import replace

from self_improving_code_review.application.review.bundle_review_models import (
    BundleReview,
)
from self_improving_code_review.application.review.models import (
    BundleOutcome,
)
from self_improving_code_review.application.review.reviewer_merge import (
    ReviewerResult,
    merge_reviewer_results,
)
from self_improving_code_review.domain.common.enums import (
    BundleStatus,
)
from self_improving_code_review.messages import Messages


def discard_blocked_review(outcome: BundleOutcome, *, is_source: bool) -> BundleOutcome:
    """遮断された実行の業務結果を除き、安全な測定値だけを残す."""
    return replace(
        outcome,
        status=BundleStatus.SKIPPED,
        findings=(),
        error_code="SECRET_DETECTED" if is_source else "RUN_BLOCKED",
        error_message=(
            Messages.SENSITIVE_CANDIDATE_IN_CONTEXT_TOOL
            if is_source
            else Messages.REVIEW_CANCELLED_BY_BLOCKED_BUNDLE
        ),
    )


def merge_specialist_reviews(
    reviews: tuple[BundleReview, ...],
) -> tuple[BundleReview, ...]:
    """同一レビュー単位の担当別結果を一件へ決定的に統合する."""
    grouped: dict[int, list[BundleReview]] = {}
    for review in reviews:
        grouped.setdefault(review.task.bundle.bundle_id.sequence, []).append(review)

    merged_reviews: list[BundleReview] = []
    for sequence in sorted(grouped):
        group = tuple(grouped[sequence])
        primary = next(item for item in group if not item.task.plan.is_specialist)
        merged = merge_reviewer_results(
            tuple(
                ReviewerResult(
                    reviewer_type=item.task.plan.reviewer_type,
                    reason_code=item.task.plan.profile_reason_code,
                    score=item.task.plan.profile_score,
                    outcome=item.outcome,
                    is_primary=not item.task.plan.is_specialist,
                )
                for item in group
            )
        )
        tasks_by_profile = {item.task.plan.reviewer_type: item.task for item in group}
        executions = tuple(
            replace(
                execution,
                prompt_version=tasks_by_profile[execution.reviewer_type].built.prompt_version,
                prompt_hash=tasks_by_profile[execution.reviewer_type].built.prompt_hash,
                input_hash=tasks_by_profile[execution.reviewer_type].input_hash,
            )
            for execution in merged.executions
        )
        merged_reviews.append(
            replace(
                primary,
                outcome=merged.outcome,
                llm_records=tuple(record for item in group for record in item.llm_records),
                tool_outcomes=tuple(tool for item in group for tool in item.tool_outcomes),
                reflected_findings=tuple(
                    finding for item in group for finding in item.reflected_findings
                ),
                secret_findings=tuple(
                    finding for item in group for finding in item.secret_findings
                ),
                candidate_count=sum(item.candidate_count for item in group),
                rejection_reason_counts=tuple(
                    reason for item in group for reason in item.rejection_reason_counts
                ),
                reflection_call_count=sum(item.reflection_call_count for item in group),
                source_fingerprints=tuple(
                    source for item in group for source in item.source_fingerprints
                ),
                finding_sources=merged.sources,
                reviewer_executions=executions,
            )
        )
    return tuple(merged_reviews)


def find_changed_source_sequences(reviews: tuple[BundleReview, ...]) -> frozenset[int]:
    """同じ作業ツリーパスで異なる内容を読んだレビュー単位を返す."""
    hashes_by_path: dict[str, dict[str, set[int]]] = {}
    for review in reviews:
        for item in review.source_fingerprints:
            if item.source_revision is not None:
                continue
            hashes_by_path.setdefault(item.path.value, {}).setdefault(
                item.content_hash,
                set(),
            ).add(item.bundle_sequence)
    return frozenset(
        sequence
        for hashes in hashes_by_path.values()
        if len(hashes) > 1
        for sequences in hashes.values()
        for sequence in sequences
    )


def discard_changed_source(review: BundleReview) -> BundleReview:
    """版が混在したレビュー単位の業務結果を失敗へ置き換える."""
    return replace(
        review,
        outcome=replace(
            review.outcome,
            status=BundleStatus.FAILED,
            findings=(),
            error_code="SOURCE_CHANGED",
            error_message=Messages.CONTEXT_SOURCE_CHANGED_DURING_REVIEW,
        ),
        reflected_findings=(),
        candidate_count=0,
        rejection_reason_counts=(),
    )
