from __future__ import annotations

import re

import pytest

from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_lines, hash_text
from self_improving_code_review.domain.common.tokens import CHARS_PER_TOKEN, estimate_tokens
from self_improving_code_review.domain.common.values import LineRange, NormalizedPath
from self_improving_code_review.messages import Messages


@pytest.mark.case("UT-M1-003")
@pytest.mark.parametrize(
    ("value", "expected_message"),
    [
        ("", Messages.PATH_EMPTY),
        ("src/\x00.py", "NUL"),
        ("src\\app.py", Messages.PATH_NOT_POSIX),
        ("/etc/passwd", Messages.PATH_NOT_RELATIVE),
        ("../secret.py", Messages.PATH_TRAVERSES_OUTSIDE),
        ("src/../../secret.py", Messages.PATH_TRAVERSES_OUTSIDE),
        (".", Messages.PATH_NOT_FILE),
        ("./", Messages.PATH_NOT_FILE),
    ],
)
def test_normalized_path_rejects_unsafe_values(value: str, expected_message: str) -> None:
    with pytest.raises(InvalidInputError, match=re.escape(expected_message)):
        NormalizedPath(value)


@pytest.mark.case("UT-M1-003")
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("./src//app.py", "src/app.py"),
        ("src/./app.py", "src/app.py"),
        ("src/app.py", "src/app.py"),
    ],
)
def test_normalized_path_removes_redundant_segments(value: str, expected: str) -> None:
    assert NormalizedPath(value).value == expected


@pytest.mark.case("UT-M1-003")
@pytest.mark.parametrize(
    ("value", "suffix", "name", "stem", "parent"),
    [
        ("src/pkg/app.PY", ".py", "app.PY", "app", "src/pkg"),
        ("README.md", ".md", "README.md", "README", ""),
        ("Makefile", "", "Makefile", "Makefile", ""),
    ],
)
def test_normalized_path_exposes_path_components(
    value: str,
    suffix: str,
    name: str,
    stem: str,
    parent: str,
) -> None:
    path = NormalizedPath(value)

    assert path.suffix == suffix
    assert path.name == name
    assert path.stem == stem
    assert path.parent == parent
    assert str(path) == path.value


@pytest.mark.case("UT-M1-003")
@pytest.mark.parametrize(
    ("start_line", "end_line", "expected_message"),
    [
        (0, 1, "start_line"),
        (-1, 1, "start_line"),
        (5, 4, "end_line"),
    ],
)
def test_line_range_rejects_invalid_bounds(
    start_line: int,
    end_line: int,
    expected_message: str,
) -> None:
    with pytest.raises(InvalidInputError, match=re.escape(expected_message)):
        LineRange(start_line=start_line, end_line=end_line)


@pytest.mark.case("UT-M1-003")
def test_line_range_containment_and_enumeration() -> None:
    line_range = LineRange(start_line=3, end_line=5)

    assert line_range.contains(3)
    assert line_range.contains(5)
    assert not line_range.contains(2)
    assert not line_range.contains(6)
    assert tuple(line_range.lines()) == (3, 4, 5)


@pytest.mark.case("UT-M1-003")
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("a", 1),
        ("a" * CHARS_PER_TOKEN, 1),
        ("a" * (CHARS_PER_TOKEN + 1), 2),
    ],
)
def test_estimate_tokens_rounds_up_from_characters(text: str, expected: int) -> None:
    assert estimate_tokens(text) == expected


@pytest.mark.case("UT-M1-003")
def test_hash_helpers_are_deterministic_and_order_sensitive() -> None:
    assert hash_text("value") == hash_text("value")
    assert hash_text("value") != hash_text("other")
    assert hash_lines(("a", "b")) == hash_lines(("a", "b"))
    assert hash_lines(("a", "b")) != hash_lines(("b", "a"))
