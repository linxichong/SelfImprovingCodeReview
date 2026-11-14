"""UT-M2-014〜UT-M2-019: 正解データ、照合、評価判定を検証する。"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from self_improving_code_review.application.evaluation.ground_truth import (
    GroundTruthService,
    PopulationRequirement,
)
from self_improving_code_review.application.evaluation.matcher import match_findings
from self_improving_code_review.application.evaluation.run_evaluation import EvaluationRunner
from self_improving_code_review.domain.common.enums import (
    EvaluationArm,
    GroundTruthStatus,
    MatchResult,
    MetricStatus,
    Permission,
    ReflectionStatus,
    Severity,
    ValidationStatus,
)
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import (
    BundleId,
    FindingId,
    GroundTruthIssueId,
    GroundTruthSetId,
    LineRange,
    NormalizedPath,
    RunId,
)
from self_improving_code_review.domain.evaluation.metrics import (
    MatchCounts,
    count_matches,
    critical_recall,
    precision,
    recall,
)
from self_improving_code_review.domain.evaluation.models import (
    GroundTruthCase,
    GroundTruthIssue,
    GroundTruthSet,
    MatchOutcome,
    compute_ground_truth_set_hash,
)
from self_improving_code_review.domain.finding.models import (
    CandidateFinding,
    Evidence,
    Finding,
    ValidationOutcome,
)
from self_improving_code_review.messages import Messages

NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
SET_ID = GroundTruthSetId("test", 1)
RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")


class AllowAuthorizer:
    """評価テストで権限要求を記録する。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Permission]] = []

    def require(self, actor_id: str, permission: Permission) -> None:
        self.calls.append((actor_id, permission))


class FixedClock:
    """一定の UTC 時刻を返す。"""

    def now_utc(self) -> datetime:
        return NOW


@pytest.mark.case("UT-M2-017")
def test_ground_truth_issue_validation_and_properties() -> None:
    line = _issue()
    symbol = _issue(sequence=2, start_line=None, symbol="load_user")

    assert line.is_approved
    assert line.locator == "line:10"
    assert symbol.locator == "symbol:load_user"
    assert not replace(line, status=GroundTruthStatus.DRAFT).is_approved

    for changes in (
        {"start_line": None, "symbol": None},
        {"start_line": 0},
        {"change_ref": ""},
        {"evidence": ""},
    ):
        with pytest.raises(InvalidInputError):
            replace(line, **changes)


@pytest.mark.case("IT-M2-018a", "UT-M2-017")
def test_ground_truth_case_validation_and_clean_case_hash() -> None:
    approved = GroundTruthCase(
        1,
        "change-clean",
        GroundTruthStatus.APPROVED,
        "author",
        "reviewer",
        "maintainer",
    )
    assert approved.is_approved
    assert not replace(approved, status=GroundTruthStatus.DRAFT).is_approved
    assert compute_ground_truth_set_hash((_issue(),), (approved,)) != (
        compute_ground_truth_set_hash((_issue(),))
    )
    with pytest.raises(InvalidInputError):
        replace(approved, sequence=0)
    with pytest.raises(InvalidInputError):
        replace(approved, change_ref="")


@pytest.mark.case("UT-M2-017")
def test_published_set_requires_auditable_approved_content() -> None:
    issue = _issue(status=GroundTruthStatus.PUBLISHED)
    published = GroundTruthSet(SET_ID, "hash", GroundTruthStatus.PUBLISHED, (issue,), "actor", NOW)

    assert published.critical_issues == (issue,)
    assert published.change_refs == frozenset({"change-1"})

    for changes in (
        {"approved_by": None},
        {"published_at": None},
        {"published_at": datetime(2026, 1, 2)},  # noqa: DTZ001 - naive time rejection
        {"issues": (replace(issue, status=GroundTruthStatus.DRAFT),)},
        {"cases": (GroundTruthCase(1, "change-1", GroundTruthStatus.DRAFT),)},
        {"cases": (GroundTruthCase(1, "other-change", GroundTruthStatus.PUBLISHED),)},
    ):
        with pytest.raises(InvalidInputError):
            replace(published, **changes)


