"""フィードバックのドメインモデル.

フィードバックは追記のみで管理する. 訂正は既存レコードを更新せず、
``supersedes_feedback_id`` を持つ新しいレコードで行う.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from self_improving_code_review.domain.common.enums import FeedbackLabel, Severity
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import (
    FeedbackId,
    FindingId,
    NormalizedPath,
    RunId,
)
from self_improving_code_review.messages import Messages

__all__ = [
    "FeedbackRecord",
    "IssueLocation",
]


@dataclass(frozen=True, slots=True)
class IssueLocation:
    """見逃し問題の位置.

    行番号は改訂で移動するため、行とシンボルの少なくとも一方を必須とし、
    両方が失われた位置を受け付けない.
    """

    file: NormalizedPath
    start_line: int | None = None
    symbol: str | None = None

    def __post_init__(self) -> None:
        """行もシンボルも持たない位置と不正な行番号を拒否する.

        Raises:
            InvalidInputError: 位置を特定できない、または行番号が 1 未満の場合.
        """
        normalized_symbol = self.symbol.strip() if self.symbol is not None else None
        if self.start_line is None and not normalized_symbol:
            message = Messages.MISSED_ISSUE_LOCATION_REQUIRED
            raise InvalidInputError(message)
        if self.start_line is not None and self.start_line < 1:
            message = Messages.MISSED_ISSUE_START_LINE_INVALID
            raise InvalidInputError(message)
        object.__setattr__(self, "symbol", normalized_symbol or None)

    @property
    def locator(self) -> str:
        """冪等性キーに使用する安定した位置表現を返す."""
        if self.start_line is not None and self.symbol is not None:
            return f"line:{self.start_line}:symbol:{self.symbol}"
        if self.start_line is not None:
            return f"line:{self.start_line}"
        return f"symbol:{self.symbol}"


@dataclass(frozen=True, slots=True)
class FeedbackRecord:
    """保存済みのフィードバックレコード.

    既存指摘への評価では ``finding_id`` を、見逃し報告では ``location`` と
    ``issue_type`` を保持する. 両方を同時に満たすレコードは作らない.
    """

    feedback_id: FeedbackId
    run_id: RunId
    label: FeedbackLabel
    reason: str
    actor_id: str
    created_at: datetime
    idempotency_key: str
    finding_id: FindingId | None = None
    location: IssueLocation | None = None
    issue_type: str | None = None
    severity: Severity | None = None
    description: str | None = None
    evidence: str | None = None
    supersedes_feedback_id: FeedbackId | None = None

    def __post_init__(self) -> None:
        """ラベルと対象の組み合わせが契約に一致することを確認する.

        Raises:
            InvalidInputError: 見逃しラベルと既存指摘評価の対象が入れ替わっている、
                実行者が空、または作成時刻が UTC ではない場合.
        """
        if not self.actor_id.strip():
            message = Messages.FEEDBACK_ACTOR_REQUIRED
            raise InvalidInputError(message)
        if self.created_at.tzinfo is None:
            message = Messages.FEEDBACK_TIME_NOT_UTC
            raise InvalidInputError(message)
        if self.label.is_missed_issue:
            self._require_missed_issue_fields()
        else:
            self._require_finding_feedback_fields()

    @property
    def is_missed_issue(self) -> bool:
        """見逃し報告の場合は True を返す."""
        return self.label.is_missed_issue

    def _require_missed_issue_fields(self) -> None:
        """見逃し報告に必要な項目の欠落と既存指摘の混在を拒否する.

        Raises:
            InvalidInputError: 位置、問題種別、重大度、根拠のいずれかがない、
                または既存指摘を指している場合.
        """
        if self.finding_id is not None:
            message = Messages.MISSED_ISSUE_FINDING_FORBIDDEN
            raise InvalidInputError(message)
        if self.location is None:
            message = Messages.MISSED_ISSUE_FEEDBACK_LOCATION_REQUIRED
            raise InvalidInputError(message)
        if not (self.issue_type or "").strip():
            message = Messages.MISSED_ISSUE_TYPE_REQUIRED
            raise InvalidInputError(message)
        if self.severity is None:
            message = Messages.MISSED_ISSUE_SEVERITY_REQUIRED
            raise InvalidInputError(message)
        if not (self.evidence or "").strip():
            message = Messages.MISSED_ISSUE_EVIDENCE_REQUIRED
            raise InvalidInputError(message)

    def _require_finding_feedback_fields(self) -> None:
        """既存指摘評価に必要な項目の欠落と見逃し項目の混在を拒否する.

        Raises:
            InvalidInputError: 指摘を指していない、または見逃し専用項目を含む場合.
        """
        if self.finding_id is None:
            message = Messages.FINDING_FEEDBACK_ID_REQUIRED
            raise InvalidInputError(message)
        if self.location is not None:
            message = Messages.FINDING_FEEDBACK_LOCATION_FORBIDDEN
            raise InvalidInputError(message)
        if not self.reason.strip():
            message = Messages.FINDING_FEEDBACK_REASON_REQUIRED
            raise InvalidInputError(message)
