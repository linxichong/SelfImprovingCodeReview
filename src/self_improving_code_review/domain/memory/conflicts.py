"""メモリ候補の安全性方針と競合の最小判定.

意味的な矛盾を自動確定しない. 競合候補は提示のみとし、
既存の有効メモリを自動変更しない.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from self_improving_code_review.domain.common.enums import (
    MemoryCategory,
    MemoryPolarity,
    Severity,
)
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.memory.models import MemoryContent

__all__ = [
    "ConflictCandidate",
    "find_conflicts",
    "is_forbidden_suppression",
    "normalize_instruction_text",
]

_CRITICAL_SUPPRESSION_SEVERITIES: frozenset[Severity] = frozenset(
    {Severity.CRITICAL, Severity.HIGH}
)
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class ConflictCandidate:
    """有効メモリと矛盾する可能性がある候補の組.

    自動解決はせず、保守担当者が適用範囲を縮小して判断するための情報を持つ.
    """

    existing: MemoryContent
    reason_code: str


def normalize_instruction_text(text: str) -> str:
    """事実または指示の比較用に正規化した SHA-256 ダイジェストを返す.

    空白の差だけで別内容と判定しないよう、連続空白を単一空白へ畳み込み、
    大文字と小文字を区別せずに比較する.
    """
    normalized = _WHITESPACE.sub(" ", text).strip().casefold()
    return hash_text(normalized)


def is_forbidden_suppression(
    *,
    polarity: MemoryPolarity,
    category: MemoryCategory,
    severity: Severity | None,
) -> bool:
    """重大なセキュリティ指摘を抑制する候補の場合は True を返す.

    Args:
        polarity: 候補がレビューへ与える方向.
        category: 候補の分類.
        severity: 抑制対象として推定される重大度. 不明な場合は None.

    Returns:
        受け入れてはならない抑制候補の場合は True.
    """
    if polarity is not MemoryPolarity.SUPPRESS:
        return False
    if category is MemoryCategory.SECURITY:
        return True
    return severity in _CRITICAL_SUPPRESSION_SEVERITIES


def find_conflicts(
    candidate: MemoryContent,
    active_contents: tuple[MemoryContent, ...],
) -> tuple[ConflictCandidate, ...]:
    """``candidate`` と矛盾しうる有効メモリを返す.

    同一分類で適用範囲が交差し、事実または指示の正規化ハッシュが
    異なるものを競合候補とする. 方向が反対の場合も競合として扱う.

    Args:
        candidate: 統制対象の新しい候補.
        active_contents: 同じプロジェクトの有効メモリ内容.

    Returns:
        競合候補の組. 検出がない場合は空タプル.
    """
    candidate_fact = normalize_instruction_text(candidate.fact)
    candidate_instruction = normalize_instruction_text(candidate.review_instruction)
    conflicts: list[ConflictCandidate] = []

    for existing in active_contents:
        if existing.memory_id == candidate.memory_id:
            continue
        if existing.category is not candidate.category:
            continue
        if not existing.scope.intersects(candidate.scope):
            continue
        if existing.polarity is not candidate.polarity:
            conflicts.append(ConflictCandidate(existing=existing, reason_code="OPPOSITE_POLARITY"))
            continue
        if normalize_instruction_text(existing.fact) != candidate_fact:
            conflicts.append(ConflictCandidate(existing=existing, reason_code="DIFFERENT_FACT"))
            continue
        if normalize_instruction_text(existing.review_instruction) != candidate_instruction:
            conflicts.append(
                ConflictCandidate(existing=existing, reason_code="DIFFERENT_INSTRUCTION")
            )
    return tuple(conflicts)
