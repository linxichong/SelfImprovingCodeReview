from __future__ import annotations

import re

import pytest

from self_improving_code_review.application.config.settings import ReviewConfig
from self_improving_code_review.application.review.bundler import SimpleBundler
from self_improving_code_review.application.review.planner import RuleBasedReviewPlanner
from self_improving_code_review.application.review.risk import classify_risk
from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.common.enums import (
    ReviewerType,
    ReviewMode,
    RiskLevel,
    Severity,
)
from self_improving_code_review.domain.common.values import RunId
from self_improving_code_review.domain.rule.models import ReviewRule, RuleScope
from self_improving_code_review.messages import Messages

RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")


@pytest.mark.case("UT-M1-008")
def test_empty_bundle_is_classified_as_medium_general_risk() -> None:
    assessment = classify_risk(())

    assert assessment.risk is RiskLevel.MEDIUM
    assert assessment.perspective == "general"


@pytest.mark.case("UT-M1-008")
@pytest.mark.parametrize(
    ("path", "added", "expected_risk", "expected_perspective"),
    [
        ("src/auth/login.py", "value = 1", RiskLevel.HIGH, "security"),
        ("src/app.py", "token = issue()", RiskLevel.HIGH, "security"),
        ("src/server.py", "app.run(debug=True)", RiskLevel.HIGH, "security"),
        ("src/server.py", "app.run(debug = False)", RiskLevel.HIGH, "security"),
        ("src/server.py", "PROPAGATE_EXCEPTIONS = True", RiskLevel.HIGH, "security"),
        ("src/feed.py", "XMLParser(resolve_entities=True)", RiskLevel.HIGH, "security"),
        ("src/feed.py", "XMLParser(resolve_entities = False)", RiskLevel.HIGH, "security"),
        ("migrations/0002.sql", "value = 1", RiskLevel.HIGH, "security-data"),
        ("src/app.py", "subprocess.run(cmd)", RiskLevel.HIGH, "security-data"),
        ("src/api/users.py", "value = 1", RiskLevel.MEDIUM, "api-validation"),
        ("src/handler.py", "@router.get('/x')", RiskLevel.MEDIUM, "api-validation"),
        ("tests/test_app.py", "value = 1", RiskLevel.LOW, "test-regression"),
        ("src/app.py", "value = 1", RiskLevel.MEDIUM, "general"),
    ],
)
def test_highest_matching_category_determines_risk(
    path: str,
    added: str,
    expected_risk: RiskLevel,
    expected_perspective: str,
) -> None:
    files = UnifiedDiffParser().parse(_diff(path, added=added)).files

    assessment = classify_risk(files)

    assert assessment.risk is expected_risk
    assert assessment.perspective == expected_perspective


@pytest.mark.case("UT-M1-008")
def test_mixed_test_and_security_change_is_high_risk() -> None:
    files = (
        UnifiedDiffParser().parse(_diff("tests/test_login.py") + _diff("src/auth/login.py")).files
    )

    assert classify_risk(files).risk is RiskLevel.HIGH


@pytest.mark.case("UT-M1-008")
@pytest.mark.parametrize(
    ("path", "expected_rule_tokens"),
    [
        ("src/auth/login.py", 800),
        ("src/app.py", 600),
        ("tests/test_app.py", 400),
    ],
)
def test_rule_token_budget_scales_with_risk(path: str, expected_rule_tokens: int) -> None:
    bundle = _bundle(path)
    config = ReviewConfig(rule_tokens=800)

    plan, _rules = RuleBasedReviewPlanner(config).plan(bundle, (), ReviewMode.RULES)

    assert plan.budget.rule_tokens == expected_rule_tokens
    assert plan.budget.bundle_diff_tokens == config.bundle_diff_tokens
    assert plan.budget.max_output_tokens == config.max_output_tokens
    assert plan.budget.max_tool_calls == 0


