"""Git を使用した安全な差分取得."""

from __future__ import annotations

# このアダプターは Git CLI を必要とし、常にシェルを介さずに呼び出す。
import subprocess  # nosec B404
from pathlib import Path
from typing import cast

from self_improving_code_review.application.ports.diff_provider import DiffSource, RawDiff
from self_improving_code_review.domain.common.enums import DiffSourceKind
from self_improving_code_review.domain.common.errors import GitCommandError, InvalidInputError
from self_improving_code_review.messages import Messages

__all__ = ["GitDiffAdapter"]

_MAX_PATCH_BYTES = 20 * 1024 * 1024


class GitDiffAdapter:
    """シェルを介さずに決定的な統一差分を取得する."""

    def __init__(self, *, timeout_seconds: int = 30) -> None:
        """Git コマンドのタイムアウトを保持する."""
        self._timeout_seconds = timeout_seconds

    def acquire(self, repository_path: Path, source: DiffSource) -> RawDiff:
        """指定された入力元から再現可能な統一差分を取得する.

        Args:
            repository_path: Git ルートとして検証するディレクトリ。
            source: 作業ツリー、範囲、コミット、パッチファイルの排他的な指定。

        Returns:
            統一差分本文と、取得可能な場合は入力元コミット SHA。

        Raises:
            InvalidInputError: 入力元の必須値がない、または指定が混在する場合。
            GitCommandError: リポジトリ検証、リビジョン解決、Git 実行に失敗した場合。
        """
        repository_root = self._verify_repository_root(repository_path)
        self._validate_source(source)

        if source.kind is DiffSourceKind.FILE:
            patch_path = cast(Path, source.patch_path)
            text = self._read_patch(patch_path)
            return RawDiff(text=text, source_revision=self.resolve_head_sha(repository_root))

        command = ["git"]
        source_revision: str | None
        if source.kind is DiffSourceKind.WORKSPACE:
            command.extend(["diff", "--no-ext-diff", "--unified=3", "--binary"])
            source_revision = self.resolve_head_sha(repository_root)
        elif source.kind is DiffSourceKind.RANGE:
            base_revision = cast(str, source.base_revision)
            head_revision = cast(str, source.head_revision)
            base = self._verify_revision(repository_root, base_revision)
            head = self._verify_revision(repository_root, head_revision)
            command.extend(["diff", "--no-ext-diff", "--unified=3", "--binary", f"{base}...{head}"])
            source_revision = head
        else:
            commit_revision = cast(str, source.commit)
            commit = self._verify_revision(repository_root, commit_revision)
            command.extend(
                ["show", "--format=", "--no-ext-diff", "--unified=3", "--binary", commit]
            )
            source_revision = commit

        return RawDiff(
            text=self._run(repository_root, command),
            source_revision=source_revision,
        )

    def resolve_head_sha(self, repository_path: Path) -> str | None:
        """現在の HEAD SHA を返す。初回コミット前のリポジトリでは None を返す."""
        try:
            value = self._run(
                repository_path.resolve(),
                ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            )
        except GitCommandError:
            return None
        return value.strip() or None

    def _verify_repository_root(self, repository_path: Path) -> Path:
        """正規化済みのリポジトリルートを検証して返す."""
        candidate = repository_path.resolve()
        if not candidate.is_dir():
            message = Messages.repository_not_directory(candidate)
            raise InvalidInputError(message)
        root_text = self._run(candidate, ["git", "rev-parse", "--show-toplevel"]).strip()
        root = Path(root_text).resolve()
        if root != candidate:
            message = Messages.repository_not_git_root(root, candidate)
            raise InvalidInputError(message)
        return root

    def _verify_revision(self, repository_root: Path, revision: str) -> str:
        """`revision` をコミット SHA へ解決する."""
        if not revision.strip() or revision.startswith("-"):
            message = Messages.invalid_git_revision(revision)
            raise InvalidInputError(message)
        return self._run(
            repository_root,
            ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
        ).strip()

    def _validate_source(self, source: DiffSource) -> None:
        """入力元固有引数の欠落または混在を拒否する."""
        supplied = {
            "base_revision": source.base_revision,
            "head_revision": source.head_revision,
            "commit": source.commit,
            "patch_path": source.patch_path,
        }
        expected: dict[DiffSourceKind, frozenset[str]] = {
            DiffSourceKind.WORKSPACE: frozenset(),
            DiffSourceKind.RANGE: frozenset({"base_revision", "head_revision"}),
            DiffSourceKind.COMMIT: frozenset({"commit"}),
            DiffSourceKind.FILE: frozenset({"patch_path"}),
        }
        present = frozenset(name for name, value in supplied.items() if value is not None)
        if present != expected[source.kind]:
            message = Messages.diff_source_arguments_mismatch(
                source.kind.value, sorted(expected[source.kind]), sorted(present)
            )
            raise InvalidInputError(message)

    def _read_patch(self, patch_path: Path) -> str:
        """上限付きの UTF-8 パッチファイルを読み込む."""
        try:
            size = patch_path.stat().st_size
        except OSError as exc:
            message = Messages.cannot_inspect_patch_file(patch_path, exc)
            raise InvalidInputError(message) from exc
        if size > _MAX_PATCH_BYTES:
            message = Messages.patch_file_too_large(_MAX_PATCH_BYTES)
            raise InvalidInputError(message)
        try:
            return patch_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            message = Messages.cannot_read_patch_file(patch_path, exc)
            raise InvalidInputError(message) from exc

    def _run(self, repository_root: Path, command: list[str]) -> str:
        """上限付きの Git コマンドを 1 回実行し、標準出力を返す."""
        try:
            # 実行ファイルは固定し、動的な引数はすべて事前に検証する。
            completed = subprocess.run(  # nosec B603
                command,
                cwd=repository_root,
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            message = Messages.git_command_not_executed(command[1])
            raise GitCommandError(message) from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip()[:500]
            message = Messages.git_command_failed(command[1], detail)
            raise GitCommandError(message)
        return completed.stdout
