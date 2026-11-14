from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from self_improving_code_review.application.ports.diff_provider import DiffSource
from self_improving_code_review.domain.common.enums import DiffSourceKind
from self_improving_code_review.domain.common.errors import GitCommandError, InvalidInputError
from self_improving_code_review.infrastructure.git.git_diff_adapter import GitDiffAdapter
from self_improving_code_review.messages import Messages


@pytest.mark.case("UT-M1-012")
def test_repository_path_must_be_an_existing_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(InvalidInputError, match=re.escape(Messages.REPOSITORY_NOT_DIRECTORY)):
        GitDiffAdapter().acquire(missing, DiffSource(kind=DiffSourceKind.WORKSPACE))


@pytest.mark.case("UT-M1-012")
def test_non_repository_directory_fails_the_root_check(tmp_path: Path) -> None:
    with pytest.raises(GitCommandError, match="rev-parse"):
        GitDiffAdapter().acquire(tmp_path, DiffSource(kind=DiffSourceKind.WORKSPACE))


@pytest.mark.case("UT-M1-012")
def test_unborn_repository_reports_no_head_revision(tmp_path: Path) -> None:
    _git(tmp_path, "init")

    diff = GitDiffAdapter().acquire(tmp_path, DiffSource(kind=DiffSourceKind.WORKSPACE))

    assert diff.source_revision is None
    assert GitDiffAdapter().resolve_head_sha(tmp_path) is None


@pytest.mark.case("UT-M1-012")
@pytest.mark.parametrize(
    ("source", "expected_message"),
    [
        (DiffSource(kind=DiffSourceKind.RANGE), re.escape(Messages.DIFF_SOURCE_ARGUMENTS_MISMATCH)),
        (
            DiffSource(kind=DiffSourceKind.RANGE, base_revision="HEAD"),
            re.escape(Messages.DIFF_SOURCE_ARGUMENTS_MISMATCH),
        ),
        (
            DiffSource(kind=DiffSourceKind.COMMIT),
            re.escape(Messages.DIFF_SOURCE_ARGUMENTS_MISMATCH),
        ),
        (DiffSource(kind=DiffSourceKind.FILE), re.escape(Messages.DIFF_SOURCE_ARGUMENTS_MISMATCH)),
        (
            DiffSource(kind=DiffSourceKind.FILE, patch_path=Path("a.patch"), commit="HEAD"),
            re.escape(Messages.DIFF_SOURCE_ARGUMENTS_MISMATCH),
        ),
    ],
)
def test_source_specific_arguments_must_match_the_kind(
    tmp_path: Path,
    source: DiffSource,
    expected_message: str,
) -> None:
    _git(tmp_path, "init")

    with pytest.raises(InvalidInputError, match=expected_message):
        GitDiffAdapter().acquire(tmp_path, source)


@pytest.mark.case("UT-M1-012")
@pytest.mark.parametrize("revision", ["   ", "--upload-pack=evil"])
def test_revisions_that_could_become_options_are_rejected(tmp_path: Path, revision: str) -> None:
    _git(tmp_path, "init")

    with pytest.raises(InvalidInputError, match=re.escape(Messages.GIT_REVISION_INVALID)):
        GitDiffAdapter().acquire(
            tmp_path,
            DiffSource(kind=DiffSourceKind.COMMIT, commit=revision),
        )


@pytest.mark.case("UT-M1-012")
def test_missing_patch_file_is_rejected(tmp_path: Path) -> None:
    _git(tmp_path, "init")

    with pytest.raises(InvalidInputError, match=re.escape(Messages.PATCH_FILE_INSPECT_FAILED)):
        GitDiffAdapter().acquire(
            tmp_path,
            DiffSource(kind=DiffSourceKind.FILE, patch_path=tmp_path / "missing.patch"),
        )


@pytest.mark.case("UT-M1-012")
def test_patch_file_larger_than_the_limit_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch = tmp_path / "big.patch"
    patch.write_text("diff --git a/a b/a\n", encoding="utf-8")
    monkeypatch.setattr(
        "self_improving_code_review.infrastructure.git.git_diff_adapter._MAX_PATCH_BYTES",
        1,
    )

    with pytest.raises(InvalidInputError, match=re.escape(Messages.PATCH_FILE_TOO_LARGE)):
        GitDiffAdapter()._read_patch(patch)


@pytest.mark.case("UT-M1-012")
def test_git_execution_failure_is_translated(tmp_path: Path) -> None:
    adapter = GitDiffAdapter(timeout_seconds=30)

    with pytest.raises(GitCommandError, match=re.escape(Messages.GIT_COMMAND_NOT_EXECUTED)):
        adapter._run(tmp_path, ["git-does-not-exist", "status"])


@pytest.mark.case("UT-M1-012")
def test_git_timeout_is_translated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(subprocess, "run", timeout)

    with pytest.raises(GitCommandError, match=re.escape(Messages.GIT_COMMAND_NOT_EXECUTED)):
        GitDiffAdapter()._run(tmp_path, ["git", "status"])


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout
