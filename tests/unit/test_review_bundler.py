from __future__ import annotations

import pytest

from self_improving_code_review.application.review.bundler import SimpleBundler
from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.common.enums import FilterReason
from self_improving_code_review.domain.common.values import RunId

RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")


@pytest.mark.case("UT-M1-006")
def test_unrelated_files_become_one_bundle_each() -> None:
    files = UnifiedDiffParser().parse(_diff("src/user.py") + _diff("src/order.py")).files

    result = SimpleBundler(max_diff_tokens=8000).bundle(RUN_ID, files)

    assert [bundle.paths for bundle in result.bundles] == [("src/user.py",), ("src/order.py",)]
    assert [bundle.bundle_id.sequence for bundle in result.bundles] == [1, 2]


@pytest.mark.case("UT-M1-006")
def test_test_file_without_matching_subject_stays_separate() -> None:
    files = UnifiedDiffParser().parse(_diff("src/user.py") + _diff("tests/test_order.py")).files

    result = SimpleBundler(max_diff_tokens=8000).bundle(RUN_ID, files)

    assert len(result.bundles) == 2


@pytest.mark.case("UT-M1-006")
def test_test_suffix_variants_group_with_their_subject() -> None:
    files = UnifiedDiffParser().parse(_diff("src/user.py") + _diff("src/user.test.py")).files

    result = SimpleBundler(max_diff_tokens=8000).bundle(RUN_ID, files)

    assert result.bundles[0].paths == ("src/user.py", "src/user.test.py")


@pytest.mark.case("UT-M1-006")
def test_migrations_in_the_same_directory_are_grouped() -> None:
    files = (
        UnifiedDiffParser()
        .parse(_diff("migrations/0002_a.sql") + _diff("migrations/0003_b.sql"))
        .files
    )

    result = SimpleBundler(max_diff_tokens=8000).bundle(RUN_ID, files)

    assert result.bundles[0].paths == ("migrations/0002_a.sql", "migrations/0003_b.sql")


@pytest.mark.case("UT-M1-006")
def test_schema_files_in_different_directories_are_not_grouped() -> None:
    files = (
        UnifiedDiffParser()
        .parse(_diff("migrations/a/0002.sql") + _diff("migrations/b/0003.sql"))
        .files
    )

    result = SimpleBundler(max_diff_tokens=8000).bundle(RUN_ID, files)

    assert len(result.bundles) == 2


@pytest.mark.case("UT-M1-006")
def test_oversized_group_is_split_on_file_boundaries() -> None:
    files = (
        UnifiedDiffParser()
        .parse(
            _diff("migrations/0002_a.sql", added="a" * 300)
            + _diff("migrations/0003_b.sql", added="b" * 300)
        )
        .files
    )

    result = SimpleBundler(max_diff_tokens=120).bundle(RUN_ID, files)

    assert [bundle.paths for bundle in result.bundles] == [
        ("migrations/0002_a.sql",),
        ("migrations/0003_b.sql",),
    ]
    assert result.oversized_files == ()


@pytest.mark.case("UT-M1-006")
def test_oversized_file_is_split_on_hunk_boundaries() -> None:
    files = UnifiedDiffParser().parse(_two_hunk_diff("src/big.py", added="x" * 200)).files

    result = SimpleBundler(max_diff_tokens=80).bundle(RUN_ID, files)

    assert len(result.bundles) == 2
    assert [bundle.paths for bundle in result.bundles] == [("src/big.py",), ("src/big.py",)]
    assert [len(bundle.files[0].hunks) for bundle in result.bundles] == [1, 1]
    assert result.oversized_files == ()


@pytest.mark.case("IT-M1-010", "UT-M1-006")
def test_single_oversized_hunk_is_reported_and_not_sent() -> None:
    files = UnifiedDiffParser().parse(_diff("src/huge.py", added="x" * 2000)).files

    result = SimpleBundler(max_diff_tokens=50).bundle(RUN_ID, files)

    assert result.bundles == ()
    assert result.oversized_files[0].path.value == "src/huge.py"
    assert result.oversized_files[0].reason is FilterReason.BUDGET_EXCEEDED


@pytest.mark.case("UT-M1-006")
def test_file_with_one_acceptable_and_one_oversized_hunk_keeps_the_acceptable_hunk() -> None:
    files = UnifiedDiffParser().parse(_mixed_size_diff("src/mixed.py")).files

    result = SimpleBundler(max_diff_tokens=60).bundle(RUN_ID, files)

    assert len(result.bundles) == 1
    assert len(result.bundles[0].files[0].hunks) == 1
    assert result.oversized_files[0].path.value == "src/mixed.py"


@pytest.mark.case("UT-M1-006")
def test_pending_chunk_is_flushed_before_splitting_an_oversized_file() -> None:
    files = (
        UnifiedDiffParser()
        .parse(
            _diff("migrations/0002_small.sql")
            + _two_hunk_diff("migrations/0003_large.sql", added="x" * 200)
        )
        .files
    )

    result = SimpleBundler(max_diff_tokens=80).bundle(RUN_ID, files)

    assert [bundle.paths for bundle in result.bundles] == [
        ("migrations/0002_small.sql",),
        ("migrations/0003_large.sql",),
        ("migrations/0003_large.sql",),
    ]
    assert result.oversized_files == ()


@pytest.mark.case("IT-M1-010", "UT-M1-006")
def test_empty_input_produces_no_bundle() -> None:
    result = SimpleBundler(max_diff_tokens=8000).bundle(RUN_ID, ())

    assert result.bundles == ()
    assert result.oversized_files == ()


@pytest.mark.case("UT-M1-006")
def test_bundle_diff_text_and_tokens_describe_the_sent_payload() -> None:
    files = UnifiedDiffParser().parse(_diff("src/user.py")).files

    bundle = SimpleBundler(max_diff_tokens=8000).bundle(RUN_ID, files).bundles[0]

    assert bundle.diff_text == files[0].to_diff_text()
    assert bundle.diff_tokens > 0


def _diff(path: str, *, added: str = "value = 2") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1,2 @@\n"
        " value = 1\n"
        f"+{added}\n"
    )


def _two_hunk_diff(path: str, *, added: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1,2 @@\n"
        " value = 1\n"
        f"+{added}\n"
        "@@ -20 +21,2 @@\n"
        " other = 1\n"
        f"+{added}\n"
    )


def _mixed_size_diff(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1,2 @@\n"
        " a = 1\n"
        "+b = 2\n"
        "@@ -20 +21,2 @@\n"
        " c = 1\n"
        f"+{'x' * 2000}\n"
    )
