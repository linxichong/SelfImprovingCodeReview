"""差分取得ポート."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from self_improving_code_review.domain.common.enums import DiffSourceKind

__all__ = ["DiffProvider", "DiffSource", "RawDiff"]


@dataclass(frozen=True, slots=True)
class DiffSource:
    """呼び出し元が選択した単一の差分入力元.

    Attributes:
        kind: 使用する入力元.
        base_revision: ``RANGE`` の基準リビジョン.
        head_revision: ``RANGE`` の先端リビジョン.
        commit: ``COMMIT`` のコミット.
        patch_path: ``FILE`` のパッチファイル.
    """

    kind: DiffSourceKind
    base_revision: str | None = None
    head_revision: str | None = None
    commit: str | None = None
    patch_path: Path | None = None


@dataclass(frozen=True, slots=True)
class RawDiff:
    """再現元リビジョンを伴う統一差分テキスト."""

    text: str
    source_revision: str | None


class DiffProvider(Protocol):
    """レビュー要求の統一差分を取得する."""

    def acquire(self, repository_path: Path, source: DiffSource) -> RawDiff:
        """``source`` が指定する統一差分を返す."""
        ...

    def resolve_head_sha(self, repository_path: Path) -> str | None:
        """現在の ``HEAD`` のコミット SHA を返す。リポジトリ外では None を返す."""
        ...
