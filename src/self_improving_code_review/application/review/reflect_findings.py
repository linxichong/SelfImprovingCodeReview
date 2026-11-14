"""決定的検証後の指摘候補をレビュー単位ごとに自己検証する."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from self_improving_code_review.application.ports.llm_client import (
    LlmClient,
    LlmRequest,
    LlmResult,
    LlmUsage,
)
from self_improving_code_review.application.review.prompt_loader import PromptTemplate
from self_improving_code_review.domain.common.enums import (
    ReflectionStatus,
    ValidationStatus,
)
from self_improving_code_review.domain.common.errors import (
    InvalidLlmOutputError,
    LlmClientError,
    ReflectionBudgetExceededError,
)
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.common.tokens import estimate_tokens
from self_improving_code_review.domain.finding.models import Finding
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.prompt_boundary import encode_untrusted_block

__all__ = ["FindingReflector", "ReflectionOutcome"]

_REASON_CODES = frozenset({"EVIDENCE_SUFFICIENT", "EVIDENCE_CONTRADICTED", "CONTEXT_REQUIRED"})
_STATUS_MAP = {
    "supported": ReflectionStatus.SUPPORTED,
    "contradicted": ReflectionStatus.CONTRADICTED,
    "needs-context": ReflectionStatus.NEEDS_CONTEXT,
}
_REASON_BY_STATUS = {
    "supported": "EVIDENCE_SUFFICIENT",
    "contradicted": "EVIDENCE_CONTRADICTED",
    "needs-context": "CONTEXT_REQUIRED",
}
_UNREACHABLE_REFLECTION_LOOP = "reflection repair loop must return or raise"


@dataclass(frozen=True, slots=True)
class ReflectionOutcome:
    """自己検証済み指摘と呼出監査情報."""

    findings: tuple[Finding, ...]
    usage: LlmUsage
    attempt: int
    request_hash: str | None
    model: str | None
    call_count: int


class FindingReflector:
    """レビュー単位の有効候補を 1 回の要求で反証確認する."""

    def __init__(
        self,
        llm_client: LlmClient,
        template: PromptTemplate,
        *,
        max_input_tokens: int,
        max_output_tokens: int,
        temperature: float | None,
    ) -> None:
        """LLM、版管理プロンプト、予算を保持する."""
        self._llm_client = llm_client
        self._template = template
        self._max_input_tokens = max_input_tokens
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature

    @property
    def prompt_version(self) -> str:
        """自己検証プロンプトの版を返す."""
        return self._template.version_label

    @property
    def prompt_hash(self) -> str:
        """自己検証プロンプト内容のハッシュを返す."""
        return self._template.content_hash

    def reflect(self, findings: tuple[Finding, ...]) -> ReflectionOutcome:
        """決定的検証に合格した候補だけを自己検証する."""
        eligible = tuple(
            finding
            for finding in findings
            if finding.validation.position_status is ValidationStatus.VALID
            and finding.validation.evidence_status is ValidationStatus.VALID
            and finding.validation.reflection_status is ReflectionStatus.REFLECTION_SKIPPED
        )
        if not eligible:
            return ReflectionOutcome(findings, LlmUsage(0, 0, 0), 0, None, None, 0)
        candidates = [_candidate_document(finding) for finding in eligible]
        candidate_text = json.dumps(
            candidates,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if estimate_tokens(candidate_text) > self._max_input_tokens:
            raise ReflectionBudgetExceededError(Messages.REFLECTION_BUDGET_EXCEEDED)
        schema = _reflection_schema(len(eligible))
        user_content = self._template.user_template.format(
            output_schema=json.dumps(schema, sort_keys=True, separators=(",", ":")),
            candidates_block=encode_untrusted_block("reflection-candidates", candidate_text),
        )
        request = LlmRequest(
            system_instruction=self._template.system_instruction,
            user_content=user_content,
            output_schema=schema,
            max_output_tokens=self._max_output_tokens,
            temperature=self._temperature,
            request_label=f"reflection:{eligible[0].bundle_id}",
        )
        result = self._generate_with_repair(
            request,
            tuple(str(item.finding_id) for item in eligible),
        )
        statuses = _parse_reflection(
            result.payload,
            tuple(str(item.finding_id) for item in eligible),
        )
        updated = tuple(
            _with_reflection(finding, statuses.get(str(finding.finding_id))) for finding in findings
        )
        return ReflectionOutcome(
            findings=updated,
            usage=result.usage,
            attempt=result.attempt,
            request_hash=hash_text(request.system_instruction + "\n" + request.user_content),
            model=result.model,
            call_count=result.attempt,
        )

    def _generate_with_repair(
        self,
        request: LlmRequest,
        expected_ids: tuple[str, ...],
    ) -> LlmResult:
        """自己検証応答を一度だけ形式修復し、使用量を合算する."""
        current = request
        usage = LlmUsage(0, 0, 0)
        attempts = 0
        for repair_attempt in range(2):
            try:
                result = self._llm_client.generate_structured(current)
            except LlmClientError as exc:
                raise LlmClientError(
                    str(exc),
                    is_retryable=exc.is_retryable,
                    attempt=attempts + exc.attempt,
                    latency_ms=usage.latency_ms + exc.latency_ms,
                    input_tokens=usage.input_tokens + exc.input_tokens,
                    output_tokens=usage.output_tokens + exc.output_tokens,
                    cost_usd=usage.cost_usd + exc.cost_usd,
                ) from exc
            except InvalidLlmOutputError as exc:
                usage = _add_error_usage(usage, exc)
                attempts += exc.attempt
                if repair_attempt == 1:
                    raise InvalidLlmOutputError(
                        Messages.REFLECTION_RESPONSE_INVALID,
                        attempt=attempts,
                        latency_ms=usage.latency_ms,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cost_usd=usage.cost_usd,
                    ) from exc
                current = _reflection_repair_request(request)
                continue
            usage = _add_usage(usage, result.usage)
            attempts += result.attempt
            try:
                _parse_reflection(result.payload, expected_ids)
            except InvalidLlmOutputError as exc:
                if repair_attempt == 1:
                    raise InvalidLlmOutputError(
                        Messages.REFLECTION_RESPONSE_INVALID,
                        attempt=attempts,
                        latency_ms=usage.latency_ms,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cost_usd=usage.cost_usd,
                    ) from exc
                current = _reflection_repair_request(request)
                continue
            return replace(result, usage=usage, attempt=attempts)
        raise AssertionError(_UNREACHABLE_REFLECTION_LOOP)


def _reflection_schema(item_count: int) -> dict[str, Any]:
    """候補数を固定した自己検証スキーマを返す."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "results"],
        "properties": {
            "schema_version": {"const": 1},
            "results": {
                "type": "array",
                "minItems": item_count,
                "maxItems": item_count,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["finding_id", "status", "reason_code"],
                    "properties": {
                        "finding_id": {"type": "string"},
                        "status": {"enum": list(_STATUS_MAP)},
                        "reason_code": {"enum": sorted(_REASON_CODES)},
                    },
                },
            },
        },
    }


