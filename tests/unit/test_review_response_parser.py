from __future__ import annotations

import re
from typing import Any

import pytest

from self_improving_code_review.application.review.bundler import SimpleBundler
from self_improving_code_review.application.review.models import ReviewBundle
from self_improving_code_review.application.review.response_parser import parse_review_response
from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.common.enums import Severity
from self_improving_code_review.domain.common.errors import InvalidLlmOutputError
from self_improving_code_review.domain.common.values import RunId
from self_improving_code_review.messages import Messages

RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")


@pytest.mark.case("UT-M1-009")
def test_valid_payload_is_converted_into_candidates() -> None:
    parsed = parse_review_response(_payload(), _bundle())

    candidate = parsed.candidates[0]
    assert parsed.rejected == ()
    assert candidate.file.value == "src/example.py"
    assert candidate.line_range.start_line == 2
    assert candidate.severity is Severity.HIGH
    assert candidate.evidence.quote == "user.name"
    assert candidate.primary_entity == "user"
    assert not candidate.needs_context


@pytest.mark.case("UT-M1-009")
def test_whitespace_in_text_fields_is_normalized() -> None:
    payload = _payload()
    payload["findings"][0]["title"] = "  Guard user  "
    payload["findings"][0]["suggestion"] = "  Return early.  "

    candidate = parse_review_response(payload, _bundle()).candidates[0]

    assert candidate.title == "Guard user"
    assert candidate.suggestion == "Return early."


@pytest.mark.case("UT-M1-009")
@pytest.mark.parametrize("version", [None, 2, "1"])
def test_unsupported_schema_version_is_rejected(version: object) -> None:
    payload = _payload()
    if version is None:
        del payload["schema_version"]
    else:
        payload["schema_version"] = version

    with pytest.raises(InvalidLlmOutputError, match="schema_version"):
        parse_review_response(payload, _bundle())


@pytest.mark.case("UT-M1-009")
@pytest.mark.parametrize("findings", [None, "not-an-array", {}])
def test_non_array_findings_is_rejected(findings: object) -> None:
    payload: dict[str, Any] = {"schema_version": 1}
    if findings is not None:
        payload["findings"] = findings

    with pytest.raises(
        InvalidLlmOutputError, match=re.escape(Messages.RESPONSE_FINDINGS_NOT_ARRAY)
    ):
        parse_review_response(payload, _bundle())


@pytest.mark.case("UT-M1-009")
@pytest.mark.parametrize(
    ("mutator", "expected_reason"),
    [
        (lambda payload: payload["findings"].__setitem__(0, "not-an-object"), "MALFORMED_FINDING"),
        (lambda payload: payload["findings"][0].update({"file": "../outside.py"}), "INVALID_PATH"),
        (
            lambda payload: payload["findings"][0].update({"file": "src/other.py"}),
            "FILE_NOT_IN_BUNDLE",
        ),
        (
            lambda payload: payload["findings"][0].update({"start_line": 5, "end_line": 2}),
            "INVALID_LINE_RANGE",
        ),
        (lambda payload: payload["findings"][0].pop("start_line"), "INVALID_LINE_RANGE"),
        (
            lambda payload: payload["findings"][0].update({"start_line": "two"}),
            "INVALID_LINE_RANGE",
        ),
        (lambda payload: payload["findings"][0].update({"start_line": None}), "INVALID_LINE_RANGE"),
        (lambda payload: payload["findings"][0].update({"severity": "urgent"}), "INVALID_SEVERITY"),
        (lambda payload: payload["findings"][0].pop("severity"), "INVALID_SEVERITY"),
        (lambda payload: payload["findings"][0].update({"evidence": "quote"}), "MISSING_EVIDENCE"),
        (lambda payload: payload["findings"][0].pop("evidence"), "MISSING_EVIDENCE"),
    ],
)
def test_individual_candidates_are_rejected_with_a_reason_code(
    mutator: Any,
    expected_reason: str,
) -> None:
    payload = _payload()
    mutator(payload)

    parsed = parse_review_response(payload, _bundle())

    assert parsed.candidates == ()
    assert parsed.rejected[0].reason_code == expected_reason


@pytest.mark.case("UT-M1-009")
def test_one_invalid_candidate_does_not_discard_the_valid_ones() -> None:
    payload = _payload()
    invalid = dict(payload["findings"][0])
    invalid["file"] = "src/other.py"
    payload["findings"].append(invalid)

    parsed = parse_review_response(payload, _bundle())

    assert len(parsed.candidates) == 1
    assert len(parsed.rejected) == 1


@pytest.mark.case("UT-M1-009")
def test_needs_context_flag_is_preserved() -> None:
    payload = _payload()
    payload["findings"][0]["needs_context"] = True

    candidate = parse_review_response(payload, _bundle()).candidates[0]

    assert candidate.needs_context


@pytest.mark.case("UT-M1-009")
def test_empty_findings_array_produces_no_candidate() -> None:
    parsed = parse_review_response({"schema_version": 1, "findings": []}, _bundle())

    assert parsed.candidates == ()
    assert parsed.rejected == ()


def _bundle() -> ReviewBundle:
    diff = (
        "diff --git a/src/example.py b/src/example.py\n"
        "--- a/src/example.py\n"
        "+++ b/src/example.py\n"
        "@@ -1 +1,2 @@\n"
        " value = 1\n"
        "+unsafe = user.name\n"
    )
    files = UnifiedDiffParser().parse(diff).files
    return SimpleBundler(max_diff_tokens=8000).bundle(RUN_ID, files).bundles[0]


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
