from __future__ import annotations

import pytest

from self_improving_code_review.application.review.review_manager import ReviewManager
from self_improving_code_review.domain.common.enums import (
    ReflectionStatus,
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

RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")
BUNDLE_ID = BundleId(run_id=RUN_ID, sequence=1)


@pytest.mark.case("IT-M1-010", "UT-M1-010")
def test_empty_input_aggregates_to_empty_lists() -> None:
    aggregation = ReviewManager().aggregate(())

    assert aggregation.main_findings == ()
    assert aggregation.reference_findings == ()
    assert aggregation.duplicates == ()
    assert aggregation.invalid_position_count == 0
    assert aggregation.invalid_evidence_count == 0


@pytest.mark.case("UT-M1-010")
def test_main_findings_are_sorted_by_severity_then_path_then_line() -> None:
    low = _finding(1, severity=Severity.LOW, path="src/a.py", start_line=2, primary_entity="w")
    high_b = _finding(2, severity=Severity.HIGH, path="src/b.py", start_line=2, primary_entity="x")
    high_a_late = _finding(
        3, severity=Severity.HIGH, path="src/a.py", start_line=9, primary_entity="y"
    )
    high_a_early = _finding(
        4, severity=Severity.HIGH, path="src/a.py", start_line=2, primary_entity="z"
    )

    aggregation = ReviewManager().aggregate((low, high_b, high_a_late, high_a_early))

    assert [str(item.finding_id) for item in aggregation.main_findings] == [
        str(high_a_early.finding_id),
        str(high_a_late.finding_id),
        str(high_b.finding_id),
        str(low.finding_id),
    ]


@pytest.mark.case("UT-M1-010")
def test_needs_context_findings_go_to_the_reference_list() -> None:
    main = _finding(1)
    reference = _finding(2, reflection=ReflectionStatus.NEEDS_CONTEXT, path="src/b.py")

    aggregation = ReviewManager().aggregate((main, reference))

    assert [str(item.finding_id) for item in aggregation.main_findings] == [str(main.finding_id)]
    assert [str(item.finding_id) for item in aggregation.reference_findings] == [
        str(reference.finding_id)
    ]


@pytest.mark.case("UT-M1-010")
def test_invalid_findings_are_counted_and_excluded_from_both_lists() -> None:
    invalid_position = _finding(1, position=ValidationStatus.INVALID)
    invalid_evidence = _finding(2, evidence=ValidationStatus.INVALID, path="src/b.py")

    aggregation = ReviewManager().aggregate((invalid_position, invalid_evidence))

    assert aggregation.main_findings == ()
    assert aggregation.reference_findings == ()
    assert aggregation.invalid_position_count == 1
    assert aggregation.invalid_evidence_count == 1


@pytest.mark.case("UT-M1-010")
def test_a_finding_invalid_on_both_axes_is_counted_once_per_axis() -> None:
    finding = _finding(
        1,
        position=ValidationStatus.INVALID,
        evidence=ValidationStatus.INVALID,
    )

    aggregation = ReviewManager().aggregate((finding,))

    assert aggregation.invalid_position_count == 1
    assert aggregation.invalid_evidence_count == 1


@pytest.mark.case("UT-M1-010")
def test_duplicates_are_removed_before_classification() -> None:
    first = _finding(1, severity=Severity.LOW)
    duplicate = _finding(2, severity=Severity.CRITICAL)

    aggregation = ReviewManager().aggregate((first, duplicate))

    assert len(aggregation.main_findings) == 1
    assert aggregation.main_findings[0].severity is Severity.CRITICAL
    assert [str(item.finding_id) for item in aggregation.duplicates] == [str(first.finding_id)]


@pytest.mark.case("UT-M1-010")
def test_same_severity_path_and_line_are_ordered_by_fingerprint() -> None:
    first = _finding(1, primary_entity="zeta")
    second = _finding(2, primary_entity="alpha")

    aggregation = ReviewManager().aggregate((first, second))

    assert [item.fingerprint for item in aggregation.main_findings] == sorted(
        [first.fingerprint, second.fingerprint]
    )


def _finding(
    sequence: int,
    *,
    severity: Severity = Severity.HIGH,
    path: str = "src/a.py",
    start_line: int = 2,
    primary_entity: str = "user",
    position: ValidationStatus = ValidationStatus.VALID,
    evidence: ValidationStatus = ValidationStatus.VALID,
    reflection: ReflectionStatus = ReflectionStatus.REFLECTION_SKIPPED,
) -> Finding:
    candidate = CandidateFinding(
        file=NormalizedPath(path),
        line_range=LineRange(start_line, start_line),
        category="correctness",
        issue_type="null-dereference",
        rule_id="PYTHON.NULL_DEREFERENCE",
        primary_entity=primary_entity,
        severity=severity,
        title="Guard nullable user",
        description="The value can be None.",
        evidence=Evidence(quote="user.name", reason="None reaches this line."),
        suggestion="Return before dereferencing user.",
        needs_context=reflection is ReflectionStatus.NEEDS_CONTEXT,
    )
    return Finding(
        finding_id=FindingId(bundle_id=BUNDLE_ID, sequence=sequence),
        bundle_id=BUNDLE_ID,
        candidate=candidate,
        fingerprint=f"PYTHON.NULL_DEREFERENCE:{path}:load:{primary_entity}",
        validation=ValidationOutcome(
            position_status=position,
            evidence_status=evidence,
            reflection_status=reflection,
        ),
        enclosing_symbol="load",
    )
