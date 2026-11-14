"""プロンプト信頼境界の符号化.

差分、メタデータ、その他のリポジトリ内容はデータであり、命令ではない.
注入された内容がデータブロックを終了して命令として解釈されないよう、
未信頼テキスト内の境界マーカーを無害化する.
"""

from __future__ import annotations

__all__ = [
    "UNTRUSTED_BEGIN",
    "UNTRUSTED_END",
    "encode_untrusted_block",
    "escape_boundary_tokens",
]

UNTRUSTED_BEGIN = "<<<UNTRUSTED_DATA_BEGIN:{label}>>>"
UNTRUSTED_END = "<<<UNTRUSTED_DATA_END:{label}>>>"

_ESCAPED_MARKERS: tuple[tuple[str, str], ...] = (
    ("<<<", "\u2039\u2039\u2039"),
    (">>>", "\u203a\u203a\u203a"),
)


def escape_boundary_tokens(text: str) -> str:
    """``text`` 内の境界マーカーを見た目が似た文字へ置換する."""
    escaped = text
    for marker, replacement in _ESCAPED_MARKERS:
        escaped = escaped.replace(marker, replacement)
    return escaped


def encode_untrusted_block(label: str, text: str) -> str:
    """``text`` をラベル付きの未信頼データブロックで囲む.

    Args:
        label: ``bundle-diff`` など、送信内容を表す短い識別子.
        text: 埋め込む未信頼の内容.

    Returns:
        ``text`` 内の境界マーカーを無害化した区切り付きブロック.
    """
    safe_label = escape_boundary_tokens(label)
    return "\n".join(
        (
            UNTRUSTED_BEGIN.format(label=safe_label),
            escape_boundary_tokens(text),
            UNTRUSTED_END.format(label=safe_label),
        )
    )
