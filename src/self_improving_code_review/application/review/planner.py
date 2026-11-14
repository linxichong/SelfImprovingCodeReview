"""ルールベースのレビュー計画.

計画処理はルールと予算だけを選択する. LLM を呼び出さないため、
差分と固定済みルールスナップショットだけから計画を再現できる.
"""

from __future__ import annotations

from self_improving_code_review.application.config.settings import ReviewConfig
from self_improving_code_review.application.review.models import (
    ReviewBudget,
    ReviewBundle,
    ReviewPlan,
)
from self_improving_code_review.application.review.risk import classify_risk
from self_improving_code_review.domain.common.enums import ReviewerType, ReviewMode, RiskLevel
from self_improving_code_review.domain.rule.matcher import RuleMatcher
from self_improving_code_review.domain.rule.models import ReviewRule

__all__ = ["RuleBasedReviewPlanner"]

_RULE_TOKEN_MULTIPLIERS: dict[RiskLevel, float] = {
    RiskLevel.HIGH: 1.0,
    RiskLevel.MEDIUM: 0.75,
    RiskLevel.LOW: 0.5,
}


class RuleBasedReviewPlanner:
    """1 つのレビュー単位に対して決定的な計画を作成する."""

    def __init__(self, config: ReviewConfig, matcher: RuleMatcher | None = None) -> None:
        """予算設定と適用範囲照合処理を保持する.

        Args:
            config: レビュー予算と上限.
            matcher: ルール適用範囲照合処理. 既定値は :class:`RuleMatcher`.
        """
        self._config = config
        self._matcher = matcher or RuleMatcher()

    def plan(
        self,
        bundle: ReviewBundle,
        rules: tuple[ReviewRule, ...],
        mode: ReviewMode,
    ) -> tuple[ReviewPlan, tuple[ReviewRule, ...]]:
        """``bundle`` に対する計画と適用可能なルールを返す.

        Args:
            bundle: 計画対象のレビュー単位.
            rules: 実行スナップショットに固定されたルール.
            mode: レビューモード. ``BASELINE`` はルール注入を無効化する.

        Returns:
            プロンプトへ構築する計画とルール.
        """
        assessment = classify_risk(bundle.files)
        resolution = self._matcher.resolve(rules, bundle.files)
        applicable = () if mode is ReviewMode.BASELINE else resolution.rules
        file_rule_bindings = () if mode is ReviewMode.BASELINE else resolution.file_bindings
        multiplier = _RULE_TOKEN_MULTIPLIERS[assessment.risk]
        budget = ReviewBudget(
            bundle_diff_tokens=self._config.bundle_diff_tokens,
            rule_tokens=int(self._config.rule_tokens * multiplier),
            max_output_tokens=self._config.max_output_tokens,
            max_tool_calls=(
                self._config.max_tool_calls if self._config.context_tools_enabled else 0
            ),
            dynamic_context_tokens=(
                self._config.dynamic_context_tokens if self._config.context_tools_enabled else 0
            ),
            reflection_tokens=(
                self._config.reflection_tokens if self._config.reflection_enabled else 0
            ),
        )
        is_m3_enabled = (
            self._config.context_tools_enabled
            or self._config.reflection_enabled
            or self._config.max_workers > 1
        )
        reviewer_type, reason_code = (
            _select_reviewer_type(
                assessment.risk,
                assessment.perspective,
                bundle,
                applicable,
            )
            if is_m3_enabled
            else (ReviewerType.GENERAL, "GENERAL_DEFAULT")
        )
        plan = ReviewPlan(
            bundle_id=bundle.bundle_id,
            reviewer_type=reviewer_type,
            perspective=assessment.perspective,
            applied_rule_ids=tuple(rule.id for rule in applicable),
            budget=budget,
            is_reflection_enabled=self._config.reflection_enabled,
            is_context_tool_enabled=self._config.context_tools_enabled,
            file_rule_bindings=file_rule_bindings,
            profile_reason_code=reason_code,
        )
        return plan, applicable


def _select_reviewer_type(
    risk: RiskLevel,
    perspective: str,
    bundle: ReviewBundle,
    rules: tuple[ReviewRule, ...],
) -> tuple[ReviewerType, str]:
    """セキュリティ、テスト、一般の優先順で担当種別を返す."""
    has_security_rule = any(
        rule.category.lower() == "security"
        or rule.id.startswith("SECURITY.")
        or ".SECURITY_" in rule.id
        for rule in rules
    )
    if risk is RiskLevel.HIGH or perspective.startswith("security") or has_security_rule:
        reason = "SECURITY_RULE" if has_security_rule else "SECURITY_RISK"
        return ReviewerType.SECURITY, reason
    if bundle.files and all(_is_test_path(item.new_path.value) for item in bundle.files):
        return ReviewerType.TEST, "TEST_ONLY"
    return ReviewerType.GENERAL, "GENERAL_DEFAULT"


def _is_test_path(path: str) -> bool:
    """パスが一般的なテスト配置または命名の場合は True を返す."""
    lowered = path.lower()
    name = lowered.rsplit("/", maxsplit=1)[-1]
    stem = name.rsplit(".", maxsplit=1)[0]
    return (
        lowered.startswith(("tests/", "test/", "spec/"))
        or "/tests/" in lowered
        or stem.startswith("test_")
        or stem.endswith(("_test", ".test", ".spec"))
    )
