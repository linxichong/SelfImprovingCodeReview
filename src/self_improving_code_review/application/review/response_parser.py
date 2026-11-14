"""レビュー応答の解析.

解析処理はスキーマ検証済みの送信内容を指摘候補へ変換する. 構造上の
問題では :class:`InvalidLlmOutputError` を送出する. レビュー単位外の
ファイルを対象とする個別候補は、レビュー単位全体を失敗させず、
理由コードを付けて除外する.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from self_improving_code_review.application.review.models import ReviewBundle
from self_improving_code_review.application.review.output_schema import (
    REVIEW_OUTPUT_SCHEMA_VERSION,
)
from self_improving_code_review.domain.common.enums import Severity
from self_improving_code_review.domain.common.errors import (
    InvalidInputError,
    InvalidLlmOutputError,
)
from self_improving_code_review.domain.common.values import LineRange, NormalizedPath
from self_improving_code_review.domain.finding.models import CandidateFinding, Evidence
from self_improving_code_review.messages import Messages

__all__ = ["ParsedResponse", "RejectedCandidate", "parse_review_response"]


@dataclass(frozen=True, slots=True)
class RejectedCandidate:
    """検証前に除外された候補とその理由コード."""

    reason_code: str
    detail: str


@dataclass(frozen=True, slots=True)
class ParsedResponse:
    """検証対象として受け入れた候補と解析時に除外した候補."""

    candidates: tuple[CandidateFinding, ...]
    rejected: tuple[RejectedCandidate, ...]


def parse_review_response(payload: dict[str, Any], bundle: ReviewBundle) -> ParsedResponse:
    """``payload`` を ``bundle`` の指摘候補へ変換する.

    Args:
        payload: スキーマ検証済みのレビュー応答.
        bundle: 応答が属するレビュー単位.

    Returns:
        解析済み候補と除外項目.

    Raises:
        InvalidLlmOutputError: 送信内容のバージョンまたは最上位構造が不正な場合.
    """
    version = payload.get("schema_version")
    if version != REVIEW_OUTPUT_SCHEMA_VERSION:
        message = f"{Messages.RESPONSE_SCHEMA_VERSION_UNSUPPORTED}: {version!r}"
        raise InvalidLlmOutputError(message)

    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        message = Messages.RESPONSE_FINDINGS_NOT_ARRAY
        raise InvalidLlmOutputError(message)

    allowed_paths = frozenset(bundle.paths)
    candidates: list[CandidateFinding] = []
    rejected: list[RejectedCandidate] = []

    for entry in raw_findings:
        if not isinstance(entry, dict):
            rejected.append(
                RejectedCandidate(reason_code="MALFORMED_FINDING", detail="not an object")
            )
            continue
        candidate = _build_candidate(entry, allowed_paths, rejected)
        if candidate is not None:
            candidates.append(candidate)

    return ParsedResponse(candidates=tuple(candidates), rejected=tuple(rejected))


def _build_candidate(
    entry: dict[str, Any],
    allowed_paths: frozenset[str],
    rejected: list[RejectedCandidate],
) -> CandidateFinding | None:
    """1件の応答要素を候補へ変換する。除外した場合は理由を記録する."""
    raw_path = str(entry.get("file", ""))
    try:
        path = NormalizedPath(raw_path)
    except InvalidInputError as exc:
        rejected.append(RejectedCandidate(reason_code="INVALID_PATH", detail=str(exc)))
        return None

    if path.value not in allowed_paths:
        rejected.append(RejectedCandidate(reason_code="FILE_NOT_IN_BUNDLE", detail=path.value))
        return None

    try:
        line_range = LineRange(start_line=int(entry["start_line"]), end_line=int(entry["end_line"]))
    except (KeyError, TypeError, ValueError, InvalidInputError) as exc:
        rejected.append(RejectedCandidate(reason_code="INVALID_LINE_RANGE", detail=str(exc)))
        return None

    try:
        severity = Severity(str(entry["severity"]))
    except (KeyError, ValueError) as exc:
        rejected.append(RejectedCandidate(reason_code="INVALID_SEVERITY", detail=str(exc)))
        return None

    raw_evidence = entry.get("evidence")
    if not isinstance(raw_evidence, dict):
        rejected.append(RejectedCandidate(reason_code="MISSING_EVIDENCE", detail=path.value))
        return None

    return CandidateFinding(
        file=path,
        line_range=line_range,
        category=str(entry.get("category", "")).strip(),
        issue_type=str(entry.get("issue_type", "")).strip(),
        rule_id=str(entry.get("rule_id", "")).strip(),
        primary_entity=str(entry.get("primary_entity", "")).strip(),
        severity=severity,
        title=str(entry.get("title", "")).strip(),
        description=str(entry.get("description", "")).strip(),
        evidence=Evidence(
            quote=str(raw_evidence.get("quote", "")),
            reason=str(raw_evidence.get("reason", "")).strip(),
        ),
        suggestion=str(entry.get("suggestion", "")).strip(),
        needs_context=bool(entry.get("needs_context", False)),
    )
