"""フィンガープリントに基づく指摘の重複排除."""

from __future__ import annotations

from self_improving_code_review.domain.finding.models import Finding

__all__ = ["deduplicate"]


def deduplicate(findings: tuple[Finding, ...]) -> tuple[tuple[Finding, ...], tuple[Finding, ...]]:
    """``findings`` を採用する指摘と置換される指摘に分ける.

    各フィンガープリントでは重大度が最も高い指摘を採用する.
    同率の場合はより具体的な根拠(長い理由)を優先し、それでも同率なら
    入力順を維持する. 採用した指摘の相対順は各フィンガープリントの
    初出位置に従い、出力の再現性を保つ.

    Args:
        findings: 入力順の実行内検証済み指摘.

    Returns:
        採用した指摘と置換された重複指摘のタプル.
    """
    winners: dict[str, Finding] = {}
    order: list[str] = []
    losers: list[Finding] = []

    for finding in findings:
        fingerprint = finding.fingerprint
        current = winners.get(fingerprint)
        if current is None:
            winners[fingerprint] = finding
            order.append(fingerprint)
            continue
        if _is_preferred(finding, current):
            winners[fingerprint] = finding
            losers.append(current)
        else:
            losers.append(finding)

    kept = tuple(winners[fingerprint] for fingerprint in order)
    return kept, tuple(losers)


def _is_preferred(challenger: Finding, incumbent: Finding) -> bool:
    """``challenger`` が ``incumbent`` を置き換えるべき場合は True を返す."""
    if challenger.severity.rank != incumbent.severity.rank:
        return challenger.severity.rank > incumbent.severity.rank
    challenger_specificity = len(challenger.candidate.evidence.reason.strip())
    incumbent_specificity = len(incumbent.candidate.evidence.reason.strip())
    return challenger_specificity > incumbent_specificity
