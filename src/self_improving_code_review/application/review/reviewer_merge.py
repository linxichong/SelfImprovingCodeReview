"""M5 の担当別レビュー結果を決定的に統合する."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from self_improving_code_review.application.review.models import BundleOutcome
from self_improving_code_review.domain.common.enums import (
    BundleStatus,
    ReviewerType,
    ValidationStatus,
)
from self_improving_code_review.domain.common.values import FindingId
from self_improving_code_review.domain.finding.models import Finding
from self_improving_code_review.messages import Messages

__all__ = [
    "FindingSource",
    "MergedReviewerResult",
    "ReviewerResult",
    "merge_reviewer_results",
]

_PROFILE_PRIORITY: dict[ReviewerType, int] = {
    ReviewerType.SECURITY: 0,
    ReviewerType.DATA: 1,
    ReviewerType.TEST: 2,
    ReviewerType.GENERAL: 3,
}


@dataclass(frozen=True, slots=True)
class ReviewerResult:
    """一担当から得た検証済みレビュー結果と選択情報."""

    reviewer_type: ReviewerType
    reason_code: str
    score: int
    outcome: BundleOutcome
    is_primary: bool = False


@dataclass(frozen=True, slots=True)
class FindingSource:
    """統合後指摘と担当別元指摘の安全な対応情報."""

    finding_id: FindingId
    reviewer_type: ReviewerType
    source_finding_id: FindingId
    source_fingerprint: str
    is_selected: bool


@dataclass(frozen=True, slots=True)
class ReviewerExecutionSummary:
    """一担当の永続化可能な結果件数."""

    reviewer_type: ReviewerType
    reason_code: str
    score: int
    status: BundleStatus
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: Decimal
    valid_finding_count: int
    duplicate_count: int
    prompt_version: str = ""
    prompt_hash: str = ""
    input_hash: str = ""


@dataclass(frozen=True, slots=True)
class MergedReviewerResult:
    """統合済みレビュー単位と採用元・担当別集計."""

    outcome: BundleOutcome
    sources: tuple[FindingSource, ...]
    executions: tuple[ReviewerExecutionSummary, ...]


def merge_reviewer_results(
    results: tuple[ReviewerResult, ...],
) -> MergedReviewerResult:
    """担当横断で重複を統合し、安定した指摘識別子を再採番する."""
    if not results:
        raise ValueError(Messages.REVIEWER_RESULTS_EMPTY)
    primary = next((item for item in results if item.is_primary), None)
    if primary is None:
        raise ValueError(Messages.REVIEWER_PRIMARY_RESULT_MISSING)

    total_input = sum(item.outcome.input_tokens for item in results)
    total_output = sum(item.outcome.output_tokens for item in results)
    total_latency = sum(item.outcome.latency_ms for item in results)
    total_cost = sum((item.outcome.cost_usd for item in results), Decimal("0"))
    successful = tuple(item for item in results if item.outcome.status is BundleStatus.SUCCESS)
    if primary.outcome.status is not BundleStatus.SUCCESS:
        outcome = replace(
            primary.outcome,
            findings=(),
            input_tokens=total_input,
            output_tokens=total_output,
            latency_ms=total_latency,
            cost_usd=total_cost,
        )
        return MergedReviewerResult(
            outcome=outcome,
            sources=(),
            executions=_execution_summaries(results, ()),
        )

    grouped: dict[str, list[tuple[ReviewerResult, Finding]]] = {}
    for result in successful:
        for finding in result.outcome.findings:
            grouped.setdefault(finding.fingerprint, []).append((result, finding))

    selected: list[tuple[ReviewerResult, Finding, tuple[tuple[ReviewerResult, Finding], ...]]] = []
    for fingerprint in sorted(grouped):
        candidates = tuple(grouped[fingerprint])
        winner = min(candidates, key=_display_priority)
        selected.append((winner[0], winner[1], candidates))

    ordered = sorted(selected, key=lambda item: _finding_order(item[1]))
    findings: list[Finding] = []
    sources: list[FindingSource] = []
    duplicate_counts: dict[ReviewerType, int] = {}
    for sequence, (winner_result, selected_finding, candidates) in enumerate(ordered, start=1):
        finding_id = FindingId(selected_finding.bundle_id, sequence)
        findings.append(replace(selected_finding, finding_id=finding_id))
        for source_result, source in candidates:
            is_selected = (
                source_result.reviewer_type is winner_result.reviewer_type
                and source.finding_id == selected_finding.finding_id
            )
            sources.append(
                FindingSource(
                    finding_id=finding_id,
                    reviewer_type=source_result.reviewer_type,
                    source_finding_id=source.finding_id,
                    source_fingerprint=source.fingerprint,
                    is_selected=is_selected,
                )
            )
            if not is_selected:
                duplicate_counts[source_result.reviewer_type] = (
                    duplicate_counts.get(source_result.reviewer_type, 0) + 1
                )

    has_auxiliary_failure = any(
        not item.is_primary and item.outcome.status is not BundleStatus.SUCCESS for item in results
    )
    outcome = BundleOutcome(
        bundle_id=primary.outcome.bundle_id,
        status=(BundleStatus.PARTIAL_SUCCESS if has_auxiliary_failure else BundleStatus.SUCCESS),
        findings=tuple(findings),
        error_code=("SPECIALIST_REVIEW_FAILED" if has_auxiliary_failure else None),
        error_message=(
            "one or more specialist reviewers failed" if has_auxiliary_failure else None
        ),
        input_tokens=total_input,
        output_tokens=total_output,
        latency_ms=total_latency,
        cost_usd=total_cost,
    )
    return MergedReviewerResult(
        outcome=outcome,
        sources=tuple(sources),
        executions=_execution_summaries(results, tuple(duplicate_counts.items())),
    )


def _display_priority(item: tuple[ReviewerResult, Finding]) -> tuple[object, ...]:
    """同一指紋の表示値を選ぶ決定的な優先キーを返す."""
    result, finding = item
    validation = finding.validation
    return (
        -finding.severity.rank,
        -(validation.evidence_status is ValidationStatus.VALID),
        -(validation.position_status is ValidationStatus.VALID),
        _PROFILE_PRIORITY[result.reviewer_type],
        str(finding.finding_id),
    )


def _finding_order(finding: Finding) -> tuple[object, ...]:
    """統合後の再採番に使う安定した指摘順を返す."""
    return (
        finding.candidate.file.value,
        finding.candidate.line_range.start_line,
        -finding.severity.rank,
        finding.fingerprint,
    )


def _execution_summaries(
    results: tuple[ReviewerResult, ...],
    duplicate_counts: tuple[tuple[ReviewerType, int], ...],
) -> tuple[ReviewerExecutionSummary, ...]:
    """担当別の有効指摘数と重複数を集計する."""
    duplicates = dict(duplicate_counts)
    return tuple(
        ReviewerExecutionSummary(
            reviewer_type=item.reviewer_type,
            reason_code=item.reason_code,
            score=item.score,
            status=item.outcome.status,
            input_tokens=item.outcome.input_tokens,
            output_tokens=item.outcome.output_tokens,
            latency_ms=item.outcome.latency_ms,
            cost_usd=item.outcome.cost_usd,
            valid_finding_count=sum(
                finding.validation.is_displayable for finding in item.outcome.findings
            ),
            duplicate_count=duplicates.get(item.reviewer_type, 0),
        )
        for item in results
    )
