from __future__ import annotations

from typing import Any

import pytest

from self_improving_code_review.application.review.output_schema import (
    REVIEW_OUTPUT_SCHEMA_VERSION,
    build_review_output_schema,
    validate_review_output_payload,
)
from self_improving_code_review.domain.common.errors import InvalidLlmOutputError


@pytest.mark.case("UT-M1-009")
def test_schema_is_closed_and_bounded() -> None:
    schema = build_review_output_schema(max_findings=7)

    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == REVIEW_OUTPUT_SCHEMA_VERSION
    findings = schema["properties"]["findings"]
    assert findings["maxItems"] == 7
    assert findings["items"]["additionalProperties"] is False
    assert "evidence" in findings["items"]["required"]


@pytest.mark.case("UT-M1-009")
def test_rule_id_schema_explains_the_baseline_general_id_contract() -> None:
    schema = build_review_output_schema(max_findings=3)
    rule_id = schema["properties"]["findings"]["items"]["properties"]["rule_id"]

    assert "applicable_rule_ids が空の場合" in rule_id["description"]
    assert "GENERAL.<ISSUE_TYPE>" in rule_id["description"]


@pytest.mark.case("UT-M1-009")
def test_evidence_schema_explains_the_added_line_quote_contract() -> None:
    schema = build_review_output_schema(max_findings=3)
    quote = schema["properties"]["findings"]["items"]["properties"]["evidence"]["properties"][
        "quote"
    ]

    assert "追加行のコード本文" in quote["description"]
    assert "Unified Diff 記号 + は含めず" in quote["description"]


@pytest.mark.case("UT-M1-009")
def test_valid_payload_passes_validation() -> None:
    validate_review_output_payload(_payload(), max_findings=20)


@pytest.mark.case("UT-M1-009")
def test_payload_without_findings_passes_validation() -> None:
    validate_review_output_payload({"schema_version": 1, "findings": []}, max_findings=20)


@pytest.mark.case("UT-M1-009")
@pytest.mark.parametrize("payload", [None, [], "text", 1])
def test_non_object_payload_is_rejected(payload: object) -> None:
    with pytest.raises(InvalidLlmOutputError, match="must be an object"):
        validate_review_output_payload(payload, max_findings=20)


@pytest.mark.case("UT-M1-009")
@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.pop("findings"),
        lambda payload: payload.update({"extra": 1}),
    ],
)
def test_top_level_properties_are_fixed(mutator: Any) -> None:
    payload = _payload()
    mutator(payload)

    with pytest.raises(InvalidLlmOutputError, match="top-level properties"):
        validate_review_output_payload(payload, max_findings=20)


@pytest.mark.case("UT-M1-009")
def test_unsupported_schema_version_is_rejected() -> None:
    payload = _payload()
    payload["schema_version"] = 2

    with pytest.raises(InvalidLlmOutputError, match="unsupported schema_version"):
        validate_review_output_payload(payload, max_findings=20)


@pytest.mark.case("UT-M1-009")
def test_findings_exceeding_the_configured_maximum_are_rejected() -> None:
    payload = _payload()
    payload["findings"].append(dict(payload["findings"][0]))

    with pytest.raises(InvalidLlmOutputError, match="bounded array"):
        validate_review_output_payload(payload, max_findings=1)


@pytest.mark.case("UT-M1-009")
@pytest.mark.parametrize(
    ("mutator", "expected_message"),
    [
        (lambda finding: finding.pop("title"), "missing or unknown properties"),
        (lambda finding: finding.update({"extra": 1}), "missing or unknown properties"),
        (lambda finding: finding.update({"start_line": "2"}), "line values must be integers"),
        (lambda finding: finding.update({"start_line": True}), "line values must be integers"),
        (lambda finding: finding.update({"end_line": 1.5}), "line values must be integers"),
        (lambda finding: finding.update({"start_line": 0}), "line range is invalid"),
        (lambda finding: finding.update({"start_line": 5, "end_line": 2}), "line range is invalid"),
        (lambda finding: finding.update({"severity": "urgent"}), "severity is invalid"),
        (lambda finding: finding.update({"needs_context": 1}), "needs_context must be boolean"),
        (lambda finding: finding.update({"evidence": {"quote": "q"}}), "only quote and reason"),
        (
            lambda finding: finding.update({"evidence": {"quote": "q", "reason": "r", "x": 1}}),
            "only quote and reason",
        ),
        (lambda finding: finding.update({"evidence": []}), "only quote and reason"),
    ],
)
def test_invalid_findings_are_rejected(mutator: Any, expected_message: str) -> None:
    payload = _payload()
    mutator(payload["findings"][0])

    with pytest.raises(InvalidLlmOutputError, match=expected_message):
        validate_review_output_payload(payload, max_findings=20)


@pytest.mark.case("UT-M1-009")
def test_non_object_finding_is_rejected() -> None:
    payload = _payload()
    payload["findings"][0] = "not-an-object"

    with pytest.raises(InvalidLlmOutputError, match="missing or unknown properties"):
        validate_review_output_payload(payload, max_findings=20)


@pytest.mark.case("UT-M1-009")
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("file", ""),
        ("file", "x" * 513),
        ("file", 1),
        ("category", "x" * 201),
        ("issue_type", ""),
        ("rule_id", "x" * 201),
        ("primary_entity", ""),
        ("title", "x" * 161),
        ("description", "x" * 2001),
        ("suggestion", ""),
    ],
)
def test_string_fields_enforce_length_limits(field: str, value: object) -> None:
    payload = _payload()
    payload["findings"][0][field] = value

    with pytest.raises(InvalidLlmOutputError, match=f"finding {field}"):
        validate_review_output_payload(payload, max_findings=20)


@pytest.mark.case("UT-M1-009")
@pytest.mark.parametrize(
    ("field", "value", "expected_message"),
    [
        ("quote", "x" * 601, "evidence quote"),
        ("quote", "", "evidence quote"),
        ("reason", "x" * 2001, "evidence reason"),
        ("reason", 5, "evidence reason"),
    ],
)
def test_evidence_fields_enforce_length_limits(
    field: str,
    value: object,
    expected_message: str,
) -> None:
    payload = _payload()
    payload["findings"][0]["evidence"][field] = value

    with pytest.raises(InvalidLlmOutputError, match=expected_message):
        validate_review_output_payload(payload, max_findings=20)


def _payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "findings": [
            {
                "file": "src/example.py",
                "start_line": 2,
                "end_line": 2,
                "category": "correctness",
                "issue_type": "null-dereference",
                "rule_id": "PYTHON.NULL_DEREFERENCE",
                "primary_entity": "user",
                "severity": "high",
                "title": "Guard nullable user",
                "description": "The value can be None.",
                "evidence": {"quote": "user.name", "reason": "None reaches this line."},
                "suggestion": "Return before dereferencing user.",
                "needs_context": False,
            }
        ],
    }
