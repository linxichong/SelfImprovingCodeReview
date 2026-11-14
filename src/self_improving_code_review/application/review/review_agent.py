"""限定ツールを使用する単一レビュー担当の状態機械."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal

from self_improving_code_review.application.ports.llm_client import (
    LlmClient,
    LlmRequest,
    LlmResult,
    LlmUsage,
)
from self_improving_code_review.application.review.agent_turn_schema import (
    AgentTurn,
    build_agent_turn_schema,
    parse_agent_turn,
)
from self_improving_code_review.application.review.context_tools import (
    ContextToolCoordinator,
    ContextToolOutcome,
    ContextToolSession,
)
from self_improving_code_review.domain.common.errors import (
    InvalidInputError,
    InvalidLlmOutputError,
    LlmClientError,
)
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.common.values import BundleId, RunId
from self_improving_code_review.security.models import SecretFinding

__all__ = ["AgentLlmCall", "AgentLoopOutcome", "ReviewAgentLoop"]

_UNREACHABLE_AGENT_LOOP = "review agent loop must return or raise"


@dataclass(frozen=True, slots=True)
class AgentLlmCall:
    """レビュー担当ループ内の 1 回の LLM 呼出監査情報."""

    turn_index: int
    request_hash: str
    model: str
    usage: LlmUsage
    attempt: int


@dataclass(frozen=True, slots=True)
class AgentLoopOutcome:
    """最終レビュー応答または機密情報遮断結果."""

    result: LlmResult | None
    llm_calls: tuple[AgentLlmCall, ...]
    tool_outcomes: tuple[ContextToolOutcome, ...]
    secret_findings: tuple[SecretFinding, ...]
    tool_call_count: int
    tool_success_count: int
    dynamic_context_tokens: int

    @property
    def is_blocked(self) -> bool:
        """機密情報によって停止した場合は True."""
        return bool(self.secret_findings)


class ReviewAgentLoop:
    """1 ターン 1 ツールの制約で最終候補まで処理する."""

    def __init__(self, llm_client: LlmClient, *, max_findings: int) -> None:
        """LLM ポートと最終指摘件数上限を保持する."""
        self._llm_client = llm_client
        self._max_findings = max_findings

    def execute(
        self,
        initial_request: LlmRequest,
        *,
        coordinator: ContextToolCoordinator,
        session: ContextToolSession,
        run_id: RunId,
        bundle_id: BundleId,
        source_revision: str | None,
        should_stop: Callable[[], bool] | None = None,
    ) -> AgentLoopOutcome:
        """ツール要求を調停し、最終レビュー応答または中断結果を返す."""
        current_request = initial_request
        total_usage = LlmUsage(0, 0, 0)
        total_attempts = 0
        llm_calls: list[AgentLlmCall] = []
        tool_outcomes: list[ContextToolOutcome] = []
        turn_index = 1
        is_stopped = should_stop or (lambda: False)
        while True:
            if is_stopped():
                return AgentLoopOutcome(
                    result=None,
                    llm_calls=tuple(llm_calls),
                    tool_outcomes=tuple(tool_outcomes),
                    secret_findings=(),
                    tool_call_count=session.call_count,
                    tool_success_count=session.success_count,
                    dynamic_context_tokens=session.consumed_tokens,
                )
            allow_tool_request = session.has_remaining_call
            current_request = replace(
                current_request,
                output_schema=build_agent_turn_schema(
                    max_findings=self._max_findings,
                    allow_tool_request=allow_tool_request,
                ),
            )
            turn, result, calls = self._generate_turn_with_repair(
                current_request,
                allow_tool_request=allow_tool_request,
                starting_turn_index=turn_index,
            )
            llm_calls.extend(calls)
            turn_index += len(calls)
            total_usage = _add_usage(total_usage, result.usage)
            total_attempts += result.attempt
            if turn.action == "final":
                if turn.final_payload is None:
                    raise AssertionError(_UNREACHABLE_AGENT_LOOP)
                final_result = replace(
                    result,
                    payload=turn.final_payload,
                    usage=total_usage,
                    attempt=total_attempts,
                )
                return AgentLoopOutcome(
                    result=final_result,
                    llm_calls=tuple(llm_calls),
                    tool_outcomes=tuple(tool_outcomes),
                    secret_findings=(),
                    tool_call_count=session.call_count,
                    tool_success_count=session.success_count,
                    dynamic_context_tokens=session.consumed_tokens,
                )
            if turn.tool_intent is None:
                raise AssertionError(_UNREACHABLE_AGENT_LOOP)
            try:
                request = coordinator.validate(
                    turn.tool_intent,
                    run_id=run_id,
                    bundle_id=bundle_id,
                    source_revision=source_revision,
                )
            except InvalidInputError:
                tool_outcome = session.reject_invalid(turn.tool_intent)
            else:
                tool_outcome = session.execute(request)
            tool_outcomes.append(tool_outcome)
            if tool_outcome.is_blocked:
                return AgentLoopOutcome(
                    result=None,
                    llm_calls=tuple(llm_calls),
                    tool_outcomes=tuple(tool_outcomes),
                    secret_findings=tool_outcome.secret_findings,
                    tool_call_count=session.call_count,
                    tool_success_count=session.success_count,
                    dynamic_context_tokens=session.consumed_tokens,
                )
            current_request = _append_tool_outcome(
                initial_request,
                current_request,
                tool_outcome,
                allow_tool_request=session.has_remaining_call,
                max_findings=self._max_findings,
            )

    def _generate_turn_with_repair(
        self,
        request: LlmRequest,
        *,
        allow_tool_request: bool,
        starting_turn_index: int,
    ) -> tuple[AgentTurn, LlmResult, tuple[AgentLlmCall, ...]]:
        """1 ターンを生成し、不正形式を 1 回だけ修復する."""
        current = request
        usage = LlmUsage(0, 0, 0)
        attempts = 0
        calls: list[AgentLlmCall] = []
        last_model = self._llm_client.model
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
                        str(exc),
                        attempt=attempts,
                        latency_ms=usage.latency_ms,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cost_usd=usage.cost_usd,
                    ) from exc
                current = _repair_request(request)
                continue
            calls.append(
                AgentLlmCall(
                    turn_index=starting_turn_index + len(calls),
                    request_hash=hash_text(
                        current.system_instruction + "\n" + current.user_content
                    ),
                    model=result.model,
                    usage=result.usage,
                    attempt=result.attempt,
                )
            )
            usage = _add_usage(usage, result.usage)
            attempts += result.attempt
            last_model = result.model
            try:
                turn = parse_agent_turn(
                    result.payload,
                    max_findings=self._max_findings,
                    allow_tool_request=allow_tool_request,
                )
            except InvalidLlmOutputError as exc:
                if repair_attempt == 1:
                    raise InvalidLlmOutputError(
                        str(exc),
                        attempt=attempts,
                        latency_ms=usage.latency_ms,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cost_usd=usage.cost_usd,
                    ) from exc
                current = _repair_request(request)
                continue
            return (
                turn,
                LlmResult(result.payload, last_model, usage, attempts),
                tuple(calls),
            )
        raise AssertionError(_UNREACHABLE_AGENT_LOOP)


def _append_tool_outcome(
    initial_request: LlmRequest,
    current_request: LlmRequest,
    outcome: ContextToolOutcome,
    *,
    allow_tool_request: bool,
    max_findings: int,
) -> LlmRequest:
    """安全なツール結果または理由コードを次回要求へ追加する."""
    result_document = json.dumps(
        {
            "status": outcome.status.value,
            "reason_code": outcome.reason_code,
            "result_count": outcome.result_count,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    block = f"\n\n## ツール結果\n{result_document}"
    if outcome.prompt_block is not None:
        block += f"\n{outcome.prompt_block}"
    schema = build_agent_turn_schema(
        max_findings=max_findings,
        allow_tool_request=allow_tool_request,
    )
    block += "\n\n## 次回応答スキーマ\n" + json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return replace(
        current_request,
        system_instruction=initial_request.system_instruction,
        user_content=current_request.user_content + block,
        output_schema=schema,
    )


def _repair_request(request: LlmRequest) -> LlmRequest:
    """不正応答後に JSON だけを要求する一回限りの修復要求を返す."""
    return replace(
        request,
        user_content=(
            request.user_content
            + "\n\n形式修復: 説明文やコードフェンスを付けず、"
            + "応答スキーマに一致する JSON オブジェクトだけを返してください。"
        ),
    )


def _add_usage(left: LlmUsage, right: LlmUsage) -> LlmUsage:
    """2 件の LLM 使用量を合算する."""
    return LlmUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        latency_ms=left.latency_ms + right.latency_ms,
        cost_usd=Decimal(left.cost_usd) + Decimal(right.cost_usd),
    )


def _add_error_usage(left: LlmUsage, error: InvalidLlmOutputError) -> LlmUsage:
    """形式不正応答で消費した安全な使用量を合算する."""
    return _add_usage(
        left,
        LlmUsage(
            error.input_tokens,
            error.output_tokens,
            error.latency_ms,
            error.cost_usd,
        ),
    )
