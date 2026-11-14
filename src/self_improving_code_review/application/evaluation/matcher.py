"""指摘と正解データの自動照合.

照合は決定的に行い、LLM を使用しない. 候補が 0 件または複数件の場合は
``REVIEW_REQUIRED`` として人間の判定へ回す.
"""

from __future__ import annotations

from dataclasses import dataclass

from self_improving_code_review.domain.common.enums import MatchResult
from self_improving_code_review.domain.common.values import LineRange
from self_improving_code_review.domain.evaluation.models import GroundTruthIssue, MatchOutcome
from self_improving_code_review.domain.finding.models import Finding

__all__ = ["LINE_TOLERANCE", "MatchReport", "match_findings"]

LINE_TOLERANCE = 3


@dataclass(frozen=True, slots=True)
class MatchReport:
    """1 件の変更参照に対する照合結果."""

    outcomes: tuple[MatchOutcome, ...]
    matched_issue_ids: frozenset[str]


def match_findings(
    findings: tuple[Finding, ...],
    issues: tuple[GroundTruthIssue, ...],
) -> MatchReport:
    """``findings`` を ``issues`` へ照合する.

    1 つの正解データに複数の指摘が一致した場合、最初の 1 件を真陽性、
    残りを重複かつ偽陽性として扱う. 参照指摘は呼び出し元が除外し、
    この処理には主要指摘だけを渡す.

    Args:
        findings: 同じ変更参照に対する主要指摘. 入力順を判定順とする.
        issues: 同じ変更参照に属する正解データ問題.

    Returns:
        照合結果と、検出できた正解データ識別子の集合.
    """
    outcomes: list[MatchOutcome] = []
    matched: set[str] = set()

    for finding in findings:
        candidates = tuple(issue for issue in issues if _is_candidate(finding, issue))
        if len(candidates) != 1:
            outcomes.append(
                MatchOutcome(
                    result=MatchResult.REVIEW_REQUIRED,
                    finding_id=finding.finding_id,
                    issue_id=candidates[0].issue_id if len(candidates) == 1 else None,
                )
            )
            continue

        issue = candidates[0]
        key = str(issue.issue_id)
        result = MatchResult.DUPLICATE if key in matched else MatchResult.TRUE_POSITIVE
        matched.add(key)
        outcomes.append(
            MatchOutcome(
                result=result,
                finding_id=finding.finding_id,
                issue_id=issue.issue_id,
            )
        )

    outcomes.extend(
        MatchOutcome(result=MatchResult.FALSE_NEGATIVE, issue_id=issue.issue_id)
        for issue in issues
        if str(issue.issue_id) not in matched
    )

    return MatchReport(outcomes=tuple(outcomes), matched_issue_ids=frozenset(matched))


def _is_candidate(finding: Finding, issue: GroundTruthIssue) -> bool:
    """``finding`` が ``issue`` の候補となる場合は True を返す.

    パスが一致し、問題種別またはルール識別子が互換で、位置が近接する
    ものだけを候補とする.
    """
    if finding.candidate.file.value != issue.file.value:
        return False
    if not _is_compatible_issue_type(finding, issue):
        return False
    return _is_near_location(finding, issue)


def _is_compatible_issue_type(finding: Finding, issue: GroundTruthIssue) -> bool:
    """問題種別またはルール識別子が互換の場合は True を返す."""
    if issue.rule_id is not None and finding.candidate.rule_id == issue.rule_id:
        return True
    return _normalize(finding.candidate.issue_type) == _normalize(issue.issue_type)


def _is_near_location(finding: Finding, issue: GroundTruthIssue) -> bool:
    """位置が許容範囲内で一致する場合は True を返す."""
    if issue.start_line is not None:
        tolerant = LineRange(
            start_line=max(issue.start_line - LINE_TOLERANCE, 1),
            end_line=issue.start_line + LINE_TOLERANCE,
        )
        return finding.candidate.line_range.overlaps(tolerant)
    return _normalize(finding.enclosing_symbol) == _normalize(issue.symbol or "")


def _normalize(value: str) -> str:
    """照合用に記号差を無視した比較キーを返す."""
    return "".join(character for character in value.lower() if character.isalnum())
