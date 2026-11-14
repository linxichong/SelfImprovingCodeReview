"""比較群評価の実行と成功判定.

全比較群は同一モデル、プロンプト、ルールスナップショット、正解データ、
ケース順を使用し、比較群 C だけメモリスナップショットを追加する.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from self_improving_code_review.application.evaluation.matcher import match_findings
from self_improving_code_review.domain.common.enums import (
    EvaluationArm,
    MetricStatus,
    Severity,
)
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.evaluation.metrics import (
    MatchCounts,
    MetricValue,
    count_matches,
    critical_recall,
    precision,
    recall,
)
from self_improving_code_review.domain.evaluation.models import GroundTruthIssue, MatchOutcome
from self_improving_code_review.domain.finding.models import Finding
from self_improving_code_review.messages import Messages

__all__ = [
    "MIN_REPEATS",
    "ArmReport",
    "ComparisonVerdict",
    "EvaluationRunner",
]

MIN_REPEATS = 3


@dataclass(frozen=True, slots=True)
class ArmReport:
    """1 つの比較群の測定値.

    ``critical_issue_ids`` は検出できた重大問題の識別子集合であり、
    比較群間の包含関係を検証するために保持する.
    """

    arm: EvaluationArm
    repeat: int
    counts: MatchCounts
    precision: MetricValue
    recall: MetricValue
    critical_recall: MetricValue
    critical_issue_ids: frozenset[str]
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost_usd: Decimal = Decimal("0")
    matches: tuple[MatchOutcome, ...] = ()

    @property
    def outcomes(self) -> tuple[MetricValue, ...]:
        """保存対象の測定値をまとめて返す."""
        return (self.precision, self.recall, self.critical_recall)


@dataclass(frozen=True, slots=True)
class ComparisonVerdict:
    """比較群 B と C の反証可能な判定結果."""

    is_successful: bool
    reason_codes: tuple[str, ...]
    lost_critical_issue_ids: tuple[str, ...]


class EvaluationRunner:
    """指摘一覧から比較群の測定値と判定を算出する."""

    def evaluate_arm(
        self,
        arm: EvaluationArm,
        repeat: int,
        *,
        main_findings: tuple[Finding, ...],
        issues: tuple[GroundTruthIssue, ...],
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
        cost_usd: Decimal = Decimal("0"),
    ) -> ArmReport:
        """1 回分の比較群実行を測定する.

        Args:
            arm: 評価した比較群.
            repeat: 何回目の反復か. 1 から始める.
            main_findings: 主要指摘のみ. 参照指摘は検出扱いにしない.
            issues: 照合対象の正解データ問題.
            input_tokens: 実行で消費した入力トークン.
            output_tokens: 実行で消費した出力トークン.
            latency_ms: 実行の総待ち時間.
            cost_usd: 取得できた場合のコスト.

        Returns:
            件数、測定値、検出できた重大問題の識別子集合.

        Raises:
            InvalidInputError: 反復回数が 1 未満の場合.
        """
        if repeat < 1:
            raise InvalidInputError(Messages.EVALUATION_REPEAT_INVALID)

        report = match_findings(main_findings, issues)
        counts = count_matches(report.outcomes)
        critical_counts = _critical_counts(report.outcomes, issues)
        return ArmReport(
            arm=arm,
            repeat=repeat,
            counts=counts,
            precision=precision(counts),
            recall=recall(counts),
            critical_recall=critical_recall(critical_counts),
            critical_issue_ids=frozenset(
                str(issue.issue_id)
                for issue in issues
                if issue.severity is Severity.CRITICAL
                and str(issue.issue_id) in report.matched_issue_ids
            ),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            matches=report.outcomes,
        )

    def evaluate_cases(
        self,
        arm: EvaluationArm,
        repeat: int,
        cases: tuple[tuple[tuple[Finding, ...], tuple[GroundTruthIssue, ...]], ...],
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
        cost_usd: Decimal = Decimal("0"),
    ) -> ArmReport:
        """変更参照ごとに照合し、比較群一回分の測定値へ集約する."""
        if repeat < 1:
            raise InvalidInputError(Messages.EVALUATION_REPEAT_INVALID)
        outcomes: list[MatchOutcome] = []
        issues: list[GroundTruthIssue] = []
        matched_critical: set[str] = set()
        for findings, case_issues in cases:
            matched = match_findings(findings, case_issues)
            outcomes.extend(matched.outcomes)
            issues.extend(case_issues)
            matched_critical.update(
                str(issue.issue_id)
                for issue in case_issues
                if issue.severity is Severity.CRITICAL
                and str(issue.issue_id) in matched.matched_issue_ids
            )
        all_outcomes = tuple(outcomes)
        all_issues = tuple(issues)
        counts = count_matches(all_outcomes)
        return ArmReport(
            arm=arm,
            repeat=repeat,
            counts=counts,
            precision=precision(counts),
            recall=recall(counts),
            critical_recall=critical_recall(_critical_counts(all_outcomes, all_issues)),
            critical_issue_ids=frozenset(matched_critical),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            matches=all_outcomes,
        )

    def compare(
        self,
        rules_reports: tuple[ArmReport, ...],
        memory_reports: tuple[ArmReport, ...],
    ) -> ComparisonVerdict:
        """比較群 B と C を比較して成功判定を返す.

        C が B の検出した重大問題を落とした場合は失敗とする. 反復回数が
        規定に達しない、または測定不能な場合も成功と判定しない.

        Args:
            rules_reports: 比較群 B の各反復の報告.
            memory_reports: 比較群 C の各反復の報告.

        Returns:
            判定結果、失敗理由コード、落とした重大問題の識別子.
        """
        reason_codes: list[str] = []
        if len(rules_reports) < MIN_REPEATS or len(memory_reports) < MIN_REPEATS:
            reason_codes.append("INSUFFICIENT_REPEATS")
        if any(report.counts.review_required > 0 for report in (*rules_reports, *memory_reports)):
            reason_codes.append("ADJUDICATION_REQUIRED")

        # B の一部反復だけで検出した重大問題も、C が維持すべき母集団から除外しない。
        rules_critical = _union_critical(rules_reports)
        memory_critical = _intersection_critical(memory_reports)
        lost = tuple(sorted(rules_critical - memory_critical))
        if lost:
            reason_codes.append("CRITICAL_REGRESSION")
        if not rules_critical:
            reason_codes.append("CRITICAL_RETENTION_NOT_EVALUABLE")

        rules_recall = _mean(report.recall for report in rules_reports)
        memory_recall = _mean(report.recall for report in memory_reports)
        rules_critical_recall = _mean(report.critical_recall for report in rules_reports)
        memory_critical_recall = _mean(report.critical_recall for report in memory_reports)
        if (
            rules_recall is None
            or memory_recall is None
            or rules_critical_recall is None
            or memory_critical_recall is None
        ):
            reason_codes.append("NOT_EVALUABLE")
        else:
            if memory_recall < rules_recall * 0.95:
                reason_codes.append("RECALL_RETENTION_BELOW_95_PERCENT")
            if memory_critical_recall < rules_critical_recall:
                reason_codes.append("CRITICAL_RECALL_REGRESSION")

        rules_false_positives = _mean_count(
            report.counts.false_positive for report in rules_reports
        )
        memory_false_positives = _mean_count(
            report.counts.false_positive for report in memory_reports
        )
        if rules_false_positives is None or rules_false_positives == 0:
            reason_codes.append("FALSE_POSITIVE_REDUCTION_NOT_EVALUABLE")
        elif memory_false_positives is None or memory_false_positives > rules_false_positives * 0.7:
            reason_codes.append("FALSE_POSITIVE_REDUCTION_BELOW_30_PERCENT")

        rules_cost = _mean_cost_per_valid_finding(rules_reports)
        memory_cost = _mean_cost_per_valid_finding(memory_reports)
        if rules_cost is None or memory_cost is None:
            reason_codes.append("COST_PER_VALID_FINDING_NOT_EVALUABLE")
        elif memory_cost > rules_cost:
            reason_codes.append("COST_PER_VALID_FINDING_REGRESSION")

        return ComparisonVerdict(
            is_successful=not reason_codes,
            reason_codes=tuple(reason_codes),
            lost_critical_issue_ids=lost,
        )


def _critical_counts(
    outcomes: tuple[MatchOutcome, ...],
    issues: tuple[GroundTruthIssue, ...],
) -> MatchCounts:
    """重大問題に限定した件数を返す."""
    critical_ids = {str(issue.issue_id) for issue in issues if issue.severity is Severity.CRITICAL}
    filtered = tuple(
        outcome
        for outcome in outcomes
        if outcome.issue_id is not None and str(outcome.issue_id) in critical_ids
    )
    return count_matches(filtered)


def _union_critical(reports: tuple[ArmReport, ...]) -> frozenset[str]:
    """反復をまたいで検出できた重大問題の識別子集合を返す."""
    union: set[str] = set()
    for report in reports:
        union |= report.critical_issue_ids
    return frozenset(union)


def _intersection_critical(reports: tuple[ArmReport, ...]) -> frozenset[str]:
    """全反復で検出できた重大問題の識別子集合を返す."""
    if not reports:
        return frozenset()
    retained = set(reports[0].critical_issue_ids)
    for report in reports[1:]:
        retained &= report.critical_issue_ids
    return frozenset(retained)


def _mean(values: Iterable[MetricValue]) -> float | None:
    """評価可能な測定値の平均を返す. 1 件でも測定不能なら None を返す."""
    collected: list[float] = []
    for value in values:
        if value.status is not MetricStatus.EVALUATED or value.value is None:
            return None
        collected.append(value.value)
    if not collected:
        return None
    return sum(collected) / len(collected)


def _mean_count(values: Iterable[int]) -> float | None:
    """整数件数の平均を返す。入力が空の場合は None を返す."""
    collected = tuple(values)
    if not collected:
        return None
    return sum(collected) / len(collected)


def _mean_cost_per_valid_finding(reports: tuple[ArmReport, ...]) -> Decimal | None:
    """有効指摘一件あたりの平均コストを返す."""
    values = tuple(
        report.cost_usd / report.counts.true_positive
        for report in reports
        if report.counts.true_positive > 0
    )
    if len(values) != len(reports) or not values:
        return None
    return sum(values, start=Decimal("0")) / len(values)
