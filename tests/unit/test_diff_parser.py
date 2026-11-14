from __future__ import annotations

import re

import pytest

from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.common.enums import ChangeType, LineKind
from self_improving_code_review.domain.common.errors import InvalidDiffError
from self_improving_code_review.messages import Messages


@pytest.mark.case("UT-M1-004")
@pytest.mark.parametrize(
    ("metadata", "expected_type"),
    [
        ("new file mode 100644", ChangeType.ADDED),
        ("deleted file mode 100644", ChangeType.DELETED),
        ("rename from src/old.py\nrename to src/new.py", ChangeType.RENAMED),
    ],
)
def test_parses_file_change_types(metadata: str, expected_type: ChangeType) -> None:
    old_path = "src/old.py" if expected_type is ChangeType.RENAMED else "src/item.py"
    new_path = "src/new.py" if expected_type is ChangeType.RENAMED else "src/item.py"
    diff = (
        f"diff --git a/{old_path} b/{new_path}\n"
        f"{metadata}\n"
        "--- a/src/item.py\n"
        "+++ b/src/item.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "\\ No newline at end of file\n"
    )

    changed_file = UnifiedDiffParser().parse(diff).files[0]

    assert changed_file.change_type is expected_type
    assert changed_file.hunks[0].lines[-1].kind is LineKind.ADDED
    assert changed_file.hunks[0].lines[-1].is_commentable


@pytest.mark.case("UT-M1-004")
def test_parses_binary_diff_without_hunks() -> None:
    diff = (
        "diff --git a/assets/logo.png b/assets/logo.png\n"
        "Binary files a/assets/logo.png and b/assets/logo.png differ\n"
    )

    changed_file = UnifiedDiffParser().parse(diff).files[0]

    assert changed_file.is_binary
    assert changed_file.hunks == ()


@pytest.mark.case("UT-M1-004")
def test_rejects_mismatched_hunk_counts() -> None:
    diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-old\n"
        "+new\n"
    )

    with pytest.raises(InvalidDiffError, match=re.escape(Messages.HUNK_LINE_COUNT_MISMATCH)):
        UnifiedDiffParser().parse(diff)


@pytest.mark.case("UT-M1-004")
def test_rejects_repository_escape_path() -> None:
    diff = "diff --git a/../secret b/../secret\n"

    with pytest.raises(InvalidDiffError, match=re.escape(Messages.PATH_TRAVERSES_OUTSIDE)):
        UnifiedDiffParser().parse(diff)


@pytest.mark.case("UT-M1-004")
def test_empty_diff_returns_an_empty_changeset() -> None:
    assert UnifiedDiffParser().parse("").files == ()