@pytest.mark.case("UT-M2-017")
def test_set_hash_does_not_depend_on_registration_order() -> None:
    first = _issue(sequence=1)
    second = _issue(sequence=2, change_ref="change-2", start_line=20)

    assert compute_ground_truth_set_hash((first, second)) == compute_ground_truth_set_hash(
        (second, first)
    )


@pytest.mark.case("UT-M2-018")
def test_match_outcome_enforces_required_identifiers() -> None:
    finding_id = _finding().finding_id
    issue_id = _issue().issue_id
    MatchOutcome(MatchResult.TRUE_POSITIVE, finding_id, issue_id)
    MatchOutcome(MatchResult.FALSE_POSITIVE, finding_id=finding_id)
    MatchOutcome(MatchResult.FALSE_NEGATIVE, issue_id=issue_id)

    for outcome in (
        (MatchResult.TRUE_POSITIVE, finding_id, None),
        (MatchResult.DUPLICATE, None, issue_id),
        (MatchResult.FALSE_POSITIVE, None, issue_id),
        (MatchResult.FALSE_NEGATIVE, finding_id, None),
        (MatchResult.REVIEW_REQUIRED, None, None),
    ):
        with pytest.raises(InvalidInputError):
            MatchOutcome(outcome[0], outcome[1], outcome[2])


@pytest.mark.case("UT-M2-017")
def test_ground_truth_service_publishes_only_approved_issues() -> None:
    authorizer = AllowAuthorizer()
    service = GroundTruthService(authorizer=authorizer, clock=FixedClock())
    candidate = GroundTruthSet(
        SET_ID,
        "draft",
        GroundTruthStatus.DRAFT,
        (_issue(), _issue(sequence=2, status=GroundTruthStatus.DRAFT)),
    )

    published = service.publish(candidate, actor_id=" reviewer ")

    assert authorizer.calls == [(" reviewer ", Permission.PUBLISH_GROUND_TRUTH)]
    assert published.status is GroundTruthStatus.PUBLISHED
    assert published.approved_by == "reviewer"
    assert len(published.issues) == 1
    assert published.issues[0].status is GroundTruthStatus.PUBLISHED
    assert published.cases[0].status is GroundTruthStatus.PUBLISHED

    with pytest.raises(
        InvalidInputError, match=re.escape(Messages.GROUND_TRUTH_SET_ALREADY_PUBLISHED)
    ):
        service.publish(published, actor_id="reviewer")
    with pytest.raises(
        InvalidInputError, match=re.escape(Messages.GROUND_TRUTH_APPROVED_ISSUE_REQUIRED)
    ):
        service.publish(
            GroundTruthSet(
                GroundTruthSetId("test", 2),
                "draft",
                GroundTruthStatus.DRAFT,
                (_issue(status=GroundTruthStatus.DRAFT),),
            ),
            actor_id="reviewer",
        )

    explicit_case = GroundTruthCase(
        1,
        "change-1",
        GroundTruthStatus.APPROVED,
        "author",
        "reviewer",
        "maintainer",
    )
    explicit = service.publish(
        GroundTruthSet(
            GroundTruthSetId("test", 3),
            "draft",
            GroundTruthStatus.DRAFT,
            (_issue(),),
            cases=(explicit_case,),
        ),
        actor_id="reviewer",
    )
    assert explicit.change_refs == frozenset({"change-1"})
    with pytest.raises(
        InvalidInputError, match=re.escape(Messages.GROUND_TRUTH_ISSUE_CASE_NOT_APPROVED)
    ):
        service.publish(
            GroundTruthSet(
                GroundTruthSetId("test", 4),
                "draft",
                GroundTruthStatus.DRAFT,
                (_issue(),),
                cases=(replace(explicit_case, status=GroundTruthStatus.DRAFT),),
            ),
            actor_id="reviewer",
        )


