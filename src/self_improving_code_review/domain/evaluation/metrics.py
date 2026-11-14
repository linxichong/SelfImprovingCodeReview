"""評価測定値の算出.

測定値は分子と分母をともに保存し、再検証できるようにする.
母数が 0 の場合は 0 を返さず ``NOT_EVALUABLE`` とする.
"""

from __future__ import annotations

from dataclasses import dataclass

from self_improving_code_review.domain.common.enums import MatchResult, MetricStatus
from self_improving_code_review.domain.evaluation.models import MatchOutcome

__all__ = [
    "MatchCounts",
    "MetricValue",
    "count_matches",
    "critical_recall",
    "precision",
    "recall",
]


@dataclass(frozen=True, slots=True)
class MetricValue:
    """分子と分母を保持する測定値.

    ``status`` が ``NOT_EVALUABLE`` の場合、``value`` は None とし、
    0 を有効な測定結果として扱わない.
    """

    metric: str
    numerator: int
    denominator: int
    status: MetricStatus
    value: float | None = None

    @property
    def is_evaluable(self) -> bool:
        """測定値を比較に使用できる場合は True を返す."""
        return self.status is MetricStatus.EVALUATED


@dataclass(frozen=True, slots=True)
class MatchCounts:
    """照合結果の件数集計."""

    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    duplicate: int = 0
    review_required: int = 0


def count_matches(outcomes: tuple[MatchOutcome, ...]) -> MatchCounts:
    """``outcomes`` を結果種別ごとに集計する.

    重複は真陽性に数えず、重複かつ偽陽性として扱うため、
    ``duplicate`` と ``false_positive`` の双方を加算する.
    """
    true_positive = 0
    false_positive = 0
    false_negative = 0
    duplicate = 0
    review_required = 0

    for outcome in outcomes:
        if outcome.result is MatchResult.TRUE_POSITIVE:
            true_positive += 1
        elif outcome.result is MatchResult.FALSE_POSITIVE:
            false_positive += 1
        elif outcome.result is MatchResult.FALSE_NEGATIVE:
            false_negative += 1
        elif outcome.result is MatchResult.DUPLICATE:
            duplicate += 1
            false_positive += 1
        else:
            review_required += 1

    return MatchCounts(
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        duplicate=duplicate,
        review_required=review_required,
    )


def precision(counts: MatchCounts) -> MetricValue:
    """適合率を返す. 報告した指摘が 0 件の場合は評価不能とする."""
    return _ratio(
        "precision",
        numerator=counts.true_positive,
        denominator=counts.true_positive + counts.false_positive,
    )


def recall(counts: MatchCounts) -> MetricValue:
    """再現率を返す. 既知問題が 0 件の場合は評価不能とする."""
    return _ratio(
        "recall",
        numerator=counts.true_positive,
        denominator=counts.true_positive + counts.false_negative,
    )


def critical_recall(counts: MatchCounts) -> MetricValue:
    """重大問題の再現率を返す. 重大問題が 0 件の場合は評価不能とする."""
    return _ratio(
        "critical_recall",
        numerator=counts.true_positive,
        denominator=counts.true_positive + counts.false_negative,
    )


def _ratio(metric: str, *, numerator: int, denominator: int) -> MetricValue:
    """分母 0 を評価不能として扱う比率を返す."""
    if denominator == 0:
        return MetricValue(
            metric=metric,
            numerator=numerator,
            denominator=denominator,
            status=MetricStatus.NOT_EVALUABLE,
        )
    return MetricValue(
        metric=metric,
        numerator=numerator,
        denominator=denominator,
        status=MetricStatus.EVALUATED,
        value=numerator / denominator,
    )
