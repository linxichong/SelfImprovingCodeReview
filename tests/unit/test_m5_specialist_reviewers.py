"""UT-M5-005/006: 専門担当選択と担当横断統合を検証する。"""

from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal

import pytest

from self_improving_code_review.application.review.bundler import SimpleBundler
from self_improving_code_review.application.review.models import (
    BundleOutcome,
    ReviewBudget,
    ReviewBundle,
    ReviewerAssignment,
    ReviewPlan,
)
from self_improving_code_review.application.review.reviewer_merge import (
    ReviewerResult,
    merge_reviewer_results,
)
from self_improving_code_review.application.review.specialist_reviewers import (
    build_specialist_plan,
    select_specialized_reviewers,
)
from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.common.enums import (
    BundleStatus,
    ReflectionStatus,
    ReviewerType,
    Severity,
    ValidationStatus,
)
from self_improving_code_review.domain.common.values import (
    BundleId,
    FindingId,
    LineRange,
    NormalizedPath,
    RunId,
)
from self_improving_code_review.domain.finding.models import (
    CandidateFinding,
    Evidence,
    Finding,
    ValidationOutcome,
)
from self_improving_code_review.domain.rule.models import ReviewRule, RuleScope
from self_improving_code_review.messages import Messages

RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")
BUNDLE_ID = BundleId(RUN_ID, 1)


@pytest.mark.case("UT-M5-005")
@pytest.mark.parametrize(
    ("kwargs", "expected_reason"),
    [
        ({"enabled": False}, "FEATURE_DISABLED"),
        ({"max_reviewer_profiles": 1}, "PROFILE_LIMIT"),
        ({}, "BELOW_THRESHOLD"),
    ],
)
def test_selection_records_deterministic_exclusion_reason(
    kwargs: dict[str, object],
    expected_reason: str,
) -> None:
    bundle = _bundle("src/app.py")
    selected = select_specialized_reviewers(
        _plan(),
        bundle,
        (),
        enabled=bool(kwargs.get("enabled", True)),
        max_reviewer_profiles=int(kwargs.get("max_reviewer_profiles", 2)),
        risk_threshold="high",
        specialist_total_tokens=6000,
        specialist_max_output_tokens=1000,
    )

    assert selected.additional_reviewers == ()
    assert selected.selection_exclusion_reason == expected_reason


@pytest.mark.case("UT-M5-005")
@pytest.mark.parametrize(
    ("path", "rule_data", "expected_type", "expected_reason"),
    [
        (
            "src/auth/login.py",
            None,
            ReviewerType.SECURITY,
            "SECURITY_BOUNDARY",
        ),
        (
            "migrations/0012_add_user.sql",
            None,
            ReviewerType.DATA,
            "DATA_INTEGRITY",
        ),
        (
            "tests/test_user.py",
            None,
            ReviewerType.TEST,
            "TEST_COVERAGE",
        ),
        (
            "src/app.py",
            ("SECURITY.INPUT", "security"),
            ReviewerType.SECURITY,
            "SECURITY_BOUNDARY",
        ),
        (
            "src/app.py",
            ("DATA.TRANSACTION", "data"),
            ReviewerType.DATA,
            "DATA_INTEGRITY",
        ),
        (
            "src/app.py",
            ("TEST.FAILURE_PATH", "test"),
            ReviewerType.TEST,
            "TEST_COVERAGE",
        ),
    ],
)
def test_selection_chooses_highest_scoring_profile(
    path: str,
    rule_data: tuple[str, str] | None,
    expected_type: ReviewerType,
    expected_reason: str,
) -> None:
    rules = (_rule(*rule_data),) if rule_data is not None else ()
    selected = _select(_bundle(path), rules)

    assert len(selected.additional_reviewers) == 1
    assignment = selected.additional_reviewers[0]
    assert assignment.reviewer_type is expected_type
    assert assignment.reason_code == expected_reason
    assert assignment.score >= 3
    assert selected.selection_exclusion_reason is None


@pytest.mark.case("UT-M5-005")
def test_selection_uses_security_data_test_tie_order() -> None:
    bundle = _bundle("src/auth/migrations/schema.py")

    selected = _select(bundle)

    assert selected.additional_reviewers[0].reviewer_type is ReviewerType.SECURITY


@pytest.mark.case("UT-M5-005")
def test_selection_does_not_duplicate_primary_profile() -> None:
    bundle = _bundle("src/auth/login.py")
    plan = replace(_plan(), reviewer_type=ReviewerType.SECURITY)

    selected = select_specialized_reviewers(
        plan,
        bundle,
        (),
        enabled=True,
        max_reviewer_profiles=2,
        risk_threshold="high",
        specialist_total_tokens=6000,
        specialist_max_output_tokens=1000,
    )

    assert selected.additional_reviewers == ()
    assert selected.selection_exclusion_reason == "BELOW_THRESHOLD"


