"""対象リビジョンから検証用コードを読み取るポート."""

from __future__ import annotations

from typing import Protocol

from self_improving_code_review.domain.common.values import NormalizedPath

__all__ = ["RevisionContentReader"]


class RevisionContentReader(Protocol):
    """固定されたソース改訂からファイル内容を安全に読み取る."""

    def read_text(self, revision: str, path: NormalizedPath) -> str | None:
        """ファイル内容を返す。対象ファイルが存在しない場合は None を返す."""
        ...
