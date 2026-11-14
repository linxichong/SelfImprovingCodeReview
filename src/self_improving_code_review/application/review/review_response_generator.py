"""構造化レビュー応答を検証し、一度だけ形式修復を試みる."""

from __future__ import annotations

from dataclasses import replace

from self_improving_code_review.application.ports.llm_client import (
    LlmClient,
    LlmRequest,
    LlmResult,
    LlmUsage,
)
from self_improving_code_review.application.ports.telemetry import Telemetry
from self_improving_code_review.application.review.bundle_review_outcomes import (
    add_error_usage,
    add_usage,
)
from self_improving_code_review.application.review.output_schema import (
    validate_review_output_payload,
)
from self_improving_code_review.domain.common.errors import (
    InvalidLlmOutputError,
    LlmClientError,
)


def generate_review_response(
    request: LlmRequest,
    *,
    llm_client: LlmClient,
    telemetry: Telemetry,
    max_findings: int,
) -> LlmResult:
    """生成と形式修復を既存の単一観測区間で実行する."""
    with telemetry.start_span("llm.generate"):
        return _generate_with_repair(request, llm_client=llm_client, max_findings=max_findings)


def _generate_with_repair(
    request: LlmRequest,
    *,
    llm_client: LlmClient,
    max_findings: int,
) -> LlmResult:
    """構造化出力を検証し、形式修復を含む全呼び出し測定値を集計する."""
    current_request = request
    total_usage = LlmUsage(0, 0, 0)
    total_attempts = 0
    repair_attempt = 0
    while True:
        try:
            result = llm_client.generate_structured(current_request)
        except LlmClientError as exc:
            raise LlmClientError(
                str(exc),
                is_retryable=exc.is_retryable,
                attempt=total_attempts + exc.attempt,
                latency_ms=total_usage.latency_ms + exc.latency_ms,
                input_tokens=total_usage.input_tokens + exc.input_tokens,
                output_tokens=total_usage.output_tokens + exc.output_tokens,
                cost_usd=total_usage.cost_usd + exc.cost_usd,
            ) from exc
        except InvalidLlmOutputError as exc:
            total_usage = add_error_usage(total_usage, exc)
            total_attempts += exc.attempt
            if repair_attempt == 1:
                raise InvalidLlmOutputError(
                    str(exc),
                    attempt=total_attempts,
                    latency_ms=total_usage.latency_ms,
                    input_tokens=total_usage.input_tokens,
                    output_tokens=total_usage.output_tokens,
                    cost_usd=total_usage.cost_usd,
                ) from exc
        else:
            total_usage = add_usage(total_usage, result.usage)
            total_attempts += result.attempt
            try:
                validate_review_output_payload(
                    result.payload,
                    max_findings=max_findings,
                )
            except InvalidLlmOutputError as exc:
                if repair_attempt == 1:
                    raise InvalidLlmOutputError(
                        str(exc),
                        attempt=total_attempts,
                        latency_ms=total_usage.latency_ms,
                        input_tokens=total_usage.input_tokens,
                        output_tokens=total_usage.output_tokens,
                        cost_usd=total_usage.cost_usd,
                    ) from exc
            else:
                return replace(result, usage=total_usage, attempt=total_attempts)

        current_request = replace(
            request,
            user_content=(
                request.user_content
                + "\n\n## 形式修復\n"
                + "前回の応答は不正です。説明文や Markdown コードフェンスを付けず、"
                + "応答スキーマに一致する JSON オブジェクトを 1 つだけ返してください。"
            ),
        )
        repair_attempt += 1
