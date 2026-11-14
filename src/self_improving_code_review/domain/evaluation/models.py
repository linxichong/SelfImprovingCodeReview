"""正解データと評価のドメインモデル.

集合は発行時に不変化する. 訂正は既存集合を書き換えず、
新しいバージョンを発行する.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from self_improving_code_review.domain.common.enums import (
    GroundTruthStatus,
    MatchResult,
    Severity,
)
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_lines
from self_improving_code_review.domain.common.values import (
    FindingId,
    GroundTruthIssueId,
    GroundTruthSetId,
    NormalizedPath,
)
from self_improving_code_review.messages import Messages

__all__ = [
    "GroundTruthCase",
    "GroundTruthIssue",
    "GroundTruthSet",
    "MatchOutcome",
    "compute_ground_truth_set_hash",
]


@dataclass(frozen=True, slots=True)
class GroundTruthCase:
    """評価対象となる変更 1 件を表す.

    問題を含まない変更も明示的に登録し、誤検出の評価対象に含める.
    """

    sequence: int
    change_ref: str
    status: GroundTruthStatus
    created_by: str = "unknown"
    verified_by: str | None = None
    approved_by: str | None = None

    def __post_init__(self) -> None:
        """連番と変更参照の不正値を拒否する."""
        if self.sequence < 1:
            raise InvalidInputError(Messages.GROUND_TRUTH_CASE_SEQUENCE_INVALID)
        if not self.change_ref.strip():
            raise InvalidInputError(Messages.GROUND_TRUTH_CASE_CHANGE_REF_REQUIRED)

    @property
    def is_approved(self) -> bool:
        """集合へ発行できる状態の場合は True を返す."""
        return self.status in {GroundTruthStatus.APPROVED, GroundTruthStatus.PUBLISHED}


@dataclass(frozen=True, slots=True)
class GroundTruthIssue:
    """人間が検証した 1 件の既知問題.

    位置は行とシンボルの少なくとも一方で特定する. どちらも失われた
    問題は照合できないため受け付けない.
    """

    issue_id: GroundTruthIssueId
    change_ref: str
    file: NormalizedPath
    issue_type: str
    severity: Severity
    evidence: str
    status: GroundTruthStatus
    start_line: int | None = None
    symbol: str | None = None
    rule_id: str | None = None
    origin: str = "manual"
    created_by: str = "unknown"
    verified_by: str | None = None
    approved_by: str | None = None

    def __post_init__(self) -> None:
        """位置、変更参照、根拠の欠落を拒否する.

        Raises:
            InvalidInputError: 位置を特定できない、変更参照が空、
                または根拠が空の場合.
        """
        if self.start_line is None and not (self.symbol or "").strip():
            message = Messages.GROUND_TRUTH_ISSUE_LOCATION_REQUIRED
            raise InvalidInputError(message)
        if self.start_line is not None and self.start_line < 1:
            message = Messages.GROUND_TRUTH_ISSUE_START_LINE_INVALID
            raise InvalidInputError(message)
        if not self.change_ref.strip():
            message = Messages.GROUND_TRUTH_ISSUE_CHANGE_REF_REQUIRED
            raise InvalidInputError(message)
        if not self.evidence.strip():
            message = Messages.GROUND_TRUTH_ISSUE_EVIDENCE_REQUIRED
            raise InvalidInputError(message)

    @property
    def is_approved(self) -> bool:
        """集合へ発行できる状態の場合は True を返す."""
        return self.status in {GroundTruthStatus.APPROVED, GroundTruthStatus.PUBLISHED}

    @property
    def locator(self) -> str:
        """安定順ハッシュに使用する位置表現を返す."""
        if self.start_line is not None:
            return f"line:{self.start_line}"
        return f"symbol:{self.symbol}"


@dataclass(frozen=True, slots=True)
class GroundTruthSet:
    """発行済みの正解データ集合."""

    set_id: GroundTruthSetId
    set_hash: str
    status: GroundTruthStatus
    issues: tuple[GroundTruthIssue, ...]
    approved_by: str | None = None
    published_at: datetime | None = None
    cases: tuple[GroundTruthCase, ...] = ()

    def __post_init__(self) -> None:
        """発行済み集合に承認者と発行時刻がないことを拒否する.

        Raises:
            InvalidInputError: 発行済みなのに承認者または発行時刻がない、
                承認済み以外の問題を含む、または発行時刻が UTC ではない場合.
        """
        if self.status is not GroundTruthStatus.PUBLISHED:
            return
        if not (self.approved_by or "").strip():
            message = Messages.PUBLISHED_SET_APPROVER_REQUIRED
            raise InvalidInputError(message)
        if self.published_at is None or self.published_at.tzinfo is None:
            message = Messages.PUBLISHED_SET_TIME_REQUIRED
            raise InvalidInputError(message)
        if any(not issue.is_approved for issue in self.issues):
            message = Messages.PUBLISHED_SET_UNAPPROVED_ISSUE
            raise InvalidInputError(message)
        if any(not case.is_approved for case in self.cases):
            message = Messages.PUBLISHED_SET_UNAPPROVED_CASE
            raise InvalidInputError(message)
        case_refs = {case.change_ref for case in self.cases}
        if self.cases and any(issue.change_ref not in case_refs for issue in self.issues):
            message = Messages.GROUND_TRUTH_ISSUE_CASE_UNREGISTERED
            raise InvalidInputError(message)

    @property
    def critical_issues(self) -> tuple[GroundTruthIssue, ...]:
        """重大問題だけを返す."""
        return tuple(issue for issue in self.issues if issue.severity is Severity.CRITICAL)

    @property
    def change_refs(self) -> frozenset[str]:
        """集合が対象とする変更参照の集合を返す."""
        if self.cases:
            return frozenset(case.change_ref for case in self.cases)
        return frozenset(issue.change_ref for issue in self.issues)


@dataclass(frozen=True, slots=True)
class MatchOutcome:
    """指摘と正解データの照合結果 1 件.

    ``REVIEW_REQUIRED`` は自動判定できなかったことを示し、
    判定担当者へ比較群を伏せて提示する.
    """

    result: MatchResult
    finding_id: FindingId | None = None
    issue_id: GroundTruthIssueId | None = None
    adjudicator: str | None = None

    def __post_init__(self) -> None:
        """結果と対象の組み合わせが照合契約に一致することを確認する.

        Raises:
            InvalidInputError: 検出結果に指摘がない、未検出に問題がない、
                または両方が欠落している場合.
        """
        if self.result in {MatchResult.TRUE_POSITIVE, MatchResult.DUPLICATE} and (
            self.finding_id is None or self.issue_id is None
        ):
            message = f"{self.result.value} match requires both finding_id and issue_id"
            raise InvalidInputError(message)
        if self.result is MatchResult.FALSE_POSITIVE and self.finding_id is None:
            message = Messages.FALSE_POSITIVE_FINDING_REQUIRED
            raise InvalidInputError(message)
        if self.result is MatchResult.FALSE_NEGATIVE and self.issue_id is None:
            message = Messages.FALSE_NEGATIVE_ISSUE_REQUIRED
            raise InvalidInputError(message)
        if self.finding_id is None and self.issue_id is None:
            message = Messages.MATCH_TARGET_REQUIRED
            raise InvalidInputError(message)


def compute_ground_truth_set_hash(
    issues: tuple[GroundTruthIssue, ...],
    cases: tuple[GroundTruthCase, ...] = (),
) -> str:
    """問題と問題なし変更を含む集合ハッシュを返す.

    変更参照、ファイル、位置、問題種別の安定順で連結するため、
    登録順が変わっても同じ集合からは同じダイジェストを生成する.
    """
    ordered = sorted(
        issues,
        key=lambda issue: (
            issue.change_ref,
            issue.file.value,
            issue.locator,
            issue.issue_type,
        ),
    )
    issue_refs = {issue.change_ref for issue in issues}
    issue_lines = [
        f"{issue.change_ref}:{issue.file.value}:{issue.locator}:{issue.issue_type}"
        for issue in ordered
    ]
    clean_case_lines = [
        f"clean-case:{case.sequence}:{case.change_ref}"
        for case in sorted(cases, key=lambda item: (item.change_ref, item.sequence))
        if case.change_ref not in issue_refs
    ]
    return hash_lines((*issue_lines, *clean_case_lines))
