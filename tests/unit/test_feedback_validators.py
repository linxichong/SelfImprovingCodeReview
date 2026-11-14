"""UT-M2-003: フィードバックの正規化と冪等性キー."""

from __future__ import annotations

import pytest

from self_improving_code_review.domain.common.enums import FeedbackLabel
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import (
    BundleId,
    FindingId,
    NormalizedPath,
    RunId,
)
from self_improving_code_review.domain.feedback.models import IssueLocation
from self_improving_code_review.domain.feedback.validators import (
    MAX_FREE_TEXT_LENGTH,
    build_finding_feedback_key,
    build_missed_issue_key,
    normalize_free_text,
)

RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")
OTHER_RUN_ID = RunId("22345678-1234-4234-9234-123456789abc")
FINDING_ID = FindingId(bundle_id=BundleId(run_id=RUN_ID, sequence=1), sequence=1)
OTHER_FINDING_ID = FindingId(bundle_id=BundleId(run_id=RUN_ID, sequence=1), sequence=2)


@pytest.mark.case("UT-M2-001")
def test_free_text_is_trimmed() -> None:
    assert normalize_free_text("reason", "  valid reason  ") == "valid reason"


@pytest.mark.case("UT-M2-001")
def test_free_text_accepts_the_maximum_length() -> None:
    value = "x" * MAX_FREE_TEXT_LENGTH

    assert normalize_free_text("reason", value) == value


def test_blank_free_text_can_be_allowed_explicitly() -> None:
    assert normalize_free_text("symbol", "   ", allow_empty=True) == ""


@pytest.mark.case("UT-M2-001")
@pytest.mark.parametrize("value", ["", "   "])
def test_blank_free_text_is_rejected_by_default(value: str) -> None:
    with pytest.raises(InvalidInputError, match="must not be empty"):
        normalize_free_text("reason", value)


@pytest.mark.case("UT-M2-001")
def test_free_text_longer_than_the_maximum_is_rejected() -> None:
    with pytest.raises(InvalidInputError, match="at most"):
        normalize_free_text("reason", "x" * (MAX_FREE_TEXT_LENGTH + 1))


@pytest.mark.case("UT-M2-001")
@pytest.mark.parametrize("control", ["\x00", "\x07", "\x1f", "\x7f"])
def test_control_characters_are_rejected(control: str) -> None:
    with pytest.raises(InvalidInputError, match="control characters"):
        normalize_free_text("reason", f"valid{control}text")


@pytest.mark.case("UT-M2-001")
@pytest.mark.parametrize(
    "marker",
    ["<<<", ">>>", "UNTRUSTED_DATA_BEGIN", "UNTRUSTED_DATA_END"],
)
def test_prompt_boundary_markers_are_rejected(marker: str) -> None:
    with pytest.raises(InvalidInputError, match="prompt boundary markers"):
        normalize_free_text("reason", f"reason {marker} injected")


@pytest.mark.case("UT-M2-003")
def test_same_finding_feedback_input_produces_the_same_key() -> None:
    first = build_finding_feedback_key(
        run_id=RUN_ID,
        finding_id=FINDING_ID,
        actor_id="reviewer-1",
        label=FeedbackLabel.FALSE_POSITIVE,
        reason="The framework validates this argument.",
    )
    second = build_finding_feedback_key(
        run_id=RUN_ID,
        finding_id=FINDING_ID,
        actor_id="reviewer-1",
        label=FeedbackLabel.FALSE_POSITIVE,
        reason="The framework validates this argument.",
    )

    assert first == second


@pytest.mark.case("UT-M2-003")
@pytest.mark.parametrize(
    "overrides",
    [
        {"run_id": OTHER_RUN_ID},
        {"finding_id": OTHER_FINDING_ID},
        {"actor_id": "reviewer-2"},
        {"label": FeedbackLabel.TRUE_POSITIVE},
        {"reason": "A different reason."},
    ],
)
def test_finding_feedback_key_changes_with_each_contract_field(
    overrides: dict[str, object],
) -> None:
    baseline = build_finding_feedback_key(
        run_id=RUN_ID,
        finding_id=FINDING_ID,
        actor_id="reviewer-1",
        label=FeedbackLabel.FALSE_POSITIVE,
        reason="The framework validates this argument.",
    )
    values: dict[str, object] = {
        "run_id": RUN_ID,
        "finding_id": FINDING_ID,
        "actor_id": "reviewer-1",
        "label": FeedbackLabel.FALSE_POSITIVE,
        "reason": "The framework validates this argument.",
    }
    values.update(overrides)

    assert build_finding_feedback_key(**values) != baseline  # type: ignore[arg-type]


@pytest.mark.case("UT-M2-003")
def test_same_missed_issue_input_produces_the_same_key() -> None:
    location = IssueLocation(file=NormalizedPath("src/app.py"), start_line=12)

    first = build_missed_issue_key(
        run_id=RUN_ID,
        location=location,
        issue_type="null-dereference",
        evidence="user.name is dereferenced.",
    )
    second = build_missed_issue_key(
        run_id=RUN_ID,
        location=IssueLocation(file=NormalizedPath("src/app.py"), start_line=12),
        issue_type="null-dereference",
        evidence="user.name is dereferenced.",
    )

    assert first == second


@pytest.mark.case("UT-M2-003")
@pytest.mark.parametrize(
    "overrides",
    [
        {"run_id": OTHER_RUN_ID},
        {"location": IssueLocation(file=NormalizedPath("src/other.py"), start_line=12)},
        {"location": IssueLocation(file=NormalizedPath("src/app.py"), start_line=13)},
        {"location": IssueLocation(file=NormalizedPath("src/app.py"), symbol="load_user")},
        {"issue_type": "resource-leak"},
        {"evidence": "A different evidence."},
    ],
)
def test_missed_issue_key_changes_with_each_contract_field(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "run_id": RUN_ID,
        "location": IssueLocation(file=NormalizedPath("src/app.py"), start_line=12),
        "issue_type": "null-dereference",
        "evidence": "user.name is dereferenced.",
    }
    baseline = build_missed_issue_key(**values)  # type: ignore[arg-type]
    values.update(overrides)

    assert build_missed_issue_key(**values) != baseline  # type: ignore[arg-type]


def test_missed_issue_key_ignores_the_reporter() -> None:
    # 同じ見逃しは報告者が異なっても重複作成しない。
    location = IssueLocation(file=NormalizedPath("src/app.py"), start_line=12)

    key = build_missed_issue_key(
        run_id=RUN_ID,
        location=location,
        issue_type="null-dereference",
        evidence="user.name is dereferenced.",
    )

    assert key == build_missed_issue_key(
        run_id=RUN_ID,
        location=location,
        issue_type="null-dereference",
        evidence="user.name is dereferenced.",
    )
