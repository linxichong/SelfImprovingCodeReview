"""検証済み指摘から安定した SCM コメント計画を作成する."""

from __future__ import annotations

from self_improving_code_review.application.review.models import ReviewResult
from self_improving_code_review.application.scm.models import PublicationPlan
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.finding.models import Finding
from self_improving_code_review.domain.scm.models import (
    CommentPosition,
    CreateComment,
    PublicationKind,
    ScmChange,
)

_SUMMARY_FINGERPRINT = "scm-review-summary"


def plan_publications(
    change: ScmChange,
    result: ReviewResult,
    *,
    max_inline_comments: int,
) -> tuple[PublicationPlan, ...]:
    """主要指摘を行コメントへ、残りを一件の概要コメントへ決定的に配置する."""
    ordered_main = tuple(sorted(result.main_findings, key=_finding_sort_key))
    inline_findings = ordered_main[:max_inline_comments]
    overflow_findings = ordered_main[max_inline_comments:]
    plans = [_inline_plan(change, result, finding) for finding in inline_findings]
    summary_findings = overflow_findings + tuple(
        sorted(result.reference_findings, key=_finding_sort_key)
    )
    if summary_findings or not ordered_main:
        plans.append(_summary_plan(change, result, summary_findings))
    return tuple(plans)


def _inline_plan(
    change: ScmChange,
    result: ReviewResult,
    finding: Finding,
) -> PublicationPlan:
    """一件の主要指摘を GitHub の新規行コメント計画へ変換する."""
    fingerprint_hash = hash_text(finding.fingerprint)
    publication_id = _publication_id(change, PublicationKind.INLINE, fingerprint_hash)
    body = _finding_body(finding, publication_id, change.head_sha)
    request = CreateComment(
        kind=PublicationKind.INLINE,
        body=body,
        position=CommentPosition(
            path=finding.candidate.file,
            line=finding.candidate.line_range.start_line,
            head_sha=change.head_sha,
        ),
    )
    return PublicationPlan(
        publication_id=publication_id,
        run_id=str(result.run.run_id),
        finding_id=str(finding.finding_id),
        fingerprint_hash=fingerprint_hash,
        request_hash=_request_hash(request),
        request=request,
    )


def _summary_plan(
    change: ScmChange,
    result: ReviewResult,
    findings: tuple[Finding, ...],
) -> PublicationPlan:
    """行コメントへ配置しない指摘を単一の概要コメントへまとめる."""
    fingerprint_hash = hash_text(_SUMMARY_FINGERPRINT)
    publication_id = _publication_id(change, PublicationKind.SUMMARY, fingerprint_hash)
    if findings:
        sections = "\n\n".join(_summary_finding(finding) for finding in findings)
        body = f"## AI コードレビュー概要\n\n行コメント以外の指摘: {len(findings)} 件\n\n{sections}"
    else:
        body = "## AI コードレビュー概要\n\n検証済みの指摘はありません。"
    body = f"{body}\n\n{_marker(publication_id, change.head_sha)}"
    request = CreateComment(kind=PublicationKind.SUMMARY, body=body)
    return PublicationPlan(
        publication_id=publication_id,
        run_id=str(result.run.run_id),
        finding_id=None,
        fingerprint_hash=fingerprint_hash,
        request_hash=_request_hash(request),
        request=request,
    )


def _finding_body(finding: Finding, publication_id: str, head_sha: str) -> str:
    """行コメント用の日本語本文と追跡マーカーを返す."""
    candidate = finding.candidate
    return (
        f"**{candidate.severity.value.upper()}: {candidate.title}**\n\n"
        f"説明: {candidate.description}\n\n"
        f"根拠理由: {candidate.evidence.reason}\n\n"
        f"提案: {candidate.suggestion}\n\n"
        f"ルール ID: {candidate.rule_id}\n\n"
        f"{_marker(publication_id, head_sha)}"
    )


def _summary_finding(finding: Finding) -> str:
    """概要内の一件分を短い箇条書きへ変換する."""
    candidate = finding.candidate
    return (
        f"- **{candidate.severity.value.upper()}** "
        f"`{candidate.file.value}:{candidate.line_range.start_line}` "
        f"{candidate.title}\n"
        f"  - 説明: {candidate.description}\n"
        f"  - 根拠理由: {candidate.evidence.reason}\n"
        f"  - 提案: {candidate.suggestion}\n"
        f"  - ルール ID: {candidate.rule_id}"
    )


def _marker(publication_id: str, head_sha: str) -> str:
    """重複投稿照合に使う非表示マーカーを返す."""
    return f"<!-- self-reviewer:publication={publication_id};head={head_sha} -->"


def _publication_id(
    change: ScmChange,
    kind: PublicationKind,
    fingerprint_hash: str,
) -> str:
    """変更要求と投稿単位から安定した内部出版物 ID を生成する."""
    return hash_text(
        "\n".join(
            (
                change.ref.provider.value,
                change.ref.repository_external_id,
                change.ref.change_external_id,
                change.head_sha,
                kind.value,
                fingerprint_hash,
            )
        )
    )


def _request_hash(request: CreateComment) -> str:
    """本文と位置から提供元非依存の要求ハッシュを返す."""
    if request.position is None:
        position = "summary"
    else:
        position = (
            f"{request.position.path.value}:{request.position.line}:"
            f"{request.position.side}:{request.position.head_sha}"
        )
    return hash_text(f"{request.kind.value}\n{position}\n{request.body}")


def _finding_sort_key(finding: Finding) -> tuple[int, str, int, str]:
    """重大度、パス、行、指摘 ID の安定順キーを返す."""
    return (
        -finding.severity.rank,
        finding.candidate.file.value,
        finding.candidate.line_range.start_line,
        str(finding.finding_id),
    )