@pytest.mark.case("IT-M2-020", "UT-M2-017")
def test_population_reports_all_unmet_fields_and_satisfied_boundary() -> None:
    empty = PopulationRequirement(0, 0, 0, 0)
    assert not empty.is_satisfied
    assert empty.unmet_requirements == (
        "change_ref_count",
        "issue_count",
        "critical_issue_count",
        "critical_change_ref_count",
    )
    assert PopulationRequirement(20, 30, 5, 3).is_satisfied

    service = GroundTruthService(authorizer=AllowAuthorizer(), clock=FixedClock())
    measured = service.measure_population((_issue(), _issue(sequence=2, severity=Severity.LOW)))
    assert measured == PopulationRequirement(1, 2, 1, 1)
    assert PopulationRequirement(20, 0, 5, 3).unmet_requirements == ("issue_count",)
    assert PopulationRequirement(0, 30, 5, 3).unmet_requirements == ("change_ref_count",)
    assert PopulationRequirement(20, 30, 0, 3).unmet_requirements == ("critical_issue_count",)
    assert PopulationRequirement(20, 30, 5, 0).unmet_requirements == ("critical_change_ref_count",)
    assert (
        service.measure_population((_issue(),), frozenset({"change-1", "clean"})).change_ref_count
        == 2
    )


@pytest.mark.case("UT-M2-018")
def test_matcher_classifies_true_positive_duplicate_review_and_false_negative() -> None:
    issues = (
        _issue(sequence=1, start_line=10),
        _issue(sequence=2, start_line=30, issue_type="sql-injection"),
    )
    findings = (
        _finding(sequence=1, start_line=8),
        _finding(sequence=2, start_line=11),
        _finding(sequence=3, start_line=50, issue_type="unknown"),
    )

    report = match_findings(findings, issues)

    assert tuple(item.result for item in report.outcomes) == (
        MatchResult.TRUE_POSITIVE,
        MatchResult.DUPLICATE,
        MatchResult.REVIEW_REQUIRED,
        MatchResult.FALSE_NEGATIVE,
    )


@pytest.mark.case("UT-M2-018")
def test_matcher_uses_rule_id_or_symbol_and_rejects_wrong_path() -> None:
    rule_issue = _issue(rule_id="RULE.1", issue_type="other")
    assert match_findings((_finding(rule_id="RULE.1"),), (rule_issue,)).matched_issue_ids

    symbol_issue = _issue(start_line=None, symbol="Load-User")
    assert match_findings(
        (_finding(enclosing_symbol="load_user"),), (symbol_issue,)
    ).matched_issue_ids

    wrong_path = _issue(file=NormalizedPath("src/other.py"))
    outcome = match_findings((_finding(),), (wrong_path,)).outcomes[0]
    assert outcome.result is MatchResult.REVIEW_REQUIRED


@pytest.mark.case("UT-M2-019")
def test_metrics_count_duplicates_and_report_not_evaluable() -> None:
    finding_id = _finding().finding_id
    issue_id = _issue().issue_id
    outcomes = (
        MatchOutcome(MatchResult.TRUE_POSITIVE, finding_id, issue_id),
        MatchOutcome(MatchResult.FALSE_POSITIVE, finding_id=finding_id),
        MatchOutcome(MatchResult.FALSE_NEGATIVE, issue_id=issue_id),
        MatchOutcome(MatchResult.DUPLICATE, finding_id, issue_id),
        MatchOutcome(MatchResult.REVIEW_REQUIRED, finding_id=finding_id),
    )
    counts = count_matches(outcomes)

    assert counts == MatchCounts(1, 2, 1, 1, 1)
    assert precision(counts).value == pytest.approx(1 / 3)
    assert recall(counts).value == 0.5
    assert critical_recall(counts).value == 0.5
    empty = precision(MatchCounts())
    assert empty.status is MetricStatus.NOT_EVALUABLE
    assert not empty.is_evaluable


