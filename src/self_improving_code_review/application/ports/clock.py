"""時刻と識別子生成のポート."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from self_improving_code_review.domain.common.values import (
    EvaluationId,
    ExtractionId,
    FeedbackId,
    MemoryId,
    RunId,
)

__all__ = ["Clock", "IdGenerator"]


class Clock(Protocol):
    """現在時刻の取得元."""

    def now_utc(self) -> datetime:
        """タイムゾーン情報を持つ現在の UTC 時刻を返す."""
        ...


class IdGenerator(Protocol):
    """実行と M2 の各種識別子の生成元."""

    def new_run_id(self) -> RunId:
        """新しく生成した実行識別子を返す."""
        ...

    def new_feedback_id(self) -> FeedbackId:
        """新しく生成したフィードバック識別子を返す."""
        ...

    def new_extraction_id(self) -> ExtractionId:
        """新しく生成した抽出実行識別子を返す."""
        ...

    def new_memory_id(self) -> MemoryId:
        """新しく生成したメモリ識別子を返す."""
        ...

    def new_evaluation_id(self) -> EvaluationId:
        """新しく生成した評価実行識別子を返す."""
        ...
