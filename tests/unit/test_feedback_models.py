"""UT-M2-001, UT-M2-002: フィードバック入力と位置の検証."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from self_improving_code_review.domain.common.enums import FeedbackLabel, Severity
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import (
    BundleId,
    FeedbackId,
    FindingId,
    NormalizedPath,
    RunId,
)
from self_improving_code_review.domain.feedback.models import FeedbackRecord, IssueLocation
from self_improving_code_review.messages import Messages

RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")
FEEDBACK_ID = FeedbackId("22345678-1234-4234-9234-123456789abc")
FINDING_ID = FindingId(bundle_id=BundleId(run_id=RUN_ID, sequence=1), sequence=1)
CREATED_AT = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)


@pytest.mark.case("UT-M2-002")
def test_location_accepts_line_only() -> None:
    location = IssueLocation(file=NormalizedPath("src/app.py"), start_line=12)

    assert location.locator == "line:12"
    assert location.symbol is None


@pytest.mark.case("UT-M2-002")
def test_location_accepts_symbol_only() -> None:
    location = IssueLocation(file=NormalizedPath("src/app.py"), symbol="load_user")

    assert location.locator == "symbol:load_user"
    assert location.start_line is None


@pytest.mark.case("UT-M2-002")
def test_location_combines_line_and_symbol_when_both_are_given() -> None:
    location = IssueLocation(
        file=NormalizedPath("src/app.py"),
        start_line=12,
        symbol="load_user",
    )

    assert location.locator == "line:12:symbol:load_user"


def test_location_normalizes_blank_symbol_to_none() -> None:
    location = IssueLocation(file=NormalizedPath("src/app.py"), start_line=1, symbol="  ")

    assert location.symbol is None


@pytest.mark.case("UT-M2-002")
@pytest.mark.parametrize(
    ("start_line", "symbol", "expected_message"),
    [
        (None, None, Messages.MISSED_ISSUE_LOCATION_REQUIRED),
        (None, "   ", Messages.MISSED_ISSUE_LOCATION_REQUIRED),
        (0, None, Messages.MISSED_ISSUE_START_LINE_INVALID),
        (-1, "load_user", Messages.MISSED_ISSUE_START_LINE_INVALID),
    ],
)
def test_location_rejects_unverifiable_positions(
    start_line: int | None,
    symbol: str | None,
    expected_message: str,
) -> None:
    with pytest.raises(InvalidInputError, match=re.escape(expected_message)):
        IssueLocation(file=NormalizedPath("src/app.py"), start_line=start_line, symbol=symbol)


@pytest.mark.case("UT-M2-001")
def test_finding_feedback_accepts_every_allowed_label() -> None:
    for label in FeedbackLabel:
        if label.is_missed_issue:
            continue
        record = _finding_feedback(label=label)

        assert record.label is label
        assert not record.is_missed_issue


@pytest.mark.case("UT-M2-001")
@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"finding_id": None}, Messages.FINDING_FEEDBACK_ID_REQUIRED),
        ({"reason": "   "}, Messages.FINDING_FEEDBACK_REASON_REQUIRED),
        (
            {"location": IssueLocation(file=NormalizedPath("src/app.py"), start_line=1)},
            Messages.FINDING_FEEDBACK_LOCATION_FORBIDDEN,
        ),
    ],
)
def test_finding_feedback_rejects_incomplete_records(
    overrides: dict[str, object],
    expected_message: str,
) -> None:
    with pytest.raises(InvalidInputError, match=re.escape(expected_message)):
        _finding_feedback(**overrides)


def test_missed_issue_feedback_is_accepted_with_required_fields() -> None:
    record = _missed_issue_feedback()

    assert record.is_missed_issue
    assert record.label is FeedbackLabel.MISSED_ISSUE
    assert record.severity is Severity.HIGH


@pytest.mark.case("UT-M2-002")
@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"finding_id": FINDING_ID}, Messages.MISSED_ISSUE_FINDING_FORBIDDEN),
        ({"location": None}, Messages.MISSED_ISSUE_FEEDBACK_LOCATION_REQUIRED),
        ({"issue_type": "   "}, Messages.MISSED_ISSUE_TYPE_REQUIRED),
        ({"issue_type": None}, Messages.MISSED_ISSUE_TYPE_REQUIRED),
        ({"severity": None}, Messages.MISSED_ISSUE_SEVERITY_REQUIRED),
        ({"evidence": "  "}, Messages.MISSED_ISSUE_EVIDENCE_REQUIRED),
        ({"evidence": None}, Messages.MISSED_ISSUE_EVIDENCE_REQUIRED),
    ],
)
def test_missed_issue_feedback_rejects_incomplete_records(
    overrides: dict[str, object],
    expected_message: str,
) -> None:
    with pytest.raises(InvalidInputError, match=re.escape(expected_message)):
        _missed_issue_feedback(**overrides)


@pytest.mark.case("UT-M2-001")
@pytest.mark.parametrize("actor_id", ["", "   "])
def test_feedback_requires_an_actor(actor_id: str) -> None:
    with pytest.raises(InvalidInputError, match=re.escape(Messages.FEEDBACK_ACTOR_REQUIRED)):
        _finding_feedback(actor_id=actor_id)


def test_feedback_requires_timezone_aware_creation_time() -> None:
    with pytest.raises(InvalidInputError, match=re.escape(Messages.FEEDBACK_TIME_NOT_UTC)):
        _finding_feedback(created_at=datetime(2026, 1, 2, 3, 4))  # noqa: DTZ001


def test_correction_references_the_superseded_record() -> None:
    superseded = FeedbackId("32345678-1234-4234-9234-123456789abc")

    record = _finding_feedback(supersedes_feedback_id=superseded)

    assert record.supersedes_feedback_id == superseded


def _finding_feedback(**overrides: object) -> FeedbackRecord:
    values: dict[str, object] = {
        "feedback_id": FEEDBACK_ID,
        "run_id": RUN_ID,
        "label": FeedbackLabel.FALSE_POSITIVE,
        "reason": "The framework validates this argument.",
        "actor_id": "reviewer-1",
        "created_at": CREATED_AT,
        "idempotency_key": "key",
        "finding_id": FINDING_ID,
    }
    values.update(overrides)
    return FeedbackRecord(**values)  # type: ignore[arg-type]


def _missed_issue_feedback(**overrides: object) -> FeedbackRecord:
    values: dict[str, object] = {
        "feedback_id": FEEDBACK_ID,
        "run_id": RUN_ID,
        "label": FeedbackLabel.MISSED_ISSUE,
        "reason": "The null check is missing.",
        "actor_id": "reviewer-1",
        "created_at": CREATED_AT,
        "idempotency_key": "key",
        "location": IssueLocation(file=NormalizedPath("src/app.py"), start_line=12),
        "issue_type": "null-dereference",
        "severity": Severity.HIGH,
        "description": "The null check is missing.",
        "evidence": "user.name is dereferenced without a guard.",
    }
    values.update(overrides)
    return FeedbackRecord(**values)  # type: ignore[arg-type]