@pytest.mark.case("UT-M2-019")
def test_runner_evaluates_arm_and_cases_and_rejects_invalid_repeat() -> None:
    runner = EvaluationRunner()
    issue = _issue()
    finding = _finding()

    report = runner.evaluate_arm(
        EvaluationArm.RULES,
        1,
        main_findings=(finding,),
        issues=(issue,),
        input_tokens=10,
        output_tokens=2,
        latency_ms=3,
        cost_usd=Decimal("0.1"),
    )
    cases = runner.evaluate_cases(EvaluationArm.MEMORY, 1, (((finding,), (issue,)),))

    assert report.counts.true_positive == 1
    assert report.critical_issue_ids == frozenset({str(issue.issue_id)})
    assert len(report.outcomes) == 3
    assert cases.matches[0].result is MatchResult.TRUE_POSITIVE
    with pytest.raises(InvalidInputError):
        runner.evaluate_arm(EvaluationArm.RULES, 0, main_findings=(), issues=())
    with pytest.raises(InvalidInputError):
        runner.evaluate_cases(EvaluationArm.RULES, 0, ())


@pytest.mark.case("IT-M2-019", "UT-M2-020")
def test_comparison_accepts_improvement_and_reports_regressions() -> None:
    runner = EvaluationRunner()
    issue = _issue()
    true_positive = runner.evaluate_arm(
        EvaluationArm.RULES,
        1,
        main_findings=(_finding(),),
        issues=(issue,),
        cost_usd=Decimal("0.3"),
    )
    rules = tuple(
        replace(true_positive, repeat=index, counts=MatchCounts(1, 1, 0)) for index in range(1, 4)
    )
    memory = tuple(
        replace(true_positive, arm=EvaluationArm.MEMORY, repeat=index, cost_usd=Decimal("0.2"))
        for index in range(1, 4)
    )

    assert runner.compare(rules, memory).is_successful

    regressed = tuple(
        replace(
            item,
            critical_issue_ids=frozenset(),
            recall=replace(item.recall, value=0.5),
            critical_recall=replace(item.critical_recall, value=0.5),
            counts=MatchCounts(1, 1, 1, review_required=1),
            cost_usd=Decimal("1"),
        )
        for item in memory[:2]
    )
    verdict = runner.compare(rules, regressed)
    assert not verdict.is_successful
    assert "INSUFFICIENT_REPEATS" in verdict.reason_codes
    assert "CRITICAL_REGRESSION" in verdict.reason_codes
    assert "RECALL_RETENTION_BELOW_95_PERCENT" in verdict.reason_codes
    assert "CRITICAL_RECALL_REGRESSION" in verdict.reason_codes
    assert "FALSE_POSITIVE_REDUCTION_BELOW_30_PERCENT" in verdict.reason_codes
    assert "COST_PER_VALID_FINDING_REGRESSION" in verdict.reason_codes
    assert "ADJUDICATION_REQUIRED" in verdict.reason_codes


@pytest.mark.case("IT-M2-020", "UT-M2-020")
def test_comparison_reports_each_not_evaluable_reason() -> None:
    verdict = EvaluationRunner().compare((), ())
    assert set(verdict.reason_codes) == {
        "INSUFFICIENT_REPEATS",
        "CRITICAL_RETENTION_NOT_EVALUABLE",
        "NOT_EVALUABLE",
        "FALSE_POSITIVE_REDUCTION_NOT_EVALUABLE",
        "COST_PER_VALID_FINDING_NOT_EVALUABLE",
    }