@pytest.mark.case("UT-M5-005")
def test_selection_rejects_auxiliary_when_total_budget_is_insufficient() -> None:
    selected = select_specialized_reviewers(
        _plan(),
        _bundle("migrations/0012.sql"),
        (),
        enabled=True,
        max_reviewer_profiles=2,
        risk_threshold="high",
        specialist_total_tokens=100,
        specialist_max_output_tokens=1000,
    )

    assert selected.additional_reviewers == ()
    assert selected.selection_exclusion_reason == "TOKEN_BUDGET_EXCEEDED"


@pytest.mark.case("UT-M5-005")
def test_build_specialist_plan_limits_output_and_clears_nested_assignments() -> None:
    primary = replace(
        _plan(),
        additional_reviewers=(ReviewerAssignment(ReviewerType.DATA, "DATA_INTEGRITY", 6),),
    )

    specialist = build_specialist_plan(
        primary,
        primary.additional_reviewers[0],
        max_output_tokens=800,
    )

    assert specialist.reviewer_type is ReviewerType.DATA
    assert specialist.perspective == "data"
    assert specialist.profile_reason_code == "DATA_INTEGRITY"
    assert specialist.budget.max_output_tokens == 800
    assert specialist.additional_reviewers == ()
    assert specialist.is_specialist
    assert specialist.profile_score == 6


@pytest.mark.case("UT-M5-006")
def test_merge_requires_results_and_primary() -> None:
    with pytest.raises(ValueError, match=re.escape(Messages.REVIEWER_RESULTS_EMPTY)):
        merge_reviewer_results(())
    with pytest.raises(ValueError, match=re.escape(Messages.REVIEWER_PRIMARY_RESULT_MISSING)):
        merge_reviewer_results(
            (
                ReviewerResult(
                    ReviewerType.TEST,
                    "TEST_COVERAGE",
                    3,
                    _outcome(BundleStatus.SUCCESS),
                ),
            )
        )


@pytest.mark.case("UT-M5-006")
def test_merge_returns_primary_failure_without_findings_and_sums_usage() -> None:
    failed = _outcome(BundleStatus.FAILED, error_code="LlmClientError")
    auxiliary = _outcome(
        BundleStatus.SUCCESS,
        findings=(_finding(1, "A"),),
        input_tokens=20,
    )

    merged = merge_reviewer_results(
        (
            ReviewerResult(ReviewerType.GENERAL, "GENERAL_DEFAULT", 0, failed, True),
            ReviewerResult(ReviewerType.TEST, "TEST_COVERAGE", 3, auxiliary),
        )
    )

    assert merged.outcome.status is BundleStatus.FAILED
    assert merged.outcome.findings == ()
    assert merged.outcome.input_tokens == 30
    assert merged.sources == ()
    assert len(merged.executions) == 2


@pytest.mark.case("UT-M5-006")
def test_merge_keeps_all_sources_and_uses_profile_priority_for_equal_duplicates() -> None:
    general = _finding(1, "A", title="general")
    security = _finding(2, "A", title="security")

    merged = merge_reviewer_results(
        (
            ReviewerResult(
                ReviewerType.GENERAL,
                "GENERAL_DEFAULT",
                0,
                _outcome(BundleStatus.SUCCESS, findings=(general,)),
                True,
            ),
            ReviewerResult(
                ReviewerType.SECURITY,
                "SECURITY_BOUNDARY",
                3,
                _outcome(BundleStatus.SUCCESS, findings=(security,)),
            ),
        )
    )

    assert merged.outcome.status is BundleStatus.SUCCESS
    assert merged.outcome.findings[0].candidate.title == "security"
    assert {source.reviewer_type for source in merged.sources} == {
        ReviewerType.GENERAL,
        ReviewerType.SECURITY,
    }
    assert sum(source.is_selected for source in merged.sources) == 1
    executions = {item.reviewer_type: item for item in merged.executions}
    assert executions[ReviewerType.GENERAL].duplicate_count == 1
    assert executions[ReviewerType.SECURITY].valid_finding_count == 1


@pytest.mark.case("UT-M5-006")
def test_merge_prefers_severity_then_evidence_then_position_then_finding_id() -> None:
    invalid = ValidationOutcome(
        ValidationStatus.INVALID,
        ValidationStatus.INVALID,
        ReflectionStatus.REFLECTION_SKIPPED,
    )
    valid_evidence = ValidationOutcome(
        ValidationStatus.INVALID,
        ValidationStatus.VALID,
        ReflectionStatus.REFLECTION_SKIPPED,
    )
    valid_position = ValidationOutcome(
        ValidationStatus.VALID,
        ValidationStatus.VALID,
        ReflectionStatus.REFLECTION_SKIPPED,
    )
    findings = (
        _finding(4, "A", severity=Severity.LOW, title="low"),
        _finding(3, "A", validation=invalid, title="invalid"),
        _finding(2, "A", validation=valid_evidence, title="evidence"),
        _finding(1, "A", validation=valid_position, title="winner"),
    )
    results = tuple(
        ReviewerResult(
            reviewer,
            "REASON",
            1,
            _outcome(BundleStatus.SUCCESS, findings=(finding,)),
            is_primary=index == 0,
        )
        for index, (reviewer, finding) in enumerate(
            zip(
                (
                    ReviewerType.GENERAL,
                    ReviewerType.TEST,
                    ReviewerType.DATA,
                    ReviewerType.SECURITY,
                ),
                findings,
                strict=True,
            )
        )
    )

    merged = merge_reviewer_results(results)

    assert merged.outcome.findings[0].candidate.title == "winner"


