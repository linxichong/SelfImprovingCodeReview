from __future__ import annotations

import pytest

from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.common.values import LineRange, NormalizedPath
from self_improving_code_review.domain.finding.fingerprint import (
    UNKNOWN_SYMBOL,
    build_fingerprint,
    find_enclosing_symbol,
)


@pytest.mark.case("UT-M1-010")
@pytest.mark.parametrize(
    ("declaration", "expected_symbol"),
    [
        ("def load_user():", "load_user"),
        ("async def load_user():", "load_user"),
        ("class UserLoader:", "UserLoader"),
        ("export async function loadUser() {", "loadUser"),
        ("public static String loadUser(", "loadUser"),
        ("func (s *Store) LoadUser() {", "LoadUser"),
        ("fn load_user() {", "load_user"),
    ],
)
def test_finds_enclosing_symbol_for_supported_languages(
    declaration: str,
    expected_symbol: str,
) -> None:
    changed_file = UnifiedDiffParser().parse(_diff(declaration)).files[0]

    symbol = find_enclosing_symbol(changed_file, LineRange(3, 3))

    assert symbol == expected_symbol


@pytest.mark.case("UT-M1-010")
def test_returns_unknown_symbol_when_no_declaration_precedes_the_range() -> None:
    changed_file = UnifiedDiffParser().parse(_diff("value = 1")).files[0]

    assert find_enclosing_symbol(changed_file, LineRange(3, 3)) == UNKNOWN_SYMBOL


@pytest.mark.case("UT-M1-010")
def test_nearest_preceding_declaration_wins() -> None:
    diff = (
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1,3 +1,4 @@\n"
        " def outer():\n"
        " def inner():\n"
        " value = 1\n"
        "+unsafe = user.name\n"
    )
    changed_file = UnifiedDiffParser().parse(diff).files[0]

    assert find_enclosing_symbol(changed_file, LineRange(4, 4)) == "inner"


@pytest.mark.case("UT-M1-010")
def test_declarations_after_the_range_and_deleted_declarations_are_ignored() -> None:
    diff = (
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-def removed():\n"
        "+unsafe = user.name\n"
        " value = 1\n"
        " def later():\n"
    )
    changed_file = UnifiedDiffParser().parse(diff).files[0]

    assert find_enclosing_symbol(changed_file, LineRange(1, 1)) == UNKNOWN_SYMBOL


@pytest.mark.case("UT-M1-010")
def test_fingerprint_substitutes_unknown_for_missing_symbol_and_entity() -> None:
    fingerprint = build_fingerprint(
        rule_id="PYTHON.NULL_DEREFERENCE",
        path=NormalizedPath("src/app.py"),
        enclosing_symbol="",
        primary_entity="",
    )

    assert fingerprint == "PYTHON.NULL_DEREFERENCE:src/app.py:unknown:unknown"


@pytest.mark.case("UT-M1-010")
def test_fingerprint_differs_when_any_component_differs() -> None:
    base = build_fingerprint(
        rule_id="PYTHON.A",
        path=NormalizedPath("src/app.py"),
        enclosing_symbol="load",
        primary_entity="user",
    )
    other_entity = build_fingerprint(
        rule_id="PYTHON.A",
        path=NormalizedPath("src/app.py"),
        enclosing_symbol="load",
        primary_entity="order",
    )

    assert base != other_entity


def _diff(declaration: str) -> str:
    return (
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1,2 +1,3 @@\n"
        f" {declaration}\n"
        " value = 1\n"
        "+unsafe = user.name\n"
    )
