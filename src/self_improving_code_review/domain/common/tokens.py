"""決定的なトークン数見積もり.

レビュー処理は LLM 呼び出し前にレビュー単位の分割を決める必要があるため、
見積もりはプロバイダーのトークナイザーではなく送信内容だけの純粋関数とする.
"""

from __future__ import annotations

import math

__all__ = ["CHARS_PER_TOKEN", "estimate_tokens"]

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """``text`` の決定的なトークン数上限見積もりを返す.

    Args:
        text: 測定対象の送信内容.

    Returns:
        トークン数の見積もり. 空文字列は 0、空でない文字列は
        切り上げにより少なくとも 1 トークンとする.
    """
    if not text:
        return 0
    return math.ceil(len(text) / CHARS_PER_TOKEN)
