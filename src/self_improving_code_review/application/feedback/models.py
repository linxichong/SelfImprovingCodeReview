"""フィードバック受付の入力契約と結果.

CLI へは ``feedback_status`` と ``candidate_status`` を分けて返す.
候補抽出が失敗しても、保存済みフィードバックを未保存として表示しない.
"""

from __future__ import annotations

from dataclasses import dataclass

from self_improving_code_review.domain.common.enums import (
    ExtractionStatus,
    FeedbackLabel,
    Severity,
)
from self_improving_code_review.domain.common.values import (
    ExtractionId,
    FeedbackId,
    FindingId,
    MemoryId,
    MemoryVersion,
    NormalizedPath,
    RunId,
)

__all__ = [
    "FeedbackOutcome",
    "FindingFeedbackInput",
    "MissedIssueInput",
]


@dataclass(frozen=True, slots=True)
class FindingFeedbackInput:
    """既存指摘への評価入力."""

    run_id: RunId
    finding_id: FindingId
    label: FeedbackLabel
    reason: str
    actor_id: str


@dataclass(frozen=True, slots=True)
class MissedIssueInput:
    """見逃し問題の報告入力.

    内部ラベルは ``MISSED_ISSUE`` に固定するため、入力にラベルを含めない.
    """

    run_id: RunId
    file: NormalizedPath
    issue_type: str
    severity: Severity
    description: str
    evidence: str
    actor_id: str
    start_line: int | None = None
    symbol: str | None = None


@dataclass(frozen=True, slots=True)
class FeedbackOutcome:
    """フィードバック保存と候補抽出の結果.

    ``feedback_status`` は常に ``RECORDED`` とし、候補抽出の成否は
    ``candidate_status`` で区別する.
    """

    feedback_id: FeedbackId
    feedback_status: str
    extraction_id: ExtractionId | None = None
    candidate_status: ExtractionStatus | None = None
    candidate_memory_id: MemoryId | None = None
    candidate_version: MemoryVersion | None = None
    safe_error_code: str | None = None
    is_duplicate: bool = False
