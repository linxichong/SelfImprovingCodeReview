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


@pytest.mark.case("IT-M1-003a")
def test_git_adapter_supports_workspace_range_commit_and_file(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Test User")
    source = repository / "example.py"
    source.write_text("value = 1\n", encoding="utf-8")
    _git(repository, "add", "example.py")
    _git(repository, "commit", "-m", "base")
    base = _git(repository, "rev-parse", "HEAD").strip()
    source.write_text("value = 2\n", encoding="utf-8")
    _git(repository, "commit", "-am", "head")
    head = _git(repository, "rev-parse", "HEAD").strip()
    adapter = GitDiffAdapter()

    range_diff = adapter.acquire(
        repository,
        DiffSource(
            kind=DiffSourceKind.RANGE,
            base_revision=base,
            head_revision=head,
        ),
    )
    commit_diff = adapter.acquire(
        repository,
        DiffSource(kind=DiffSourceKind.COMMIT, commit=head),
    )
    source.write_text("value = 3\n", encoding="utf-8")
    workspace_diff = adapter.acquire(repository, DiffSource(kind=DiffSourceKind.WORKSPACE))
    patch = repository / "change.patch"
    patch.write_text(workspace_diff.text, encoding="utf-8")
    file_diff = adapter.acquire(
        repository,
        DiffSource(kind=DiffSourceKind.FILE, patch_path=patch),
    )

    assert range_diff.source_revision == head
    assert "-value = 1" in range_diff.text
    assert commit_diff.source_revision == head
    assert "+value = 2" in commit_diff.text
    assert "+value = 3" in workspace_diff.text
    assert file_diff.text == workspace_diff.text
    assert adapter.resolve_head_sha(repository) == head


def test_git_adapter_rejects_nested_root_mixed_source_and_bad_revision(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    nested = repository / "nested"
    nested.mkdir(parents=True)
    _git(repository, "init")
    adapter = GitDiffAdapter()

    with pytest.raises(InvalidInputError, match=re.escape(Messages.REPOSITORY_NOT_GIT_ROOT)):
        adapter.acquire(nested, DiffSource(kind=DiffSourceKind.WORKSPACE))
    with pytest.raises(InvalidInputError, match=re.escape(Messages.DIFF_SOURCE_ARGUMENTS_MISMATCH)):
        adapter.acquire(
            repository,
            DiffSource(kind=DiffSourceKind.WORKSPACE, commit="HEAD"),
        )
    with pytest.raises(InvalidInputError, match=re.escape(Messages.GIT_REVISION_INVALID)):
        adapter.acquire(
            repository,
            DiffSource(kind=DiffSourceKind.COMMIT, commit="--help"),
        )
    with pytest.raises(GitCommandError):
        adapter.acquire(
            repository,
            DiffSource(kind=DiffSourceKind.COMMIT, commit="missing"),
        )


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout
