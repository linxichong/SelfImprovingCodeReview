"""実行の再現性に使用する決定的なハッシュ補助処理."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

__all__ = ["hash_lines", "hash_text"]


def hash_text(text: str) -> str:
    """``text`` を UTF-8 で符号化した SHA-256 16進ダイジェストを返す."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_lines(lines: Iterable[str]) -> str:
    """``lines`` を改行で結合した値の SHA-256 十六進ダイジェストを返す.

    要素境界を越えた内容の並べ替えでダイジェストが変化しないよう、
    各要素の末尾に改行を追加する.
    """
    digest = hashlib.sha256()
    for line in lines:
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()