@pytest.mark.case("UT-M5-006")
def test_auxiliary_failure_produces_partial_success_and_preserves_primary_findings() -> None:
    primary_finding = _finding(7, "B")

    merged = merge_reviewer_results(
        (
            ReviewerResult(
                ReviewerType.GENERAL,
                "GENERAL_DEFAULT",
                0,
                _outcome(BundleStatus.SUCCESS, findings=(primary_finding,)),
                True,
            ),
            ReviewerResult(
                ReviewerType.TEST,
                "TEST_COVERAGE",
                3,
                _outcome(BundleStatus.FAILED, error_code="InvalidLlmOutputError"),
            ),
        )
    )

    assert merged.outcome.status is BundleStatus.PARTIAL_SUCCESS
    assert merged.outcome.error_code == "SPECIALIST_REVIEW_FAILED"
    assert merged.outcome.findings[0].finding_id == FindingId(BUNDLE_ID, 1)


@pytest.mark.case("UT-M5-006")
def test_merge_orders_unique_findings_by_path_line_severity_and_fingerprint() -> None:
    later = _finding(1, "Z", path="src/z.py", line=5)
    first = _finding(2, "A", path="src/a.py", line=3)

    merged = merge_reviewer_results(
        (
            ReviewerResult(
                ReviewerType.GENERAL,
                "GENERAL_DEFAULT",
                0,
                _outcome(BundleStatus.SUCCESS, findings=(later, first)),
                True,
            ),
        )
    )

    assert [item.candidate.file.value for item in merged.outcome.findings] == [
        "src/a.py",
        "src/z.py",
    ]
    assert [item.finding_id.sequence for item in merged.outcome.findings] == [1, 2]


def _select(
    bundle: ReviewBundle,
    rules: tuple[ReviewRule, ...] = (),
) -> ReviewPlan:
    return select_specialized_reviewers(
        _plan(),
        bundle,
        rules,
        enabled=True,
        max_reviewer_profiles=2,
        risk_threshold="high",
        specialist_total_tokens=6000,
        specialist_max_output_tokens=1000,
    )


def _plan() -> ReviewPlan:
    return ReviewPlan(
        BUNDLE_ID,
        ReviewerType.GENERAL,
        "general",
        (),
        ReviewBudget(4000, 500, 1200),
    )


def _bundle(path: str) -> ReviewBundle:
    diff = (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1,2 @@\n"
        " value = 1\n"
        "+value = 2\n"
    )
    files = UnifiedDiffParser().parse(diff).files
    return SimpleBundler(max_diff_tokens=8000).bundle(RUN_ID, files).bundles[0]


def _rule(rule_id: str, category: str) -> ReviewRule:
    return ReviewRule(
        id=rule_id,
        category=category,
        severity=Severity.HIGH,
        scope=RuleScope(paths=("**/*",)),
        instruction="確認する。",
        source_path="rule.yaml",
        content_hash="hash",
    )


def _outcome(
    status: BundleStatus,
    *,
    findings: tuple[Finding, ...] = (),
    error_code: str | None = None,
    input_tokens: int = 10,
) -> BundleOutcome:
    return BundleOutcome(
        BUNDLE_ID,
        status,
        findings,
        error_code=error_code,
        input_tokens=input_tokens,
        output_tokens=2,
        latency_ms=3,
        cost_usd=Decimal("0.01"),
    )


def _finding(
    sequence: int,
    fingerprint: str,
    *,
    severity: Severity = Severity.MEDIUM,
    validation: ValidationOutcome | None = None,
    title: str = "issue",
    path: str = "src/app.py",
    line: int = 2,
) -> Finding:
    candidate = CandidateFinding(
        file=NormalizedPath(path),
        line_range=LineRange(line, line),
        category="correctness",
        issue_type="incorrect-result",
        rule_id="GENERAL.INCORRECT_RESULT",
        primary_entity="value",
        severity=severity,
        title=title,
        description="The result is incorrect.",
        evidence=Evidence("value = 2", "The value is wrong."),
        suggestion="Use the correct value.",
        needs_context=False,
    )
    return Finding(
        FindingId(BUNDLE_ID, sequence),
        BUNDLE_ID,
        candidate,
        fingerprint,
        validation
        or ValidationOutcome(
            ValidationStatus.VALID,
            ValidationStatus.VALID,
            ReflectionStatus.REFLECTION_SKIPPED,
        ),
        "module",
    )
