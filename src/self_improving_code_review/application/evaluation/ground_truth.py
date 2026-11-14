"""正解データ集合の発行.

発行時は承認済み問題だけを選び、安定順のハッシュで不変化する.
訂正は既存集合を書き換えず、新しいバージョンを発行する.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from self_improving_code_review.application.ports.authorization import ActorAuthorizer
from self_improving_code_review.application.ports.clock import Clock
from self_improving_code_review.domain.common.enums import (
    GroundTruthStatus,
    Permission,
    Severity,
)
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.evaluation.models import (
    GroundTruthCase,
    GroundTruthIssue,
    GroundTruthSet,
    compute_ground_truth_set_hash,
)
from self_improving_code_review.messages import Messages

__all__ = [
    "MIN_CRITICAL_CHANGE_REFS",
    "MIN_CRITICAL_ISSUES",
    "MIN_ISSUES",
    "MIN_PULL_REQUESTS",
    "GroundTruthService",
    "PopulationRequirement",
]

MIN_PULL_REQUESTS = 20
MIN_ISSUES = 30
MIN_CRITICAL_ISSUES = 5
MIN_CRITICAL_CHANGE_REFS = 3


@dataclass(frozen=True, slots=True)
class PopulationRequirement:
    """テスト用データセットの母集団要件と実測値.

    要件を満たさない集合でも発行はできるが、成功判定には使用できない.
    """

    change_ref_count: int
    issue_count: int
    critical_issue_count: int
    critical_change_ref_count: int

    @property
    def is_satisfied(self) -> bool:
        """成功判定に使用できる母集団を満たす場合は True を返す."""
        return (
            self.change_ref_count >= MIN_PULL_REQUESTS
            and self.issue_count >= MIN_ISSUES
            and self.critical_issue_count >= MIN_CRITICAL_ISSUES
            and self.critical_change_ref_count >= MIN_CRITICAL_CHANGE_REFS
        )

    @property
    def unmet_requirements(self) -> tuple[str, ...]:
        """満たしていない要件の名前を返す."""
        unmet: list[str] = []
        if self.change_ref_count < MIN_PULL_REQUESTS:
            unmet.append("change_ref_count")
        if self.issue_count < MIN_ISSUES:
            unmet.append("issue_count")
        if self.critical_issue_count < MIN_CRITICAL_ISSUES:
            unmet.append("critical_issue_count")
        if self.critical_change_ref_count < MIN_CRITICAL_CHANGE_REFS:
            unmet.append("critical_change_ref_count")
        return tuple(unmet)


class GroundTruthService:
    """正解データ集合を検証して発行する."""

    def __init__(self, *, authorizer: ActorAuthorizer, clock: Clock) -> None:
        """認可処理と時刻取得処理を保持する."""
        self._authorizer = authorizer
        self._clock = clock

    def publish(
        self,
        candidate: GroundTruthSet,
        *,
        actor_id: str,
    ) -> GroundTruthSet:
        """承認済み問題だけを選んで集合を発行する.

        Args:
            candidate: 発行対象の下書き集合.
            actor_id: 発行を実行する保守担当者.

        Returns:
            承認済み問題だけを含み、集合ハッシュを固定した発行済み集合.

        Raises:
            AuthorizationError: 実行者に発行権限がない場合.
            InvalidInputError: すでに発行済み、または承認済み問題が 0 件の場合.
        """
        self._authorizer.require(actor_id, Permission.PUBLISH_GROUND_TRUTH)
        if candidate.status is GroundTruthStatus.PUBLISHED:
            message = f"{Messages.GROUND_TRUTH_SET_ALREADY_PUBLISHED}: {candidate.set_id}"
            raise InvalidInputError(message)

        approved_issues = tuple(
            replace(issue, status=GroundTruthStatus.PUBLISHED)
            for issue in candidate.issues
            if issue.is_approved
        )
        if not approved_issues:
            message = Messages.GROUND_TRUTH_APPROVED_ISSUE_REQUIRED
            raise InvalidInputError(message)
        approved_cases = _published_cases(candidate, approved_issues)
        case_refs = {case.change_ref for case in approved_cases}
        if any(issue.change_ref not in case_refs for issue in approved_issues):
            message = Messages.GROUND_TRUTH_ISSUE_CASE_NOT_APPROVED
            raise InvalidInputError(message)

        return GroundTruthSet(
            set_id=candidate.set_id,
            set_hash=compute_ground_truth_set_hash(approved_issues, approved_cases),
            status=GroundTruthStatus.PUBLISHED,
            issues=approved_issues,
            approved_by=actor_id.strip(),
            published_at=self._clock.now_utc(),
            cases=approved_cases,
        )

    def measure_population(
        self,
        issues: tuple[GroundTruthIssue, ...],
        change_refs: frozenset[str] | None = None,
    ) -> PopulationRequirement:
        """母集団要件の実測値を返す.

        Args:
            issues: 評価対象として選んだ問題.
            change_refs: 問題なし変更も含む評価対象の変更参照.

        Returns:
            変更参照数、問題数、重大問題数、重大問題を含む変更参照数.
        """
        critical = tuple(issue for issue in issues if issue.severity is Severity.CRITICAL)
        return PopulationRequirement(
            change_ref_count=len(
                change_refs if change_refs is not None else {issue.change_ref for issue in issues}
            ),
            issue_count=len(issues),
            critical_issue_count=len(critical),
            critical_change_ref_count=len({issue.change_ref for issue in critical}),
        )


def _published_cases(
    candidate: GroundTruthSet,
    approved_issues: tuple[GroundTruthIssue, ...],
) -> tuple[GroundTruthCase, ...]:
    """候補のケースを発行状態へ変換し、旧形式では問題から補完する."""
    if candidate.cases:
        return tuple(
            replace(case, status=GroundTruthStatus.PUBLISHED)
            for case in candidate.cases
            if case.is_approved
        )
    first_issue_by_ref: dict[str, GroundTruthIssue] = {}
    for issue in approved_issues:
        first_issue_by_ref.setdefault(issue.change_ref, issue)
    return tuple(
        GroundTruthCase(
            sequence=sequence,
            change_ref=change_ref,
            status=GroundTruthStatus.PUBLISHED,
            created_by=issue.created_by,
            verified_by=issue.verified_by,
            approved_by=issue.approved_by,
        )
        for sequence, (change_ref, issue) in enumerate(
            sorted(first_issue_by_ref.items()),
            start=1,
        )
    )
