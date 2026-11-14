"""Git 固定版と作業ツリーの上限付きコード読取."""

from __future__ import annotations

import os
import re
import stat
import subprocess  # nosec B404
from pathlib import Path

from self_improving_code_review.application.ports.context_source import (
    ContextSource,
    SourceFingerprint,
)
from self_improving_code_review.domain.common.enums import DiffSourceKind
from self_improving_code_review.domain.common.errors import (
    GitCommandError,
    InvalidInputError,
)
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.common.values import NormalizedPath
from self_improving_code_review.infrastructure.git.revision_content_reader import (
    GitRevisionContentReader,
)
from self_improving_code_review.messages import Messages

__all__ = ["GitContextSource", "RepositoryContextSourceFactory", "WorktreeContextSource"]

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


class WorktreeContextSource(ContextSource):
    """作業ツリーから同一実行中に固定した UTF-8 内容を読む."""

    def __init__(self, repository: Path, *, max_file_bytes: int) -> None:
        """リポジトリルートとファイル上限を保持する."""
        self._repository = repository.resolve()
        self._max_file_bytes = max_file_bytes
        self._content_hashes: dict[str, str] = {}

    @property
    def source_revision(self) -> str | None:
        """作業ツリー入力のため None を返す."""
        return None

    @property
    def fingerprints(self) -> tuple[SourceFingerprint, ...]:
        """読み取った作業ツリーファイルの内容ハッシュを返す."""
        return tuple(
            SourceFingerprint(0, NormalizedPath(path), None, content_hash)
            for path, content_hash in sorted(self._content_hashes.items())
        )

    def read_text(self, path: NormalizedPath) -> str | None:
        """通常ファイルを読み、初回内容ハッシュからの変更を拒否する."""
        candidate = (self._repository / path.value).resolve()
        if not candidate.is_relative_to(self._repository):
            return None
        try:
            before = candidate.stat()
            if not stat.S_ISREG(before.st_mode):
                return None
            if before.st_size > self._max_file_bytes:
                raise InvalidInputError(Messages.CONTEXT_SOURCE_TOO_LARGE)
            payload = candidate.read_bytes()
            after = candidate.stat()
        except OSError:
            return None
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if before_identity != after_identity or len(payload) != after.st_size:
            raise InvalidInputError(Messages.CONTEXT_SOURCE_CHANGED)
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidInputError(Messages.CONTEXT_SOURCE_NOT_UTF8) from exc
        content_hash = hash_text(text)
        previous = self._content_hashes.setdefault(path.value, content_hash)
        if previous != content_hash:
            raise InvalidInputError(Messages.CONTEXT_SOURCE_CHANGED)
        return text

    def list_paths(
        self,
        path_scope: tuple[NormalizedPath, ...],
        *,
        max_files: int,
    ) -> tuple[NormalizedPath, ...]:
        """指定範囲の通常ファイルを安定順で返す."""
        roots = (
            tuple((self._repository / item.value).resolve() for item in path_scope)
            if path_scope
            else (self._repository,)
        )
        paths: set[str] = set()
        for root in roots:
            if not root.is_relative_to(self._repository) or not root.exists():
                continue
            if root.is_file():
                relative = root.relative_to(self._repository).as_posix()
                paths.add(relative)
                continue
            for directory, directory_names, file_names in os.walk(root, followlinks=False):
                directory_names[:] = sorted(directory_names)
                for file_name in sorted(file_names):
                    candidate = (Path(directory) / file_name).resolve()
                    if candidate.is_relative_to(self._repository) and candidate.is_file():
                        paths.add(candidate.relative_to(self._repository).as_posix())
                        if len(paths) >= max_files:
                            return tuple(NormalizedPath(item) for item in sorted(paths))
        return tuple(NormalizedPath(item) for item in sorted(paths))


class GitContextSource(ContextSource):
    """解決済みコミットの Git オブジェクトだけを読み取る."""

    def __init__(
        self,
        repository: Path,
        revision: str,
        *,
        max_file_bytes: int,
        timeout_seconds: int = 30,
    ) -> None:
        """固定 SHA、読取上限、タイムアウトを検証して保持する."""
        if _COMMIT_SHA.fullmatch(revision) is None:
            raise InvalidInputError(Messages.CONTEXT_REVISION_INVALID)
        self._repository = repository.resolve()
        self._revision = revision
        self._max_file_bytes = max_file_bytes
        self._timeout_seconds = timeout_seconds
        self._reader = GitRevisionContentReader(
            self._repository,
            timeout_seconds=timeout_seconds,
        )
        self._content_hashes: dict[str, str] = {}

    @property
    def source_revision(self) -> str:
        """固定したコミット SHA を返す."""
        return self._revision

    @property
    def fingerprints(self) -> tuple[SourceFingerprint, ...]:
        """読み取った固定コミットファイルの内容ハッシュを返す."""
        return tuple(
            SourceFingerprint(0, NormalizedPath(path), self._revision, content_hash)
            for path, content_hash in sorted(self._content_hashes.items())
        )

    def read_text(self, path: NormalizedPath) -> str | None:
        """固定コミットの UTF-8 ファイルを返す."""
        text = self._reader.read_text(self._revision, path)
        if text is not None and len(text.encode("utf-8")) > self._max_file_bytes:
            raise InvalidInputError(Messages.CONTEXT_SOURCE_TOO_LARGE)
        if text is not None:
            self._content_hashes[path.value] = hash_text(text)
        return text

    def list_paths(
        self,
        path_scope: tuple[NormalizedPath, ...],
        *,
        max_files: int,
    ) -> tuple[NormalizedPath, ...]:
        """固定コミットに含まれるファイルを安定順で返す."""
        try:
            # Git は PATH 上の正式な実行ファイルを固定引数配列かつ shell=False で呼び出す。
            completed = subprocess.run(  # nosec B603, B607
                ["git", "ls-tree", "-r", "--name-only", self._revision],
                cwd=self._repository,
                capture_output=True,
                check=False,
                timeout=self._timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitCommandError(Messages.CONTEXT_SOURCE_READ_FAILED) from exc
        if completed.returncode != 0:
            raise GitCommandError(Messages.CONTEXT_SOURCE_READ_FAILED)
        try:
            candidates = completed.stdout.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise GitCommandError(Messages.CONTEXT_SOURCE_READ_FAILED) from exc
        prefixes = tuple(item.value.rstrip("/") + "/" for item in path_scope)
        selected = (
            path
            for path in sorted(candidates)
            if not prefixes
            or path in {item.value for item in path_scope}
            or path.startswith(prefixes)
        )
        return tuple(NormalizedPath(path) for path in list(selected)[:max_files])


class RepositoryContextSourceFactory:
    """差分種類に応じて Git 固定版または作業ツリー入力元を生成する."""

    def __init__(self, *, max_file_bytes: int) -> None:
        """一ファイルの読取上限を保持する."""
        self._max_file_bytes = max_file_bytes

    def __call__(
        self,
        repository: Path,
        source_revision: str | None,
        source_kind: DiffSourceKind,
    ) -> ContextSource:
        """コミットまたは範囲入力だけを Git 固定版として返す."""
        if source_kind in {DiffSourceKind.COMMIT, DiffSourceKind.RANGE}:
            if source_revision is None:
                raise InvalidInputError(Messages.CONTEXT_REVISION_INVALID)
            return GitContextSource(
                repository,
                source_revision,
                max_file_bytes=self._max_file_bytes,
            )
        return WorktreeContextSource(
            repository,
            max_file_bytes=self._max_file_bytes,
        )
