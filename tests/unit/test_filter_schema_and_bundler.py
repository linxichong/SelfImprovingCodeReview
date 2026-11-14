from __future__ import annotations

from typing import Any

import pytest

from self_improving_code_review.application.review.bundler import SimpleBundler
from self_improving_code_review.application.review.output_schema import (
    validate_review_output_payload,
)
from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.change.file_filter import FileFilter, FilterPolicy
from self_improving_code_review.domain.common.enums import FileDecision, FilterReason
from self_improving_code_review.domain.common.errors import InvalidLlmOutputError
from self_improving_code_review.domain.common.values import RunId


@pytest.mark.case("UT-M1-005")
@pytest.mark.parametrize(
    ("path", "expected_decision", "expected_reason"),
    [
        ("uv.lock", FileDecision.FILTER, FilterReason.LOCK_FILE),
        ("vendor/code.py", FileDecision.FILTER, FilterReason.VENDOR),
        ("build/code.py", FileDecision.FILTER, FilterReason.BUILD_OUTPUT),
        ("web/app.min.js", FileDecision.FILTER, FilterReason.MINIFIED),
        ("gen/item_pb2.py", FileDecision.FILTER, FilterReason.GENERATED),
        ("docs/readme.md", FileDecision.LIGHTWEIGHT, FilterReason.DOCUMENT),
        ("src/app.py", FileDecision.REVIEW, None),
    ],
)
def test_file_filter_reason_codes(
    path: str,
    expected_decision: FileDecision,
    expected_reason: FilterReason | None,
) -> None:
    changed_file = UnifiedDiffParser().parse(_diff(path)).files[0]

    result = FileFilter().decide(changed_file)

    assert result.decision is expected_decision
    assert result.reason is expected_reason


@pytest.mark.case("UT-M1-005")
def test_file_filter_honors_configured_exclusion() -> None:
    changed_file = UnifiedDiffParser().parse(_diff("private/data.py")).files[0]

    result = FileFilter(FilterPolicy(exclude_patterns=("private/**",))).decide(changed_file)

    assert result.reason is FilterReason.EXCLUDED


@pytest.mark.case("UT-M1-006")
def test_bundler_groups_subject_and_test_and_rejects_oversized_hunk() -> None:
    parser = UnifiedDiffParser()
    subject = parser.parse(_diff("src/user.py")).files[0]
    test = parser.parse(_diff("tests/test_user.py")).files[0]
    grouped = SimpleBundler(max_diff_tokens=500).bundle(
        RunId("12345678-1234-4234-9234-123456789abc"),
        (subject, test),
    )
    oversized = parser.parse(_diff("src/huge.py", added="x" * 200)).files[0]
    rejected = SimpleBundler(max_diff_tokens=10).bundle(
        RunId("12345678-1234-4234-9234-123456789abc"),
        (oversized,),
    )

    assert grouped.bundles[0].paths == ("src/user.py", "tests/test_user.py")
    assert rejected.bundles == ()
    assert rejected.oversized_files[0].path.value == "src/huge.py"


@pytest.mark.case("UT-M1-009")
@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.update({"unknown": True}),
        lambda value: value.update({"schema_version": 2}),
        lambda value: value.update({"findings": "not-an-array"}),
        lambda value: value["findings"][0].update({"needs_context": 1}),
        lambda value: value["findings"][0].update({"title": "x" * 161}),
        lambda value: value["findings"][0].update({"severity": "urgent"}),
    ],
)
def test_closed_output_schema_rejects_invalid_payload(mutator: Any) -> None:
    payload = _valid_payload()
    mutator(payload)

    with pytest.raises(InvalidLlmOutputError):
        validate_review_output_payload(payload, max_findings=20)


def _diff(path: str, *, added: str = "new = value") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1,2 @@\n"
        " old = value\n"
        f"+{added}\n"
    )


def _valid_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "findings": [
            {
                "file": "src/example.py",
                "start_line": 2,
                "end_line": 2,
                "category": "correctness",
                "issue_type": "bug",
                "rule_id": "PYTHON.BUG",
                "primary_entity": "value",
                "severity": "high",
                "title": "Problem",
                "description": "A concrete problem.",
                "evidence": {"quote": "new = value", "reason": "It demonstrates the issue."},
                "suggestion": "Fix the value.",
                "needs_context": False,
            }
        ],
    }
