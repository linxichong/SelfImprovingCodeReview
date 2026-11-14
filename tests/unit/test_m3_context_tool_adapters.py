"""M3 のコード入力元と限定ツール実装を検証する。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from self_improving_code_review.application.review.context_tools import ValidatedToolRequest
from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.common.enums import DiffSourceKind, ToolStatus
from self_improving_code_review.domain.common.errors import GitCommandError, InvalidInputError
from self_improving_code_review.domain.common.values import BundleId, NormalizedPath, RunId
from self_improving_code_review.infrastructure.context_tools import repository_source
from self_improving_code_review.infrastructure.context_tools.repository_source import (
    GitContextSource,
    RepositoryContextSourceFactory,
    WorktreeContextSource,
)
from self_improving_code_review.infrastructure.context_tools.tools import (
    RepositoryContextToolExecutor,
)
from self_improving_code_review.infrastructure.git import file_context_verifier
from self_improving_code_review.infrastructure.git.file_context_verifier import (
    GitFileContextVerifier,
)
from self_improving_code_review.infrastructure.git.revision_content_reader import (
    GitRevisionContentReader,
)
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.path_policy import PathPolicy

RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")
BUNDLE_ID = BundleId(RUN_ID, 1)
REVISION = "a" * 40


class Source:
    """パス別テキストを返すテスト用コード入力元。"""

    source_revision = None

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def read_text(self, path: NormalizedPath) -> str | None:
        return self.files.get(path.value)

    def list_paths(
        self, scope: tuple[NormalizedPath, ...], *, max_files: int
    ) -> tuple[NormalizedPath, ...]:
        prefixes = tuple(item.value.rstrip("/") + "/" for item in scope)
        selected = [
            path
            for path in sorted(self.files)
            if not prefixes or path.startswith(prefixes) or NormalizedPath(path) in scope
        ]
        return tuple(NormalizedPath(path) for path in selected[:max_files])


@pytest.mark.case("UT-M3-008")
def test_worktree_source_reads_lists_and_detects_changes(tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    path = source_dir / "app.py"
    path.write_text("value = 1\n", encoding="utf-8")
    (source_dir / "other.py").write_text("other = 1\n", encoding="utf-8")
    (source_dir / "empty").mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("outside = 1\n", encoding="utf-8")
    (source_dir / "outside.py").symlink_to(outside)
    source = WorktreeContextSource(tmp_path, max_file_bytes=100)

    assert source.source_revision is None
    assert source.read_text(NormalizedPath("src/app.py")) == "value = 1\n"
    assert source.read_text(NormalizedPath("missing.py")) is None
    assert source.read_text(NormalizedPath("src/empty")) is None
    assert source.read_text(NormalizedPath("src/outside.py")) is None
    assert [item.value for item in source.list_paths((NormalizedPath("src"),), max_files=1)] == [
        "src/app.py"
    ]
    assert [item.value for item in source.list_paths((NormalizedPath("src"),), max_files=10)] == [
        "src/app.py",
        "src/other.py",
    ]
    assert source.list_paths((NormalizedPath("missing"),), max_files=10) == ()

    path.write_text("value = 2\n", encoding="utf-8")
    with pytest.raises(InvalidInputError, match=re.escape(Messages.CONTEXT_SOURCE_CHANGED)):
        source.read_text(NormalizedPath("src/app.py"))
    assert source.fingerprints[0].path == NormalizedPath("src/app.py")


def test_worktree_source_rejects_large_and_non_utf8_files(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("0123456789", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"\xff")
    source = WorktreeContextSource(tmp_path, max_file_bytes=5)

    with pytest.raises(InvalidInputError, match=re.escape(Messages.CONTEXT_SOURCE_TOO_LARGE)):
        source.read_text(NormalizedPath("large.txt"))
    with pytest.raises(InvalidInputError, match="UTF-8"):
        WorktreeContextSource(tmp_path, max_file_bytes=100).read_text(NormalizedPath("binary.bin"))


def test_worktree_source_handles_read_error_and_file_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "single.py"
    path.write_text("value = 1", encoding="utf-8")
    source = WorktreeContextSource(tmp_path, max_file_bytes=100)

    assert source.list_paths((NormalizedPath("single.py"),), max_files=10) == (
        NormalizedPath("single.py"),
    )
    monkeypatch.setattr(Path, "read_bytes", lambda _path: (_ for _ in ()).throw(OSError()))
    assert source.read_text(NormalizedPath("single.py")) is None


@pytest.mark.case("UT-M3-008")
def test_worktree_source_detects_change_during_single_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """読取前後のファイル識別情報が変わった場合は内容を採用しない。"""
    path = tmp_path / "app.py"
    path.write_text("value = 1", encoding="utf-8")
    original_read = Path.read_bytes

    def read_and_change(candidate: Path) -> bytes:
        """対象ファイルの読取直後にサイズを変え、時刻精度に依存せず競合を再現する."""
        payload = original_read(candidate)
        if candidate == path:
            candidate.write_bytes(payload + b"\n")
        return payload

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "read_bytes", read_and_change)
        with pytest.raises(InvalidInputError, match=re.escape(Messages.CONTEXT_SOURCE_CHANGED)):
            WorktreeContextSource(tmp_path, max_file_bytes=100).read_text(NormalizedPath("app.py"))
    assert path.read_bytes() == b"value = 1\n"


@pytest.mark.case("CT-M3-001", "UT-M3-008")
def test_git_source_reads_fixed_revision_and_filters_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(GitRevisionContentReader, "read_text", lambda *_args: "value = 1")
    monkeypatch.setattr(
        repository_source.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=b"README.md\nsrc/app.py\nsrc/other.py\n",
        ),
    )
    source = GitContextSource(tmp_path, REVISION, max_file_bytes=100)

    assert source.source_revision == REVISION
    assert source.read_text(NormalizedPath("src/app.py")) == "value = 1"
    assert source.fingerprints[0].source_revision == REVISION
    assert [item.value for item in source.list_paths((NormalizedPath("src"),), max_files=1)] == [
        "src/app.py"
    ]


@pytest.mark.case("CT-M3-001")
def test_git_source_validates_revision_size_and_command_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(InvalidInputError, match=re.escape(Messages.CONTEXT_REVISION_INVALID)):
        GitContextSource(tmp_path, "main", max_file_bytes=100)
    monkeypatch.setattr(GitRevisionContentReader, "read_text", lambda *_args: "x" * 20)
    with pytest.raises(InvalidInputError, match=re.escape(Messages.CONTEXT_SOURCE_TOO_LARGE)):
        GitContextSource(tmp_path, REVISION, max_file_bytes=5).read_text(
            NormalizedPath("src/app.py")
        )

    source = GitContextSource(tmp_path, REVISION, max_file_bytes=100)
    monkeypatch.setattr(
        repository_source.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=b""),
    )
    with pytest.raises(GitCommandError):
        source.list_paths((), max_files=10)
    monkeypatch.setattr(GitRevisionContentReader, "read_text", lambda *_args: None)
    assert source.read_text(NormalizedPath("missing.py")) is None
    monkeypatch.setattr(
        repository_source.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=b"\xff"),
    )
    with pytest.raises(GitCommandError):
        source.list_paths((), max_files=10)
    monkeypatch.setattr(
        repository_source.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("git", 1)),
    )
    with pytest.raises(GitCommandError):
        source.list_paths((), max_files=10)


def test_source_factory_selects_git_or_worktree(tmp_path: Path) -> None:
    factory = RepositoryContextSourceFactory(max_file_bytes=100)

    assert isinstance(factory(tmp_path, None, DiffSourceKind.WORKSPACE), WorktreeContextSource)
    assert isinstance(factory(tmp_path, REVISION, DiffSourceKind.COMMIT), GitContextSource)
    with pytest.raises(InvalidInputError):
        factory(tmp_path, None, DiffSourceKind.RANGE)


def test_file_context_verifier_returns_git_result_and_translates_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """逆適用検査の終了状態を返し、実行不能は基盤例外へ変換する。"""
    verifier = GitFileContextVerifier(timeout_seconds=1)
    monkeypatch.setattr(
        file_context_verifier.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    assert verifier(tmp_path, "patch")
    monkeypatch.setattr(
        file_context_verifier.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )
    assert not verifier(tmp_path, "patch")
    monkeypatch.setattr(
        file_context_verifier.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("git", 1)),
    )
    with pytest.raises(GitCommandError):
        verifier(tmp_path, "patch")


def test_repository_tools_read_changed_file_and_line_range(tmp_path: Path) -> None:
    changed = UnifiedDiffParser().parse(_diff("src/app.py", "value = 2")).files
    executor = RepositoryContextToolExecutor(changed, path_policy=PathPolicy(), max_results=2)
    source = Source({"src/app.py": "one\ntwo\nthree"})

    changed_result = executor.execute(_request(tmp_path, "get_changed_file"), source)
    read_result = executor.execute(
        _request(tmp_path, "read_file", start_line=2, end_line=9), source
    )
    missing = executor.execute(
        _request(tmp_path, "read_file", path="missing.py", start_line=1, end_line=2), source
    )

    assert changed_result.status is ToolStatus.SUCCESS
    assert read_result.fragments[0].text == "two\nthree"
    assert missing.status is ToolStatus.NOT_FOUND


@pytest.mark.case("CT-M3-002")
def test_repository_tools_find_symbols_and_literal_code(tmp_path: Path) -> None:
    executor = RepositoryContextToolExecutor((), path_policy=PathPolicy(), max_results=1)
    source = Source(
        {
            "src/app.py": "def load_user():\n    target = 1\n    return target\n",
            "src/broken.py": "def broken(",
            ".git/config": "target",
        }
    )

    symbol = executor.execute(_request(tmp_path, "find_symbol", query="load_user"), source)
    code = executor.execute(_request(tmp_path, "search_code", query="target"), source)
    absent = executor.execute(_request(tmp_path, "find_symbol", query="absent"), source)

    assert symbol.fragments[0].start_line == 1
    assert code.fragments[0].path == NormalizedPath("src/app.py")
    assert absent.status is ToolStatus.NOT_FOUND


def test_repository_tools_return_closed_errors_for_missing_internal_arguments(
    tmp_path: Path,
) -> None:
    executor = RepositoryContextToolExecutor((), path_policy=PathPolicy(), max_results=10)
    source = Source({"src/app.py": "value = 1"})

    no_path = ValidatedToolRequest(
        RUN_ID, BUNDLE_ID, tmp_path, None, "get_changed_file", None, (), None, None, None, "h"
    )
    no_range = ValidatedToolRequest(
        RUN_ID,
        BUNDLE_ID,
        tmp_path,
        None,
        "read_file",
        NormalizedPath("src/app.py"),
        (),
        None,
        None,
        None,
        "h",
    )
    no_query_symbol = ValidatedToolRequest(
        RUN_ID, BUNDLE_ID, tmp_path, None, "find_symbol", None, (), None, None, None, "h"
    )
    no_query_search = ValidatedToolRequest(
        RUN_ID, BUNDLE_ID, tmp_path, None, "search_code", None, (), None, None, None, "h"
    )
    missing_changed = executor.execute(_request(tmp_path, "get_changed_file"), source)
    missing_line = executor.execute(
        _request(tmp_path, "read_file", start_line=9, end_line=10), source
    )

    assert executor.execute(no_path, source).reason_code == "PATH_REQUIRED"
    assert executor.execute(no_range, source).reason_code == "RANGE_REQUIRED"
    assert executor.execute(no_query_symbol, source).reason_code == "QUERY_REQUIRED"
    assert executor.execute(no_query_search, source).reason_code == "QUERY_REQUIRED"
    assert missing_changed.reason_code == "CHANGED_FILE_NOT_FOUND"
    assert missing_line.reason_code == "LINE_NOT_FOUND"


def test_repository_search_skips_disappeared_files_and_returns_partial_results(
    tmp_path: Path,
) -> None:
    class DisappearingSource(Source):
        """一覧化後に一部ファイルが消える入力元。"""

        def list_paths(
            self, scope: tuple[NormalizedPath, ...], *, max_files: int
        ) -> tuple[NormalizedPath, ...]:
            del scope
            return (
                NormalizedPath("src/missing.py"),
                NormalizedPath("src/app.py"),
            )[:max_files]

    source = DisappearingSource({"src/app.py": "target = 1\nother = 2"})
    executor = RepositoryContextToolExecutor((), path_policy=PathPolicy(), max_results=2)

    symbol = executor.execute(_request(tmp_path, "find_symbol", query="absent"), source)
    search = executor.execute(_request(tmp_path, "search_code", query="target"), source)
    absent_search = executor.execute(_request(tmp_path, "search_code", query="absent"), source)

    assert symbol.status is ToolStatus.NOT_FOUND
    assert search.status is ToolStatus.SUCCESS
    assert len(search.fragments) == 1
    assert absent_search.reason_code == "CODE_NOT_FOUND"


def _request(
    repository: Path,
    tool_name: str,
    *,
    path: str = "src/app.py",
    query: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> ValidatedToolRequest:
    return ValidatedToolRequest(
        RUN_ID,
        BUNDLE_ID,
        repository,
        None,
        tool_name,
        NormalizedPath(path),
        (),
        query,
        start_line,
        end_line,
        "hash",
    )


def _diff(path: str, added: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1,2 @@\n"
        " value = 1\n"
        f"+{added}\n"
    )