@pytest.mark.case("UT-M2-020")
@pytest.mark.parametrize(
    ("rules_ids", "memory_ids", "expected_lost"),
    [
        (({"a"}, {"a", "b"}, {"a"}), ({"a"}, {"a"}, {"a"}), ("b",)),
        (({"a"}, {"a", "b"}, {"a"}), ({"a", "b"}, {"a"}, {"a", "b"}), ("b",)),
        (({"a"}, {"b"}, {"a", "b"}), ({"a", "b"}, {"a", "b"}, {"a", "b"}), ()),
    ],
)
def test_critical_retention_includes_every_issue_detected_by_any_rules_repeat(
    rules_ids: tuple[set[str], ...],
    memory_ids: tuple[set[str], ...],
    expected_lost: tuple[str, ...],
) -> None:
    """B の検出揺らぎで重大問題が維持判定の母集団から消えないことを検証する。"""
    runner = EvaluationRunner()
    base = runner.evaluate_arm(
        EvaluationArm.RULES, 1, main_findings=(_finding(),), issues=(_issue(),)
    )
    rules = tuple(
        replace(base, repeat=index, counts=MatchCounts(1, 1, 0), critical_issue_ids=frozenset(ids))
        for index, ids in enumerate(rules_ids, 1)
    )
    memory = tuple(
        replace(base, arm=EvaluationArm.MEMORY, repeat=index, critical_issue_ids=frozenset(ids))
        for index, ids in enumerate(memory_ids, 1)
    )
    verdict = runner.compare(rules, memory)
    assert verdict.lost_critical_issue_ids == expected_lost
    assert ("CRITICAL_REGRESSION" in verdict.reason_codes) is bool(expected_lost)
    assert "CRITICAL_RETENTION_NOT_EVALUABLE" not in verdict.reason_codes
    assert verdict.is_successful is not bool(expected_lost)


def test_evaluation_private_aggregators_cover_union_and_non_evaluable_value() -> None:
    from self_improving_code_review.application.evaluation import run_evaluation
    from self_improving_code_review.domain.evaluation.metrics import MetricValue

    report = EvaluationRunner().evaluate_arm(EvaluationArm.RULES, 1, main_findings=(), issues=())
    assert run_evaluation._union_critical(
        (replace(report, critical_issue_ids=frozenset({"a"})),)
    ) == frozenset({"a"})
    assert run_evaluation._mean((MetricValue("metric", 0, 0, MetricStatus.NOT_EVALUABLE),)) is None


def _issue(**overrides: object) -> GroundTruthIssue:
    values: dict[str, object] = {
        "issue_id": GroundTruthIssueId(SET_ID, int(overrides.pop("sequence", 1))),
        "change_ref": "change-1",
        "file": NormalizedPath("src/app.py"),
        "issue_type": "null-dereference",
        "severity": Severity.CRITICAL,
        "evidence": "value may be null",
        "status": GroundTruthStatus.APPROVED,
        "start_line": 10,
    }
    values.update(overrides)
    return GroundTruthIssue(**values)  # type: ignore[arg-type]


def _finding(**overrides: object) -> Finding:
    sequence = int(overrides.pop("sequence", 1))
    start_line = int(overrides.pop("start_line", 10))
    issue_type = str(overrides.pop("issue_type", "null-dereference"))
    rule_id = str(overrides.pop("rule_id", "GENERAL.NULL_DEREFERENCE"))
    enclosing_symbol = str(overrides.pop("enclosing_symbol", "load_user"))
    bundle_id = BundleId(RUN_ID, 1)
    candidate = CandidateFinding(
        file=NormalizedPath("src/app.py"),
        line_range=LineRange(start_line, start_line),
        category="correctness",
        issue_type=issue_type,
        rule_id=rule_id,
        primary_entity="value",
        severity=Severity.CRITICAL,
        title="Possible null access",
        description="Value may be null",
        evidence=Evidence("value.name", "No guard"),
        suggestion="Add a guard",
        needs_context=False,
    )
    return Finding(
        FindingId(bundle_id, sequence),
        bundle_id,
        candidate,
        f"fingerprint-{sequence}",
        ValidationOutcome(
            ValidationStatus.VALID,
            ValidationStatus.VALID,
            ReflectionStatus.REFLECTION_SKIPPED,
        ),
        enclosing_symbol,
    )
