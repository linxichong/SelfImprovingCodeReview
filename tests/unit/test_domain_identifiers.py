from __future__ import annotations

import re

import pytest

from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import (
    BundleId,
    FindingId,
    ProjectId,
    RunId,
)
from self_improving_code_review.messages import Messages

VALID_RUN_ID = "12345678-1234-4234-9234-123456789abc"


@pytest.mark.case("UT-M1-003")
@pytest.mark.parametrize(
    ("value", "expected_message"),
    [
        ("", Messages.PROJECT_ID_LENGTH_INVALID),
        ("x" * 65, Messages.PROJECT_ID_LENGTH_INVALID),
        ("Upper", Messages.PROJECT_ID_FORMAT_INVALID),
        ("-leading", Messages.PROJECT_ID_FORMAT_INVALID),
        ("with space", Messages.PROJECT_ID_FORMAT_INVALID),
    ],
)
def test_project_id_rejects_invalid_values(value: str, expected_message: str) -> None:
    with pytest.raises(InvalidInputError, match=re.escape(expected_message)):
        ProjectId(value)


@pytest.mark.case("UT-M1-003")
def test_project_id_accepts_allowed_characters_and_renders_raw_value() -> None:
    assert str(ProjectId("a0_project-1")) == "a0_project-1"


@pytest.mark.case("UT-M1-003")
@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-a-uuid",
        "12345678-1234-1234-9234-123456789abc",
        "12345678-1234-4234-c234-123456789abc",
        VALID_RUN_ID.upper(),
    ],
)
def test_run_id_requires_lowercase_uuid4(value: str) -> None:
    with pytest.raises(InvalidInputError, match="UUIDv4"):
        RunId(value)


@pytest.mark.case("UT-M1-003")
def test_run_id_renders_raw_value() -> None:
    assert str(RunId(VALID_RUN_ID)) == VALID_RUN_ID


@pytest.mark.case("UT-M1-003")
@pytest.mark.parametrize("sequence", [0, -1])
def test_bundle_id_requires_positive_sequence(sequence: int) -> None:
    with pytest.raises(InvalidInputError, match=re.escape(Messages.BUNDLE_SEQUENCE_INVALID)):
        BundleId(run_id=RunId(VALID_RUN_ID), sequence=sequence)


@pytest.mark.case("UT-M1-003")
@pytest.mark.parametrize("sequence", [0, -1])
def test_finding_id_requires_positive_sequence(sequence: int) -> None:
    bundle_id = BundleId(run_id=RunId(VALID_RUN_ID), sequence=1)

    with pytest.raises(InvalidInputError, match=re.escape(Messages.FINDING_SEQUENCE_INVALID)):
        FindingId(bundle_id=bundle_id, sequence=sequence)


@pytest.mark.case("UT-M1-003")
def test_identifier_external_representation_is_zero_padded() -> None:
    bundle_id = BundleId(run_id=RunId(VALID_RUN_ID), sequence=7)
    finding_id = FindingId(bundle_id=bundle_id, sequence=12)

    assert str(bundle_id) == "B007"
    assert str(finding_id) == "B007-F012"
