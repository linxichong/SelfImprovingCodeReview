"""メモリ候補抽出の入力契約と結果."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from self_improving_code_review.domain.common.enums import ExtractionStatus
from self_improving_code_review.domain.common.values import (
    ExtractionId,
    FeedbackId,
    MemoryId,
    MemoryVersion,
    ProjectId,
)

__all__ = ["ExtractionOutcome", "ExtractionRequest"]


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    """メモリ候補抽出の入力.

    コンテキストは呼び出し元が必要最小限だけ取得して渡す. 抽出処理は
    リポジトリから追加のコードを読まない.
    """

    project_id: ProjectId
    feedback_ids: tuple[FeedbackId, ...]
    source_revision: str | None = None
    context_text: str = ""
    context_locator: str = ""
    is_test_evaluation: bool = False


@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    """抽出実行の結果.

    ``BLOCKED`` および ``FAILED`` では候補を作らないが、保存済み
    フィードバックは維持する.
    """

    extraction_id: ExtractionId
    status: ExtractionStatus
    attempt: int
    candidate_memory_id: MemoryId | None = None
    candidate_version: MemoryVersion | None = None
    safe_error_code: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost_usd: Decimal = Decimal("0")
    is_replayed: bool = False