@pytest.mark.case("IT-M1-004", "UT-M1-008")
def test_m1_plan_uses_general_reviewer_without_reflection_or_tools() -> None:
    plan, _rules = RuleBasedReviewPlanner(ReviewConfig()).plan(
        _bundle("src/app.py"), (), ReviewMode.RULES
    )

    assert plan.reviewer_type is ReviewerType.GENERAL
    assert not plan.is_reflection_enabled
    assert not plan.is_context_tool_enabled


@pytest.mark.case("UT-M3-003")
@pytest.mark.parametrize(
    ("path", "added", "expected_reviewer", "expected_reason"),
    [
        ("src/auth/login.py", "value = 1", ReviewerType.SECURITY, "SECURITY_RISK"),
        ("src/server.py", "app.run(debug=True)", ReviewerType.SECURITY, "SECURITY_RISK"),
        ("src/feed.py", "XMLParser(resolve_entities=True)", ReviewerType.SECURITY, "SECURITY_RISK"),
        ("tests/test_app.py", "value = 1", ReviewerType.TEST, "TEST_ONLY"),
        ("src/app.py", "value = 1", ReviewerType.GENERAL, "GENERAL_DEFAULT"),
    ],
)
def test_m3_plan_routes_specialists_in_priority_order(
    path: str,
    added: str,
    expected_reviewer: ReviewerType,
    expected_reason: str,
) -> None:
    files = UnifiedDiffParser().parse(_diff(path, added=added)).files
    bundle = SimpleBundler(max_diff_tokens=8000).bundle(RUN_ID, files).bundles[0]
    config = ReviewConfig(context_tools_enabled=True)

    plan, _rules = RuleBasedReviewPlanner(config).plan(bundle, (), ReviewMode.RULES)

    assert plan.reviewer_type is expected_reviewer
    assert plan.profile_reason_code == expected_reason
    assert plan.budget.max_tool_calls == config.max_tool_calls
    assert plan.budget.dynamic_context_tokens == config.dynamic_context_tokens


def test_review_config_rejects_enabled_m3_features_without_budget() -> None:
    with pytest.raises(ValueError, match=re.escape(Messages.CONFIG_CONTEXT_BUDGET_REQUIRED)):
        ReviewConfig(context_tools_enabled=True, max_tool_calls=0)
    with pytest.raises(ValueError, match=re.escape(Messages.CONFIG_REFLECTION_BUDGET_REQUIRED)):
        ReviewConfig(reflection_enabled=True, reflection_tokens=0)


@pytest.mark.case("IT-M1-004", "UT-M1-008")
def test_baseline_mode_injects_no_rule() -> None:
    bundle = _bundle("src/app.py")
    rules = (_rule(),)

    baseline_plan, baseline_rules = RuleBasedReviewPlanner(ReviewConfig()).plan(
        bundle, rules, ReviewMode.BASELINE
    )
    rules_plan, applicable_rules = RuleBasedReviewPlanner(ReviewConfig()).plan(
        bundle, rules, ReviewMode.RULES
    )

    assert baseline_rules == ()
    assert baseline_plan.applied_rule_ids == ()
    assert baseline_plan.file_rule_bindings == ()
    assert applicable_rules == rules
    assert rules_plan.applied_rule_ids == ("PYTHON.NULL_DEREFERENCE",)
    assert [
        (binding.path.value, binding.rule_ids) for binding in rules_plan.file_rule_bindings
    ] == [("src/app.py", ("PYTHON.NULL_DEREFERENCE",))]


def _bundle(path: str) -> object:
    files = UnifiedDiffParser().parse(_diff(path)).files
    return SimpleBundler(max_diff_tokens=8000).bundle(RUN_ID, files).bundles[0]


def _rule() -> ReviewRule:
    return ReviewRule(
        id="PYTHON.NULL_DEREFERENCE",
        category="correctness",
        severity=Severity.HIGH,
        scope=RuleScope(paths=("**/*.py",)),
        instruction="Check the nullable value.",
        source_path="rule.yaml",
        content_hash="hash",
    )


def _diff(path: str, *, added: str = "value = 2") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1,2 @@\n"
        " value = 1\n"
        f"+{added}\n"
    )
