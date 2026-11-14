"""UT-M2-007: メモリ候補の閉じたスキーマ検証."""

from __future__ import annotations

from typing import Any

import pytest

from self_improving_code_review.application.memory.candidate_schema import (
    MEMORY_CANDIDATE_SCHEMA_VERSION,
    build_memory_candidate_schema,
    validate_memory_candidate_payload,
)
from self_improving_code_review.domain.common.enums import MemoryCategory, MemoryPolarity
from self_improving_code_review.domain.common.errors import InvalidLlmOutputError

_SCOPE_GROUPS = ("languages", "frameworks", "paths", "symbols", "keywords", "risk_tags")


@pytest.mark.case("UT-M2-007")
def test_schema_is_closed_and_declares_every_scope_group() -> None:
    schema = build_memory_candidate_schema()

    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == MEMORY_CANDIDATE_SCHEMA_VERSION
    scope = schema["properties"]["scope"]
    assert scope["additionalProperties"] is False
    assert set(scope["required"]) == set(_SCOPE_GROUPS)
    assert schema["properties"]["category"]["enum"] == [item.value for item in MemoryCategory]
    assert schema["properties"]["polarity"]["enum"] == [item.value for item in MemoryPolarity]


@pytest.mark.case("UT-M2-007")
def test_valid_payload_passes_validation() -> None:
    validate_memory_candidate_payload(_payload())


@pytest.mark.case("UT-M2-007")
@pytest.mark.parametrize("payload", [None, [], "text", 1, True])
def test_non_object_payload_is_rejected(payload: object) -> None:
    with pytest.raises(InvalidLlmOutputError, match="must be an object"):
        validate_memory_candidate_payload(payload)


@pytest.mark.case("UT-M2-007")
@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.pop("fact"),
        lambda payload: payload.update({"extra": 1}),
        lambda payload: payload.pop("scope"),
    ],
)
def test_top_level_properties_are_fixed(mutator: Any) -> None:
    payload = _payload()
    mutator(payload)

    with pytest.raises(InvalidLlmOutputError, match="top-level properties"):
        validate_memory_candidate_payload(payload)


@pytest.mark.case("UT-M2-007")
@pytest.mark.parametrize("version", [0, 2, "1"])
def test_unsupported_schema_version_is_rejected(version: object) -> None:
    payload = _payload()
    payload["schema_version"] = version

    with pytest.raises(InvalidLlmOutputError, match="unsupported schema_version"):
        validate_memory_candidate_payload(payload)


@pytest.mark.case("UT-M2-007")
@pytest.mark.parametrize("category", ["unknown", "", 1, None])
def test_invalid_category_is_rejected(category: object) -> None:
    payload = _payload()
    payload["category"] = category

    with pytest.raises(InvalidLlmOutputError, match="category is invalid"):
        validate_memory_candidate_payload(payload)


@pytest.mark.case("UT-M2-007")
@pytest.mark.parametrize("polarity", ["delete", "", 1, None])
def test_invalid_polarity_is_rejected(polarity: object) -> None:
    payload = _payload()
    payload["polarity"] = polarity

    with pytest.raises(InvalidLlmOutputError, match="polarity is invalid"):
        validate_memory_candidate_payload(payload)


@pytest.mark.case("UT-M2-007")
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fact", ""),
        ("fact", "   "),
        ("fact", "x" * 501),
        ("fact", 1),
        ("review_instruction", ""),
        ("review_instruction", "x" * 501),
        ("evidence_summary", ""),
        ("evidence_summary", "x" * 501),
        ("evidence_summary", None),
    ],
)
def test_text_fields_enforce_length_limits(field: str, value: object) -> None:
    payload = _payload()
    payload[field] = value

    with pytest.raises(InvalidLlmOutputError, match=f"memory candidate {field}"):
        validate_memory_candidate_payload(payload)


@pytest.mark.case("UT-M2-007")
def test_text_fields_accept_the_maximum_length() -> None:
    payload = _payload()
    payload["fact"] = "x" * 500
    payload["review_instruction"] = "y" * 500
    payload["evidence_summary"] = "z" * 500

    validate_memory_candidate_payload(payload)


@pytest.mark.case("UT-M2-007")
@pytest.mark.parametrize("confidence", [0.0, 0.5, 1.0, 1])
def test_confidence_accepts_the_allowed_range(confidence: object) -> None:
    payload = _payload()
    payload["confidence"] = confidence

    validate_memory_candidate_payload(payload)


