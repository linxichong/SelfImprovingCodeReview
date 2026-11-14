"""メモリ候補の方針検証.

スキーマ検証を通過した送信内容を、さらにメモリ方針へ照らして判定する.
``suppress`` は削除命令ではなく強い根拠を要求する指示へ正規化し、
重大なセキュリティ指摘を抑制する候補は拒否する.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from self_improving_code_review.domain.common.enums import (
    MemoryCategory,
    MemoryPolarity,
    Severity,
)
from self_improving_code_review.domain.common.errors import PolicyViolationError
from self_improving_code_review.domain.memory.conflicts import is_forbidden_suppression
from self_improving_code_review.domain.memory.scope import MemoryScope
from self_improving_code_review.messages import Messages

__all__ = [
    "SUPPRESSION_PREFIX",
    "CandidateDraft",
    "build_candidate_draft",
]

SUPPRESSION_PREFIX = "強い根拠を確認できない限り報告しない: "
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(the\s+)?(previous|above|system)", re.IGNORECASE),
    re.compile(r"disregard\s+(the\s+)?(previous|above|system)", re.IGNORECASE),
    re.compile(r"system\s*prompt", re.IGNORECASE),
    re.compile(r"(以前|上記|システム)の(指示|命令)を(無視|破棄)"),
    re.compile(r"UNTRUSTED_DATA_(BEGIN|END)"),
    re.compile(r"<<<|>>>"),
)
_DELETION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"never\s+report", re.IGNORECASE),
    re.compile(r"always\s+ignore", re.IGNORECASE),
    re.compile(r"(絶対に|一切)報告(しない|するな)"),
    re.compile(r"無条件に(抑制|無視)"),
)


@dataclass(frozen=True, slots=True)
class CandidateDraft:
    """方針検証を通過したメモリ候補の内容.

    ドメインの :class:`MemoryContent` は識別子と時刻を必要とするため、
    ユースケースがそれらを付与する前段としてこの型を用いる.
    """

    category: MemoryCategory
    scope: MemoryScope
    fact: str
    review_instruction: str
    polarity: MemoryPolarity
    confidence: float
    evidence_summary: str


def build_candidate_draft(
    payload: dict[str, object],
    *,
    reported_severity: Severity | None = None,
) -> CandidateDraft:
    """スキーマ検証済みの送信内容を方針検証して候補へ変換する.

    Args:
        payload: 閉じたスキーマ検証を通過した候補送信内容.
        reported_severity: 抑制対象として推定される重大度. 不明な場合は None.

    Returns:
        方針検証を通過し、抑制指示を正規化した候補.

    Raises:
        PolicyViolationError: 命令注入パターンを含む、削除命令とみなせる、
            または重大なセキュリティ指摘を抑制する場合.
    """
    category = MemoryCategory(str(payload["category"]))
    polarity = MemoryPolarity(str(payload["polarity"]))
    fact = str(payload["fact"]).strip()
    instruction = str(payload["review_instruction"]).strip()
    evidence_summary = str(payload["evidence_summary"]).strip()

    for name, text in (
        ("fact", fact),
        ("review_instruction", instruction),
        ("evidence_summary", evidence_summary),
    ):
        _reject_injection(name, text)

    if is_forbidden_suppression(
        polarity=polarity,
        category=category,
        severity=reported_severity,
    ):
        message = Messages.MEMORY_CANDIDATE_SUPPRESSES_CRITICAL_SECURITY
        raise PolicyViolationError(message)

    if polarity is MemoryPolarity.SUPPRESS:
        _reject_deletion_instruction(instruction)
        instruction = _normalize_suppression(instruction)

    return CandidateDraft(
        category=category,
        scope=_build_scope(payload["scope"]),
        fact=fact,
        review_instruction=instruction,
        polarity=polarity,
        confidence=float(payload["confidence"]),  # type: ignore[arg-type]
        evidence_summary=evidence_summary,
    )


def _build_scope(raw_scope: object) -> MemoryScope:
    """送信内容の適用範囲をドメインの適用範囲へ変換する."""
    scope = raw_scope if isinstance(raw_scope, dict) else {}
    return MemoryScope(
        languages=_group(scope, "languages"),
        frameworks=_group(scope, "frameworks"),
        paths=_group(scope, "paths"),
        symbols=_group(scope, "symbols"),
        keywords=_group(scope, "keywords"),
        risk_tags=_group(scope, "risk_tags"),
    )


def _group(scope: dict[str, object], name: str) -> tuple[str, ...]:
    """適用範囲グループを空白除去済みの文字列タプルとして返す."""
    values = scope.get(name, [])
    if not isinstance(values, list):
        return ()
    return tuple(str(value).strip() for value in values if str(value).strip())


def _reject_injection(field_name: str, text: str) -> None:
    """命令注入パターンを含むテキストを拒否する.

    Raises:
        PolicyViolationError: 注入パターンに一致した場合.
    """
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text) is not None:
            message = f"{Messages.MEMORY_CANDIDATE_INJECTION_PATTERN}: {field_name}"
            raise PolicyViolationError(message)


def _reject_deletion_instruction(instruction: str) -> None:
    """無条件の報告禁止を求める指示を拒否する.

    Raises:
        PolicyViolationError: 削除命令とみなせる表現を含む場合.
    """
    for pattern in _DELETION_PATTERNS:
        if pattern.search(instruction) is not None:
            message = Messages.MEMORY_SUPPRESSION_FORBIDS_REPORTING
            raise PolicyViolationError(message)


def _normalize_suppression(instruction: str) -> str:
    """抑制指示へ根拠要求の前置きを付けて正規化する."""
    if instruction.startswith(SUPPRESSION_PREFIX):
        return instruction
    return f"{SUPPRESSION_PREFIX}{instruction}"
