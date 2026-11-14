from __future__ import annotations

import pytest

from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.change.line_map import LineMap
from self_improving_code_review.domain.change.models import ChangedFile
from self_improving_code_review.domain.common.enums import ValidationStatus
from self_improving_code_review.domain.common.values import LineRange
from self_improving_code_review.domain.finding.validators import (
    EvidenceValidator,
    PositionValidator,
    PythonSyntaxValidator,
)


@pytest.mark.case("UT-M1-010")
def test_position_is_valid_when_range_contains_an_added_line() -> None:
    line_map = LineMap(commentable_lines=frozenset({2}), present_lines=frozenset({1, 2}))

    result = PositionValidator().validate(LineRange(1, 2), line_map)

    assert result.status is ValidationStatus.VALID
    assert result.reason_code is None
    assert result.is_valid


@pytest.mark.case("UT-M1-010")
def test_position_outside_the_new_file_is_rejected() -> None:
    line_map = LineMap(commentable_lines=frozenset({2}), present_lines=frozenset({1, 2}))

    result = PositionValidator().validate(LineRange(3, 3), line_map)

    assert result.status is ValidationStatus.INVALID
    assert result.reason_code == "LINE_NOT_IN_DIFF"
    assert not result.is_valid


@pytest.mark.case("UT-M1-010")
def test_position_covering_only_context_lines_is_rejected() -> None:
    line_map = LineMap(commentable_lines=frozenset({5}), present_lines=frozenset({1, 2, 5}))

    result = PositionValidator().validate(LineRange(1, 2), line_map)

    assert result.status is ValidationStatus.INVALID
    assert result.reason_code == "LINE_NOT_COMMENTABLE"


@pytest.mark.case("UT-M1-010")
def test_evidence_present_in_sent_payload_is_valid() -> None:
    result = EvidenceValidator().validate("  user.name  ", _changed_file())

    assert result.status is ValidationStatus.VALID
    assert result.reason_code is None
    assert result.line_range == LineRange(2, 2)
    assert result.is_valid


@pytest.mark.case("UT-M1-010")
def test_multiline_evidence_without_diff_markers_determines_the_added_range() -> None:
    changed_file = _changed_file(
        '+command = f"git show {revision}"\n+result = subprocess.run(command, shell=True)\n'
    )

    result = EvidenceValidator().validate(
        'command = f"git show {revision}"\nresult = subprocess.run(command, shell=True)',
        changed_file,
    )

    assert result.line_range == LineRange(2, 3)


@pytest.mark.case("UT-M1-010")
def test_multiline_evidence_with_diff_markers_determines_the_added_range() -> None:
    changed_file = _changed_file("+first = 1\n+second = 2\n")

    result = EvidenceValidator().validate("+first = 1\n+second = 2", changed_file)

    assert result.line_range == LineRange(2, 3)


@pytest.mark.case("UT-M1-010")
def test_nonmatching_multiline_evidence_is_rejected() -> None:
    changed_file = _changed_file("+first = 1\n+second = 2\n")

    result = EvidenceValidator().validate("first = 1\nother = 3", changed_file)

    assert result.reason_code == "EVIDENCE_NOT_IN_PAYLOAD"


@pytest.mark.case("UT-M1-010")
def test_evidence_matching_multiple_added_lines_is_rejected_as_ambiguous() -> None:
    changed_file = _changed_file("+unsafe = user.name\n+other = user.name\n")

    result = EvidenceValidator().validate("user.name", changed_file)

    assert result.status is ValidationStatus.INVALID
    assert result.reason_code == "EVIDENCE_AMBIGUOUS"
    assert not result.is_valid


@pytest.mark.case("UT-M1-010")
@pytest.mark.parametrize("quote", ["", "  ", "ab"])
def test_evidence_shorter_than_the_minimum_is_rejected(quote: str) -> None:
    result = EvidenceValidator().validate(quote, _changed_file())

    assert result.status is ValidationStatus.INVALID
    assert result.reason_code == "EVIDENCE_TOO_SHORT"


@pytest.mark.case("UT-M1-010")
def test_evidence_absent_from_sent_payload_is_rejected() -> None:
    result = EvidenceValidator().validate("order.total", _changed_file())

    assert result.status is ValidationStatus.INVALID
    assert result.reason_code == "EVIDENCE_NOT_IN_PAYLOAD"


@pytest.mark.case("UT-M1-010")
def test_configured_minimum_quote_length_is_honored() -> None:
    result = EvidenceValidator(min_quote_length=20).validate("user.name", _changed_file())

    assert result.reason_code == "EVIDENCE_TOO_SHORT"


@pytest.mark.case("UT-M1-010")
def test_valid_python_change_disproves_a_syntax_error_candidate() -> None:
    changed_file = _changed_file(
        "+def extract_member(path):\n"
        "+    return subprocess.run(\n"
        '+        ["tar", "-xf", path],\n'
        "+        shell=False,\n"
        "+    )\n",
        old_count=0,
    )

    reason = PythonSyntaxValidator().rejection_reason("syntax-error", changed_file)

    assert reason == "PYTHON_SYNTAX_VALID"


@pytest.mark.case("UT-M1-010")
def test_invalid_python_change_does_not_disprove_a_syntax_error_candidate() -> None:
    changed_file = _changed_file("+def broken(:\n+    pass\n", old_count=0)

    assert PythonSyntaxValidator().rejection_reason("SYNTAX_ERROR", changed_file) is None


@pytest.mark.case("UT-M1-010")
def test_non_syntax_issue_is_not_checked_by_the_python_parser() -> None:
    assert PythonSyntaxValidator().rejection_reason("other", _changed_file()) is None


@pytest.mark.case("UT-M1-010")
def test_non_python_file_is_not_checked_by_the_python_parser() -> None:
    diff = (
        "diff --git a/src/example.js b/src/example.js\n"
        "--- a/src/example.js\n"
        "+++ b/src/example.js\n"
        "@@ -1 +1,2 @@\n"
        " const value = 1;\n"
        "+const other = 2;\n"
    )
    changed_file = UnifiedDiffParser().parse(diff).files[0]

    assert PythonSyntaxValidator().rejection_reason("syntax-error", changed_file) is None


@pytest.mark.case("UT-M1-010")
def test_partial_python_hunk_is_not_treated_as_a_complete_syntax_unit() -> None:
    diff = (
        "diff --git a/src/example.py b/src/example.py\n"
        "--- a/src/example.py\n"
        "+++ b/src/example.py\n"
        "@@ -10,0 +10 @@\n"
        "+value = 1\n"
    )
    changed_file = UnifiedDiffParser().parse(diff).files[0]

    assert PythonSyntaxValidator().rejection_reason("syntax-error", changed_file) is None


def _changed_file(
    added_lines: str = "+unsafe = user.name\n",
    *,
    old_count: int = 1,
) -> ChangedFile:
    diff = (
        "diff --git a/src/example.py b/src/example.py\n"
        "--- a/src/example.py\n"
        "+++ b/src/example.py\n"
        f"@@ -1,{old_count} +1,{old_count + len(added_lines.splitlines())} @@\n"
        + (" value = 1\n" if old_count else "")
        + added_lines
    )
    return UnifiedDiffParser().parse(diff).files[0]
