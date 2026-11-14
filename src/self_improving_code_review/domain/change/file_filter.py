"""レビュー対象とする変更ファイルを決定する選別処理."""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch

from self_improving_code_review.domain.change.models import ChangedFile
from self_improving_code_review.domain.common.enums import (
    ChangeType,
    FileDecision,
    FilterReason,
)
from self_improving_code_review.domain.common.values import NormalizedPath

__all__ = ["FileFilter", "FileFilterResult", "FilterPolicy"]

_LOCK_FILE_NAMES: frozenset[str] = frozenset(
    {
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "go.sum",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "yarn.lock",
    }
)

_VENDOR_DIRECTORIES: frozenset[str] = frozenset({".venv", "node_modules", "third_party", "vendor"})

_BUILD_OUTPUT_DIRECTORIES: frozenset[str] = frozenset(
    {".next", ".tox", "build", "dist", "htmlcov", "out", "target"}
)

_GENERATED_MARKERS: tuple[str, ...] = (
    "_pb2.py",
    "_pb2_grpc.py",
    ".g.dart",
    ".generated.ts",
    ".pb.go",
)

_DOCUMENT_SUFFIXES: frozenset[str] = frozenset({".adoc", ".md", ".rst", ".txt"})

_MINIFIED_MARKERS: tuple[str, ...] = (".min.css", ".min.js")


@dataclass(frozen=True, slots=True)
class FilterPolicy:
    """選別方針の設定可能部分.

    Attributes:
        exclude_patterns: 正規化済みパスと照合するグロブパターン.
        max_diff_bytes: ファイルを縮小済みまたは自動生成として扱う
            再構築済み差分サイズの上限.
    """

    exclude_patterns: tuple[str, ...] = field(default_factory=tuple)
    max_diff_bytes: int = 512_000


@dataclass(frozen=True, slots=True)
class FileFilterResult:
    """単一の変更ファイルに対する判定."""

    path: NormalizedPath
    decision: FileDecision
    reason: FilterReason | None

    @property
    def is_reviewable(self) -> bool:
        """ファイルをレビュー担当へ送信する場合は True を返す."""
        return self.decision is not FileDecision.FILTER


class FileFilter:
    """変更ファイルをレビュー、簡易レビュー、除外に分類する."""

    def __init__(self, policy: FilterPolicy | None = None) -> None:
        """選別方針を保持する.

        Args:
            policy: 設定可能な方針. 既定値は :class:`FilterPolicy`.
        """
        self._policy = policy or FilterPolicy()

    def decide(self, changed_file: ChangedFile) -> FileFilterResult:
        """``changed_file`` に対するレビュー判定を返す.

        選別処理を再実行せずに除外ファイルの理由を説明できるよう、
        理由コードを実行に記録する.
        """
        path = changed_file.new_path
        reason = self._filter_reason(changed_file)
        if reason is not None:
            return FileFilterResult(path=path, decision=FileDecision.FILTER, reason=reason)
        if path.suffix in _DOCUMENT_SUFFIXES:
            return FileFilterResult(
                path=path,
                decision=FileDecision.LIGHTWEIGHT,
                reason=FilterReason.DOCUMENT,
            )
        return FileFilterResult(path=path, decision=FileDecision.REVIEW, reason=None)

    def _filter_reason(self, changed_file: ChangedFile) -> FilterReason | None:
        """``changed_file`` の除外理由を返す。対象に残す場合は None を返す."""
        path = changed_file.new_path
        if changed_file.is_binary:
            return FilterReason.BINARY
        if changed_file.change_type is ChangeType.DELETED:
            return FilterReason.DELETED
        if any(fnmatch(path.value, pattern) for pattern in self._policy.exclude_patterns):
            return FilterReason.EXCLUDED
        if path.name.lower() in _LOCK_FILE_NAMES:
            return FilterReason.LOCK_FILE

        directories = frozenset(path.value.split("/")[:-1])
        if directories & _VENDOR_DIRECTORIES:
            return FilterReason.VENDOR
        if directories & _BUILD_OUTPUT_DIRECTORIES:
            return FilterReason.BUILD_OUTPUT
        if any(path.value.endswith(marker) for marker in _MINIFIED_MARKERS):
            return FilterReason.MINIFIED
        if any(path.value.endswith(marker) for marker in _GENERATED_MARKERS):
            return FilterReason.GENERATED
        if len(changed_file.to_diff_text().encode("utf-8")) > self._policy.max_diff_bytes:
            return FilterReason.MINIFIED
        if not changed_file.has_commentable_line:
            return FilterReason.NO_COMMENTABLE_LINE
        return None