def _reflection_repair_request(request: LlmRequest) -> LlmRequest:
    """不正な自己検証応答を JSON だけに修復する要求を返す."""
    return replace(
        request,
        user_content=(
            request.user_content
            + "\n\n形式修復: 説明文やコードフェンスを付けず、"
            + "スキーマに一致する JSON オブジェクトだけを返してください。"
        ),
    )


def _parse_reflection(
    payload: dict[str, Any],
    expected_ids: tuple[str, ...],
) -> dict[str, ReflectionStatus]:
    """自己検証応答の閉じた項目と ID 集合を検証する."""
    if frozenset(payload) != {"schema_version", "results"} or payload.get("schema_version") != 1:
        raise InvalidLlmOutputError(Messages.REFLECTION_RESPONSE_INVALID)
    results = payload.get("results")
    if not isinstance(results, list) or len(results) != len(expected_ids):
        raise InvalidLlmOutputError(Messages.REFLECTION_RESPONSE_INVALID)
    statuses: dict[str, ReflectionStatus] = {}
    for item in results:
        if not isinstance(item, dict) or frozenset(item) != {
            "finding_id",
            "status",
            "reason_code",
        }:
            raise InvalidLlmOutputError(Messages.REFLECTION_RESPONSE_INVALID)
        finding_id = item["finding_id"]
        status = item["status"]
        reason_code = item["reason_code"]
        if (
            not isinstance(finding_id, str)
            or status not in _STATUS_MAP
            or reason_code != _REASON_BY_STATUS.get(str(status))
            or finding_id in statuses
        ):
            raise InvalidLlmOutputError(Messages.REFLECTION_RESPONSE_INVALID)
        statuses[finding_id] = _STATUS_MAP[str(status)]
    if frozenset(statuses) != frozenset(expected_ids):
        raise InvalidLlmOutputError(Messages.REFLECTION_RESPONSE_INVALID)
    return statuses


def _candidate_document(finding: Finding) -> dict[str, object]:
    """自己検証へ送る最小候補表現を返す."""
    candidate = finding.candidate
    return {
        "finding_id": str(finding.finding_id),
        "path": candidate.file.value,
        "severity": candidate.severity.value,
        "title": candidate.title,
        "description": candidate.description,
        "rule_id": candidate.rule_id,
        "evidence": candidate.evidence.quote,
    }


def _with_reflection(
    finding: Finding,
    status: ReflectionStatus | None,
) -> Finding:
    """対応する自己検証状態がある指摘だけを更新する."""
    if status is None:
        return finding
    reason_code = {
        ReflectionStatus.SUPPORTED: "EVIDENCE_SUFFICIENT",
        ReflectionStatus.CONTRADICTED: "EVIDENCE_CONTRADICTED",
        ReflectionStatus.NEEDS_CONTEXT: "CONTEXT_REQUIRED",
    }[status]
    return replace(
        finding,
        validation=replace(
            finding.validation,
            reflection_status=status,
            reason_codes=(*finding.validation.reason_codes, reason_code),
        ),
    )


def _add_usage(left: LlmUsage, right: LlmUsage) -> LlmUsage:
    """LLM 使用量を合算する."""
    return LlmUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        latency_ms=left.latency_ms + right.latency_ms,
        cost_usd=left.cost_usd + right.cost_usd,
    )


def _add_error_usage(left: LlmUsage, error: InvalidLlmOutputError) -> LlmUsage:
    """形式不正応答で消費した使用量を自己検証の合計へ加える."""
    return _add_usage(
        left,
        LlmUsage(
            error.input_tokens,
            error.output_tokens,
            error.latency_ms,
            error.cost_usd,
        ),
    )
