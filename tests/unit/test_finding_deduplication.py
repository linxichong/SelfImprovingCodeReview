from __future__ import annotations

import pytest

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
from self_improving_code_review.domain.finding.deduplication import deduplicate
from self_improving_code_review.domain.finding.models import (
    CandidateFinding,
    Evidence,
    Finding,
    ValidationOutcome,
)

RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")
BUNDLE_ID = BundleId(run_id=RUN_ID, sequence=1)


@pytest.mark.case("UT-M1-010")
def test_unique_fingerprints_are_all_kept_in_input_order() -> None:
    first = _finding(1, fingerprint="A")
    second = _finding(2, fingerprint="B")

    kept, losers = deduplicate((first, second))

    assert kept == (first, second)
    assert losers == ()


@pytest.mark.case("UT-M1-010")
def test_higher_severity_replaces_the_incumbent_at_its_first_position() -> None:
    low = _finding(1, fingerprint="A", severity=Severity.LOW)
    other = _finding(2, fingerprint="B")
    critical = _finding(3, fingerprint="A", severity=Severity.CRITICAL)

    kept, losers = deduplicate((low, other, critical))

    assert kept == (critical, other)
    assert losers == (low,)


@pytest.mark.case("UT-M1-010")
def test_lower_severity_duplicate_is_discarded() -> None:
    high = _finding(1, fingerprint="A", severity=Severity.HIGH)
    low = _finding(2, fingerprint="A", severity=Severity.LOW)

    kept, losers = deduplicate((high, low))

    assert kept == (high,)
    assert losers == (low,)


@pytest.mark.case("UT-M1-010")
def test_equal_severity_prefers_the_more_specific_evidence_reason() -> None:
    terse = _finding(1, fingerprint="A", reason="Bad.")
    specific = _finding(2, fingerprint="A", reason="None reaches this line on the error path.")

    kept, losers = deduplicate((terse, specific))

    assert kept == (specific,)
    assert losers == (terse,)


@pytest.mark.case("UT-M1-010")
def test_equal_severity_and_specificity_keeps_the_input_order() -> None:
    first = _finding(1, fingerprint="A", reason="Same reason.")
    second = _finding(2, fingerprint="A", reason="Same reason.")

    kept, losers = deduplicate((first, second))

    assert kept == (first,)
    assert losers == (second,)


@pytest.mark.case("UT-M1-010")
def test_empty_input_produces_empty_results() -> None:
    assert deduplicate(()) == ((), ())


@pytest.mark.case("UT-M1-010")
@pytest.mark.parametrize(
    ("position", "evidence", "reflection", "expected_is_main"),
    [
        (
            ValidationStatus.VALID,
            ValidationStatus.VALID,
            ReflectionStatus.REFLECTION_SKIPPED,
            True,
        ),
        (ValidationStatus.VALID, ValidationStatus.VALID, ReflectionStatus.SUPPORTED, True),
        (
            ValidationStatus.INVALID,
            ValidationStatus.VALID,
            ReflectionStatus.REFLECTION_SKIPPED,
            False,
        ),
        (
            ValidationStatus.VALID,
            ValidationStatus.INVALID,
            ReflectionStatus.REFLECTION_SKIPPED,
            False,
        ),
        (ValidationStatus.VALID, ValidationStatus.VALID, ReflectionStatus.NEEDS_CONTEXT, False),
        (ValidationStatus.VALID, ValidationStatus.VALID, ReflectionStatus.CONTRADICTED, False),
    ],
)
def test_main_finding_requires_valid_position_evidence_and_reflection(
    position: ValidationStatus,
    evidence: ValidationStatus,
    reflection: ReflectionStatus,
    expected_is_main: bool,
) -> None:
    outcome = ValidationOutcome(
        position_status=position,
        evidence_status=evidence,
        reflection_status=reflection,
    )
    finding = _finding(1, fingerprint="A", validation=outcome)

    assert outcome.is_displayable is expected_is_main
    assert finding.is_main is expected_is_main


def _finding(
    sequence: int,
    *,
    fingerprint: str,
    severity: Severity = Severity.MEDIUM,
    reason: str = "None reaches this line.",
    validation: ValidationOutcome | None = None,
) -> Finding:
    candidate = CandidateFinding(
        file=NormalizedPath("src/app.py"),
        line_range=LineRange(2, 2),
        category="correctness",
        issue_type="null-dereference",
        rule_id="PYTHON.NULL_DEREFERENCE",
        primary_entity="user",
        severity=severity,
        title="Guard nullable user",
        description="The value can be None.",
        evidence=Evidence(quote="user.name", reason=reason),
        suggestion="Return before dereferencing user.",
        needs_context=False,
    )
    return Finding(
        finding_id=FindingId(bundle_id=BUNDLE_ID, sequence=sequence),
        bundle_id=BUNDLE_ID,
        candidate=candidate,
        fingerprint=fingerprint,
        validation=validation
        or ValidationOutcome(
            position_status=ValidationStatus.VALID,
            evidence_status=ValidationStatus.VALID,
            reflection_status=ReflectionStatus.REFLECTION_SKIPPED,
        ),
        enclosing_symbol="load_user",
    )
