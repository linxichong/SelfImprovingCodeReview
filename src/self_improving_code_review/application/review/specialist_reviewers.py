"""M5 の専門レビュー担当を決定的に選択する."""

from __future__ import annotations

from dataclasses import replace

from self_improving_code_review.application.review.models import (
    ReviewBundle,
    ReviewerAssignment,
    ReviewPlan,
)
from self_improving_code_review.domain.common.enums import ReviewerType, RiskLevel
from self_improving_code_review.domain.rule.models import ReviewRule

__all__ = ["select_specialized_reviewers"]

_RISK_THRESHOLDS: dict[str, int] = {"low": 1, "medium": 2, "high": 3}
_PROFILE_PRIORITY: dict[ReviewerType, int] = {
    ReviewerType.SECURITY: 0,
    ReviewerType.DATA: 1,
    ReviewerType.TEST: 2,
}
_SECURITY_PATH_MARKERS = (
    "auth/",
    "security/",
    "permission",
    "credential",
    "secret",
)
_DATA_PATH_MARKERS = (
    "migration",
    "schema",
    "persistence",
    "repository",
    "database",
    "transaction",
)
_TEST_PATH_MARKERS = ("tests/", "test/", "spec/")
_FAILURE_RULE_MARKERS = ("FAIL", "ERROR", "EXCEPTION", "ROLLBACK")


def select_specialized_reviewers(
    plan: ReviewPlan,
    bundle: ReviewBundle,
    rules: tuple[ReviewRule, ...],
    *,
    enabled: bool,
    max_reviewer_profiles: int,
    risk_threshold: str,
    specialist_total_tokens: int,
    specialist_max_output_tokens: int,
) -> ReviewPlan:
    """主担当を保持し、条件を満たす補助担当を最大一件追加する."""
    if not enabled:
        return replace(plan, selection_exclusion_reason="FEATURE_DISABLED")
    if max_reviewer_profiles < 2:
        return replace(plan, selection_exclusion_reason="PROFILE_LIMIT")

    candidates = tuple(
        assignment
        for assignment in _score_candidates(bundle, rules)
        if assignment.reviewer_type is not plan.reviewer_type
    )
    threshold = _RISK_THRESHOLDS[risk_threshold]
    eligible = tuple(item for item in candidates if item.score >= threshold)
    if not eligible:
        return replace(plan, selection_exclusion_reason="BELOW_THRESHOLD")

    selected = min(
        eligible,
        key=lambda item: (-item.score, _PROFILE_PRIORITY[item.reviewer_type]),
    )
    required_tokens = bundle.diff_tokens + plan.budget.rule_tokens + specialist_max_output_tokens
    if required_tokens > specialist_total_tokens:
        return replace(plan, selection_exclusion_reason="TOKEN_BUDGET_EXCEEDED")
    return replace(
        plan,
        additional_reviewers=(selected,),
        selection_exclusion_reason=None,
    )


def build_specialist_plan(
    primary_plan: ReviewPlan,
    assignment: ReviewerAssignment,
    *,
    max_output_tokens: int,
) -> ReviewPlan:
    """主担当計画から補助担当専用の独立計画を作成する."""
    return replace(
        primary_plan,
        reviewer_type=assignment.reviewer_type,
        perspective=assignment.reviewer_type.value,
        profile_reason_code=assignment.reason_code,
        additional_reviewers=(),
        selection_exclusion_reason=None,
        is_specialist=True,
        profile_score=assignment.score,
        budget=replace(
            primary_plan.budget,
            max_output_tokens=min(
                primary_plan.budget.max_output_tokens,
                max_output_tokens,
            ),
        ),
    )


def _score_candidates(
    bundle: ReviewBundle,
    rules: tuple[ReviewRule, ...],
) -> tuple[ReviewerAssignment, ...]:
    """許可済みメタデータだけから三種類の補助担当候補を採点する."""
    paths = tuple(path.lower() for path in bundle.paths)
    categories = tuple(rule.category.lower() for rule in rules)
    rule_ids = tuple(rule.id.upper() for rule in rules)
    has_data_path = _path_matches(paths, _DATA_PATH_MARKERS)

    security_score = (
        (
            3
            if bundle.risk is RiskLevel.HIGH
            and not has_data_path
            and not any(_is_test_path(path) for path in paths)
            else 0
        )
        + (3 if _path_matches(paths, _SECURITY_PATH_MARKERS) else 0)
        + (
            3
            if any(category == "security" for category in categories)
            or any(rule_id.startswith("SECURITY.") for rule_id in rule_ids)
            else 0
        )
    )
    data_score = (3 if has_data_path else 0) + (
        3
        if any(category in {"data", "database", "persistence"} for category in categories)
        or any(rule_id.startswith(("DATA.", "DATABASE.", "PERSISTENCE.")) for rule_id in rule_ids)
        else 0
    )
    has_test = any(_is_test_path(path) for path in paths)
    has_production = any(not _is_test_path(path) for path in paths)
    test_score = (3 if has_test and not has_production else 2 if has_test else 0) + (
        3
        if any(category == "test" for category in categories)
        or any(marker in rule_id for marker in _FAILURE_RULE_MARKERS for rule_id in rule_ids)
        else 0
    )
    return (
        ReviewerAssignment(ReviewerType.SECURITY, "SECURITY_BOUNDARY", security_score),
        ReviewerAssignment(ReviewerType.DATA, "DATA_INTEGRITY", data_score),
        ReviewerAssignment(ReviewerType.TEST, "TEST_COVERAGE", test_score),
    )


def _path_matches(paths: tuple[str, ...], markers: tuple[str, ...]) -> bool:
    """いずれかのパスが分類用マーカーを含む場合は True を返す."""
    return any(marker in path for path in paths for marker in markers)


def _is_test_path(path: str) -> bool:
    """パスが一般的なテスト配置または命名の場合は True を返す."""
    name = path.rsplit("/", maxsplit=1)[-1]
    stem = name.rsplit(".", maxsplit=1)[0]
    return (
        path.startswith(_TEST_PATH_MARKERS)
        or "/tests/" in path
        or stem.startswith("test_")
        or stem.endswith(("_test", ".test", ".spec"))
    )