@pytest.mark.case("UT-M2-007")
@pytest.mark.parametrize("confidence", [-0.1, 1.1, 2])
def test_confidence_outside_the_range_is_rejected(confidence: object) -> None:
    payload = _payload()
    payload["confidence"] = confidence

    with pytest.raises(InvalidLlmOutputError, match=r"between 0\.0 and 1\.0"):
        validate_memory_candidate_payload(payload)


@pytest.mark.case("UT-M2-007")
@pytest.mark.parametrize("confidence", ["0.8", None, True, False])
def test_non_numeric_confidence_is_rejected(confidence: object) -> None:
    payload = _payload()
    payload["confidence"] = confidence

    with pytest.raises(InvalidLlmOutputError, match="confidence must be a number"):
        validate_memory_candidate_payload(payload)


@pytest.mark.case("UT-M2-007")
@pytest.mark.parametrize("scope", [None, [], "paths", 1])
def test_non_object_scope_is_rejected(scope: object) -> None:
    payload = _payload()
    payload["scope"] = scope

    with pytest.raises(InvalidLlmOutputError, match="scope has missing or unknown groups"):
        validate_memory_candidate_payload(payload)


@pytest.mark.case("UT-M2-007")
@pytest.mark.parametrize(
    "mutator",
    [
        lambda scope: scope.pop("paths"),
        lambda scope: scope.update({"unknown": []}),
    ],
)
def test_scope_groups_are_fixed(mutator: Any) -> None:
    payload = _payload()
    mutator(payload["scope"])

    with pytest.raises(InvalidLlmOutputError, match="scope has missing or unknown groups"):
        validate_memory_candidate_payload(payload)


@pytest.mark.case("UT-M2-007")
@pytest.mark.parametrize("group", _SCOPE_GROUPS)
def test_scope_group_must_be_a_bounded_array(group: str) -> None:
    payload = _payload()
    payload["scope"][group] = "not-an-array"

    with pytest.raises(InvalidLlmOutputError, match=f"scope {group} must be a bounded array"):
        validate_memory_candidate_payload(payload)


@pytest.mark.case("UT-M2-007")
def test_scope_group_exceeding_the_item_limit_is_rejected() -> None:
    payload = _payload()
    payload["scope"]["paths"] = [f"src/{index}/**" for index in range(21)]

    with pytest.raises(InvalidLlmOutputError, match="scope paths must be a bounded array"):
        validate_memory_candidate_payload(payload)


@pytest.mark.case("UT-M2-007")
def test_scope_group_accepts_the_item_limit() -> None:
    payload = _payload()
    payload["scope"]["paths"] = [f"src/{index}/**" for index in range(20)]

    validate_memory_candidate_payload(payload)


@pytest.mark.case("UT-M2-007")
@pytest.mark.parametrize("value", ["", "   ", "x" * 201, 1, None])
def test_scope_items_must_be_bounded_strings(value: object) -> None:
    payload = _payload()
    payload["scope"]["paths"] = [value]

    with pytest.raises(InvalidLlmOutputError, match="scope paths item"):
        validate_memory_candidate_payload(payload)


@pytest.mark.case("UT-M2-007")
def test_completely_empty_scope_is_rejected() -> None:
    payload = _payload()
    payload["scope"] = {name: [] for name in _SCOPE_GROUPS}

    with pytest.raises(InvalidLlmOutputError, match="scope must define at least one group"):
        validate_memory_candidate_payload(payload)


@pytest.mark.case("UT-M2-007")
def test_scope_with_one_populated_group_is_accepted() -> None:
    payload = _payload()
    payload["scope"] = {**{name: [] for name in _SCOPE_GROUPS}, "keywords": ["@Valid"]}

    validate_memory_candidate_payload(payload)


def _payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "category": "framework",
        "scope": {
            "languages": ["java"],
            "frameworks": ["spring"],
            "paths": ["src/**/controller/**"],
            "symbols": [],
            "keywords": ["@Valid"],
            "risk_tags": ["validation"],
        },
        "fact": "The framework validates annotated arguments before handling.",
        "review_instruction": "Require a bypass proof before reporting missing validation.",
        "polarity": "suppress",
        "confidence": 0.82,
        "evidence_summary": "Confirmed on the reviewed revision.",
    }
