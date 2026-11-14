from __future__ import annotations

import re

import pytest

from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.change.file_filter import (
    FileFilter,
    FileFilterResult,
    FilterPolicy,
)
from self_improving_code_review.domain.change.line_map import build_new_line_map
from self_improving_code_review.domain.common.enums import ChangeType, FileDecision, FilterReason
from self_improving_code_review.domain.common.errors import InvalidDiffError
from self_improving_code_review.domain.common.values import LineRange, NormalizedPath
from self_improving_code_review.messages import Messages


@pytest.mark.case("UT-M1-004")
def test_rejects_diff_containing_nul() -> None:
    with pytest.raises(InvalidDiffError, match="NUL"):
        UnifiedDiffParser().parse("diff --git a/src/a.py b/src/a.py\n\x00\n")


@pytest.mark.case("UT-M1-004")
def test_ignores_lines_before_the_first_file_header() -> None:
    diff = (
        "commit 1111111111111111111111111111111111111111\n"
        "Author: Test User <test@example.invalid>\n"
        "\n"
        "    subject line\n"
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -1 +1,2 @@\n"
        " value = 1\n"
        "+value = 2\n"
    )

    changeset = UnifiedDiffParser().parse(diff)

    assert len(changeset.files) == 1
    assert changeset.files[0].new_path.value == "src/a.py"


@pytest.mark.case("UT-M1-004")
def test_parses_copy_metadata_as_copied_change() -> None:
    diff = (
        "diff --git a/src/source.py b/src/copy.py\n"
        "similarity index 100%\n"
        "copy from src/source.py\n"
        "copy to src/copy.py\n"
    )

    changed_file = UnifiedDiffParser().parse(diff).files[0]

    assert changed_file.change_type is ChangeType.COPIED
    assert changed_file.new_path.value == "src/copy.py"
    assert changed_file.old_path is not None
    assert changed_file.old_path.value == "src/source.py"


@pytest.mark.case("UT-M1-004")
def test_stops_hunk_at_trailing_lines_after_declared_counts() -> None:
    diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -1 +1,2 @@\n"
        " value = 1\n"
        "+value = 2\n"
        "-- \n"
        "2.43.0\n"
    )

    changed_file = UnifiedDiffParser().parse(diff).files[0]

    assert len(changed_file.hunks[0].lines) == 2
    assert changed_file.added_line_numbers == frozenset({2})


@pytest.mark.case("UT-M1-004")
def test_rejects_unexpected_line_marker_inside_hunk() -> None:
    diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -1,2 +1,2 @@\n"
        " value = 1\n"
        "?unexpected\n"
    )

    with pytest.raises(InvalidDiffError, match=re.escape(Messages.DIFF_LINE_MARKER_UNEXPECTED)):
        UnifiedDiffParser().parse(diff)


@pytest.mark.case("UT-M1-004")
def test_rejects_malformed_hunk_header() -> None:
    # 呼び出し規約を破った場合も差分を修復せず拒否することを固定する。
    with pytest.raises(InvalidDiffError, match=re.escape(Messages.HUNK_HEADER_MALFORMED)):
        UnifiedDiffParser()._parse_hunk(["@@ broken @@"], 0)


@pytest.mark.case("UT-M1-004")
def test_multiple_files_are_parsed_and_findable_by_path() -> None:
    diff = _modification("src/a.py") + _modification("src/b.py")

    changeset = UnifiedDiffParser().parse(diff)

    assert changeset.find(NormalizedPath("src/b.py")) is changeset.files[1]
    assert changeset.find(NormalizedPath("src/missing.py")) is None


@pytest.mark.case("UT-M1-004")
def test_binary_file_renders_marker_instead_of_hunks() -> None:
    diff = "diff --git a/assets/logo.png b/assets/logo.png\nGIT binary patch\nliteral 8\n"

    changed_file = UnifiedDiffParser().parse(diff).files[0]

    assert changed_file.is_binary
    assert "Binary files a/assets/logo.png and b/assets/logo.png differ" in (
        changed_file.to_diff_text()
    )


@pytest.mark.case("UT-M1-004")
def test_line_map_excludes_deleted_lines_and_reports_commentable_lines() -> None:
    diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -1,2 +1,2 @@\n"
        " kept = 1\n"
        "-removed = 2\n"
        "+added = 2\n"
    )
    changed_file = UnifiedDiffParser().parse(diff).files[0]

    line_map = build_new_line_map(changed_file)

    assert line_map.commentable_lines == frozenset({2})
    assert line_map.present_lines == frozenset({1, 2})
    assert line_map.is_commentable(2)
    assert not line_map.is_commentable(1)
    assert line_map.covers(LineRange(1, 2))
    assert not line_map.covers(LineRange(1, 3))
    assert line_map.intersects_commentable(LineRange(1, 2))
    assert not line_map.intersects_commentable(LineRange(1, 1))


@pytest.mark.case("UT-M1-005")
def test_filters_binary_file() -> None:
    diff = (
        "diff --git a/assets/logo.png b/assets/logo.png\n"
        "Binary files a/assets/logo.png and b/assets/logo.png differ\n"
    )
    changed_file = UnifiedDiffParser().parse(diff).files[0]

    assert FileFilter().decide(changed_file).reason is FilterReason.BINARY


@pytest.mark.case("UT-M1-005")
def test_filters_deleted_file() -> None:
    diff = (
        "diff --git a/src/gone.py b/src/gone.py\n"
        "deleted file mode 100644\n"
        "--- a/src/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-value = 1\n"
    )
    changed_file = UnifiedDiffParser().parse(diff).files[0]

    assert FileFilter().decide(changed_file).reason is FilterReason.DELETED


@pytest.mark.case("UT-M1-005")
def test_filters_file_exceeding_configured_diff_size() -> None:
    changed_file = UnifiedDiffParser().parse(_modification("src/big.py")).files[0]

    result = FileFilter(FilterPolicy(max_diff_bytes=10)).decide(changed_file)

    assert result.reason is FilterReason.MINIFIED


@pytest.mark.case("UT-M1-005")
def test_filters_modification_without_any_added_line() -> None:
    diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -1,2 +1 @@\n"
        " kept = 1\n"
        "-removed = 2\n"
    )
    changed_file = UnifiedDiffParser().parse(diff).files[0]

    assert FileFilter().decide(changed_file).reason is FilterReason.NO_COMMENTABLE_LINE


@pytest.mark.case("UT-M1-005")
@pytest.mark.parametrize(
    ("decision", "expected_is_reviewable"),
    [
        (FileDecision.REVIEW, True),
        (FileDecision.LIGHTWEIGHT, True),
        (FileDecision.FILTER, False),
    ],
)
def test_only_filtered_files_are_excluded_from_review(
    decision: FileDecision,
    expected_is_reviewable: bool,
) -> None:
    result = FileFilterResult(
        path=NormalizedPath("src/a.py"),
        decision=decision,
        reason=None,
    )

    assert result.is_reviewable is expected_is_reviewable


def _modification(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1,2 @@\n"
        " value = 1\n"
        "+value = 2\n"
    )
