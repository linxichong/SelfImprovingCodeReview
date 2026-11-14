"""安定したフィンガープリントの生成.

フィンガープリントは決定的に計算し、リビジョン間で変化する
行番号と自然言語のタイトルを意図的に除外する.
"""

from __future__ import annotations

import re

from self_improving_code_review.domain.change.models import ChangedFile
from self_improving_code_review.domain.common.enums import LineKind
from self_improving_code_review.domain.common.values import LineRange, NormalizedPath

__all__ = ["UNKNOWN_SYMBOL", "build_fingerprint", "find_enclosing_symbol"]

UNKNOWN_SYMBOL = "unknown"

_SYMBOL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)"),
    re.compile(r"^\s*class\s+(?P<name>[A-Za-z_]\w*)"),
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)"),
    re.compile(
        r"^\s*(?:public|private|protected|internal)\s+"
        r"(?:static\s+)?[\w<>\[\],\s]+\s+(?P<name>[A-Za-z_]\w*)\s*\("
    ),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_]\w*)"),
    re.compile(r"^\s*fn\s+(?P<name>[A-Za-z_]\w*)"),
)


def build_fingerprint(
    *,
    rule_id: str,
    path: NormalizedPath,
    enclosing_symbol: str,
    primary_entity: str,
) -> str:
    """指摘の安定したフィンガープリントを返す.

    Args:
        rule_id: 標準の問題識別子.
        path: 指摘のリポジトリ相対パス.
        enclosing_symbol: 最も近い外側の関数またはクラス. 不明な場合は ``unknown``.
        primary_entity: 問題の主対象となるシンボル.

    Returns:
        実行をまたいだ指摘の重複排除に使用するコロン区切りフィンガープリント.
    """
    return ":".join(
        (
            rule_id,
            path.value,
            enclosing_symbol or UNKNOWN_SYMBOL,
            primary_entity or UNKNOWN_SYMBOL,
        )
    )


def find_enclosing_symbol(changed_file: ChangedFile, line_range: LineRange) -> str:
    """``line_range`` 以前で最も近い宣言を返す.

    削除された宣言は結果ファイル内の指摘を包含できないため、
    新しいリビジョンに存在する行だけを対象とする.
    """
    best_line = -1
    best_name = UNKNOWN_SYMBOL
    for hunk in changed_file.hunks:
        for line in hunk.lines:
            if line.kind is LineKind.DELETED or line.new_line is None:
                continue
            if line.new_line > line_range.start_line or line.new_line <= best_line:
                continue
            name = _match_declaration(line.text)
            if name is not None:
                best_line = line.new_line
                best_name = name
    return best_name


def _match_declaration(text: str) -> str | None:
    """``text`` 内で宣言されたシンボル名を返す。存在しない場合は None を返す."""
    for pattern in _SYMBOL_PATTERNS:
        match = pattern.match(text)
        if match is not None:
            return match.group("name")
    return None
