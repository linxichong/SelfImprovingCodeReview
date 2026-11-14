"""レビュー計画、固定入力、保存結果の識別と再開可否を決定する."""

from __future__ import annotations

from collections.abc import Mapping

from self_improving_code_review.application.review.models import (
    BundleOutcome,
    ReviewBundle,
    ReviewPlan,
    ReviewResult,
)
from self_improving_code_review.domain.common.enums import (
    BundleStatus,
    RunStatus,
)
from self_improving_code_review.domain.common.hashing import hash_lines, hash_text


def is_resume_candidate(result: ReviewResult | None) -> bool:
    """保存結果が再試行可能な失敗だけを含む場合は True を返す."""
    if result is None:
        return False
    if result.run.status is RunStatus.RUNNING:
        return bool(result.bundle_outcomes)
    retryable_codes = {"LlmClientError", "InvalidLlmOutputError", "DatabaseError"}
    failed = tuple(
        outcome for outcome in result.bundle_outcomes if outcome.status is BundleStatus.FAILED
    )
    return bool(failed) and all(outcome.error_code in retryable_codes for outcome in failed)


def compute_plan_hash(
    bundles: tuple[ReviewBundle, ...],
    planned_reviews: Mapping[int, tuple[ReviewPlan, object]],
) -> str:
    """レビュー単位と担当振り分けの決定結果を安定ハッシュ化する."""
    return hash_lines(
        (
            "m3-plan-v1",
            *(
                plan_hash_entry(
                    bundle,
                    planned_reviews[bundle.bundle_id.sequence][0],
                )
                for bundle in bundles
            ),
        )
    )


def plan_hash_entry(bundle: ReviewBundle, plan: ReviewPlan) -> str:
    """無効時互換性を保ち、M5 担当計画だけを追加してハッシュ化する."""
    parts = [
        str(bundle.bundle_id.sequence),
        hash_text(bundle.diff_text),
        plan.reviewer_type.value,
        plan.profile_reason_code,
    ]
    if plan.additional_reviewers or plan.selection_exclusion_reason is not None:
        parts.extend(
            (
                plan.selection_exclusion_reason or "SELECTED",
                *(
                    f"{item.reviewer_type.value},{item.reason_code},{item.score}"
                    for item in plan.additional_reviewers
                ),
            )
        )
    return ":".join(parts)


def bundle_input_hash(bundle: ReviewBundle, plan: ReviewPlan) -> str:
    """再開判定に使用するレビュー単位の固定入力ハッシュを返す."""
    parts = [
        "m3-bundle-input-v1",
        str(bundle.bundle_id.sequence),
        hash_text(bundle.diff_text),
        bundle.risk.value,
        plan.reviewer_type.value,
        plan.perspective,
        plan.profile_reason_code,
        str(plan.is_context_tool_enabled),
        str(plan.is_reflection_enabled),
        str(plan.budget.bundle_diff_tokens),
        str(plan.budget.rule_tokens),
        str(plan.budget.max_output_tokens),
        str(plan.budget.max_tool_calls),
        str(plan.budget.dynamic_context_tokens),
        str(plan.budget.reflection_tokens),
        *plan.applied_rule_ids,
        *(
            f"{binding.path.value}:{','.join(binding.rule_ids)}"
            for binding in plan.file_rule_bindings
        ),
    ]
    if (
        plan.is_specialist
        or plan.additional_reviewers
        or plan.selection_exclusion_reason is not None
    ):
        parts.extend(
            (
                str(plan.is_specialist),
                str(plan.profile_score),
                plan.selection_exclusion_reason or "",
                *(
                    f"{item.reviewer_type.value},{item.reason_code},{item.score}"
                    for item in plan.additional_reviewers
                ),
            )
        )
    return hash_lines(tuple(parts))


def outcome_hash(outcome: BundleOutcome) -> str:
    """本文を保存せずにレビュー単位結果を照合できるハッシュを返す."""
    return hash_lines(
        (
            outcome.status.value,
            outcome.error_code or "",
            *(str(finding.finding_id) for finding in outcome.findings),
        )
    )


def result_hash(outcomes: tuple[BundleOutcome, ...]) -> str:
    """レビュー単位順の結果ハッシュから実行結果ハッシュを返す."""
    return hash_lines(
        (
            "m3-result-v1",
            *(
                f"{outcome.bundle_id.sequence}:{outcome_hash(outcome)}"
                for outcome in sorted(outcomes, key=lambda item: item.bundle_id.sequence)
            ),
        )
    )
