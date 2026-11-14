"""固定版または作業ツリーのコード読取ポート."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from self_improving_code_review.domain.common.enums import DiffSourceKind
from self_improving_code_review.domain.common.values import NormalizedPath

__all__ = ["ContextSource", "ContextSourceFactory", "SourceFingerprint"]


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    """再開時に照合する追加ソースの安全な識別情報."""

    bundle_sequence: int
    path: NormalizedPath
    source_revision: str | None
    content_hash: str


class ContextSource(Protocol):
    """コンテキストツールから参照できるコード入力元."""

    @property
    def source_revision(self) -> str | None:
        """固定コミット SHA を返す。作業ツリーの場合は None."""
        ...

    @property
    def fingerprints(self) -> tuple[SourceFingerprint, ...]:
        """この入力元で読み取ったパスと内容ハッシュを返す."""
        ...

    def read_text(self, path: NormalizedPath) -> str | None:
        """UTF-8 テキストを読み、存在しない場合は None を返す."""
        ...

    def list_paths(
        self,
        path_scope: tuple[NormalizedPath, ...],
        *,
        max_files: int,
    ) -> tuple[NormalizedPath, ...]:
        """範囲内の通常ファイルを安定順で返す."""
        ...


class ContextSourceFactory(Protocol):
    """差分入力元に対応する固定コード入力元を作成する."""

    def __call__(
        self,
        repository: Path,
        source_revision: str | None,
        source_kind: DiffSourceKind,
    ) -> ContextSource:
        """入力元と解決済みリビジョンからコード入力元を返す."""
        ...
