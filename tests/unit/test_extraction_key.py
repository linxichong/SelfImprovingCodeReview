"""UT-M2-004: メモリ候補抽出の冪等性キー."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from self_improving_code_review.application.memory.extraction_key import (
    build_extraction_key,
    feedback_content_hash,
)
from self_improving_code_review.domain.common.enums import FeedbackLabel, Severity
from self_improving_code_review.domain.common.values import (
    BundleId,
    FeedbackId,
    FindingId,
    NormalizedPath,
    ProjectId,
    RunId,
)
from self_improving_code_review.domain.feedback.models import FeedbackRecord, IssueLocation

PROJECT_ID = ProjectId("test-project")
OTHER_PROJECT_ID = ProjectId("other-project")
RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")
CREATED_AT = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
FIRST_ID = FeedbackId("11111111-1234-4234-9234-123456789abc")
SECOND_ID = FeedbackId("22222222-1234-4234-9234-123456789abc")


@pytest.mark.case("UT-M2-004")
def test_key_is_stable_for_the_same_input() -> None:
    feedback = (_feedback(FIRST_ID), _feedback(SECOND_ID))

    assert _key(feedback=feedback) == _key(feedback=feedback)


@pytest.mark.case("UT-M2-004")
def test_feedback_order_does_not_change_the_key() -> None:
    first = _feedback(FIRST_ID)
    second = _feedback(SECOND_ID)

    assert _key(feedback=(first, second)) == _key(feedback=(second, first))


@pytest.mark.case("UT-M2-004")
@pytest.mark.parametrize(
    "overrides",
    [
        {"project_id": OTHER_PROJECT_ID},
        {"source_revision": "b" * 40},
        {"source_revision": None},
        {"context_locator": "src/app.py:10-20"},
        {"prompt_hash": "other-prompt-hash"},
        {"model": "claude-other-v1"},
    ],
)
def test_key_changes_with_each_contract_field(overrides: dict[str, object]) -> None:
    baseline = _key()

    assert _key(**overrides) != baseline


@pytest.mark.case("UT-M2-004")
def test_key_changes_when_the_feedback_set_changes() -> None:
    single = _key(feedback=(_feedback(FIRST_ID),))
    pair = _key(feedback=(_feedback(FIRST_ID), _feedback(SECOND_ID)))

    assert single != pair


@pytest.mark.case("UT-M2-004")
def test_key_changes_when_feedback_content_changes_under_the_same_id() -> None:
    original = _key(feedback=(_feedback(FIRST_ID),))
    edited = _key(feedback=(_feedback(FIRST_ID, reason="A different reason."),))

    assert original != edited


@pytest.mark.case("UT-M2-004")
def test_content_hash_is_stable_for_the_same_record() -> None:
    record = _feedback(FIRST_ID)

    assert feedback_content_hash(record) == feedback_content_hash(_feedback(FIRST_ID))


@pytest.mark.case("UT-M2-004")
@pytest.mark.parametrize(
    "overrides",
    [
        {"label": FeedbackLabel.TRUE_POSITIVE},
        {"reason": "A different reason."},
    ],
)
def test_content_hash_changes_with_each_meaningful_field(overrides: dict[str, object]) -> None:
    baseline = feedback_content_hash(_feedback(FIRST_ID))

    assert feedback_content_hash(_feedback(FIRST_ID, **overrides)) != baseline


@pytest.mark.case("UT-M2-004")
def test_content_hash_distinguishes_different_identifiers() -> None:
    assert feedback_content_hash(_feedback(FIRST_ID)) != feedback_content_hash(_feedback(SECOND_ID))


@pytest.mark.case("UT-M2-004")
def test_content_hash_covers_missed_issue_fields() -> None:
    line_report = _missed_issue(FIRST_ID, start_line=12)
    symbol_report = _missed_issue(FIRST_ID, symbol="load_user")
    other_severity = _missed_issue(FIRST_ID, start_line=12, severity=Severity.LOW)
    other_evidence = _missed_issue(FIRST_ID, start_line=12, evidence="Another evidence.")

    hashes = {
        feedback_content_hash(line_report),
        feedback_content_hash(symbol_report),
        feedback_content_hash(other_severity),
        feedback_content_hash(other_evidence),
    }
    assert len(hashes) == 4


def _key(**overrides: object) -> str:
    values: dict[str, object] = {
        "project_id": PROJECT_ID,
        "source_revision": "a" * 40,
        "feedback": (_feedback(FIRST_ID),),
        "context_locator": "",
        "prompt_hash": "prompt-hash",
        "model": "claude-fixed-v1",
    }
    values.update(overrides)
    return build_extraction_key(**values)  # type: ignore[arg-type]


def _feedback(feedback_id: FeedbackId, **overrides: object) -> FeedbackRecord:
    values: dict[str, object] = {
        "feedback_id": feedback_id,
        "run_id": RUN_ID,
        "label": FeedbackLabel.FALSE_POSITIVE,
        "reason": "The framework validates this argument.",
        "actor_id": "reviewer-1",
        "created_at": CREATED_AT,
        "idempotency_key": f"key-{feedback_id.value}",
        "finding_id": FindingId(bundle_id=BundleId(run_id=RUN_ID, sequence=1), sequence=1),
    }
    values.update(overrides)
    return FeedbackRecord(**values)  # type: ignore[arg-type]


def _missed_issue(feedback_id: FeedbackId, **overrides: object) -> FeedbackRecord:
    location_values: dict[str, object] = {
        "file": NormalizedPath("src/app.py"),
        "start_line": overrides.pop("start_line", None),
        "symbol": overrides.pop("symbol", None),
    }
    values: dict[str, object] = {
        "feedback_id": feedback_id,
        "run_id": RUN_ID,
        "label": FeedbackLabel.MISSED_ISSUE,
        "reason": "The null check is missing.",
        "actor_id": "reviewer-1",
        "created_at": CREATED_AT,
        "idempotency_key": f"key-{feedback_id.value}",
        "location": IssueLocation(**location_values),  # type: ignore[arg-type]
        "issue_type": "null-dereference",
        "severity": Severity.HIGH,
        "description": "The null check is missing.",
        "evidence": "user.name is dereferenced without a guard.",
    }
    values.update(overrides)
    return FeedbackRecord(**values)  # type: ignore[arg-type]
