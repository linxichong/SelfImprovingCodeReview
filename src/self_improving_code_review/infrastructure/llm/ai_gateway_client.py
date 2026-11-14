"""プロジェクトの OpenAI 互換 AI ゲートウェイ向け LiteLLM アダプター."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

import litellm
from litellm.exceptions import (
    APIConnectionError,
    APIError,
    APIResponseValidationError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

from self_improving_code_review.application.ports.llm_client import (
    LlmRequest,
    LlmResult,
    LlmUsage,
)
from self_improving_code_review.domain.common.errors import (
    InvalidInputError,
    InvalidLlmOutputError,
    LlmClientError,
)
from self_improving_code_review.messages import Messages

__all__ = ["AiGatewayLlmClient"]

CompletionFunction = Callable[..., object]

_RETRYABLE_EXCEPTIONS = (
    APIConnectionError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)


class AiGatewayLlmClient:
    """LiteLLM Python SDK を使用し、OpenAI 互換 AI ゲートウェイを呼び出す.

    LiteLLM は OpenAI 互換プロバイダーアダプターだけに制限する。ゲートウェイ
    背後のプロバイダー固有認証情報と SDK 型は、このインフラストラクチャ境界を
    越えない。バックエンド連携とモデル別パラメーターはゲートウェイが所有する。
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: int = 60,
        max_retries: int = 2,
        completion_function: CompletionFunction = litellm.completion,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        """LiteLLM ゲートウェイ設定を検証して保存する."""
        if not api_key.strip():
            message = Messages.API_KEY_REQUIRED
            raise InvalidInputError(message)
        if not model.strip() or model.strip().lower() in {"latest", "unresolved"}:
            message = Messages.MODEL_REQUIRED
            raise InvalidInputError(message)
        if not base_url.lower().startswith("https://"):
            message = Messages.HTTPS_REQUIRED
            raise InvalidInputError(message)
        self._base_url = _openai_base_url(base_url)
        self._model = model.strip()
        self._litellm_model = f"openai/{self._model}"
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._completion = completion_function
        self._sleeper = sleeper

    @property
    def model(self) -> str:
        """プロバイダー接頭辞を除いた固定済みゲートウェイモデル識別子を返す."""
        return self._model

    def close(self) -> None:
        """従来のクライアントライフサイクル API を維持する.

        ここでは LiteLLM がローカルクライアントを保持しない.
        """

    def generate_structured(self, request: LlmRequest) -> LlmResult:
        """構造化出力を返し、LiteLLM の一時的な障害だけを再試行する.

        Args:
            request: 信頼済み指示、未信頼データ、出力上限を含む要求。

        Returns:
            単一 JSON オブジェクトと、全試行を含む使用量測定値。

        Raises:
            LlmClientError: 通信、一時障害、認証、要求処理に失敗した場合。
            InvalidLlmOutputError: 応答を単一 JSON オブジェクトへ変換できない場合。
        """
        operation_started = time.perf_counter()
        attempt = 0
        while True:
            attempt += 1
            try:
                completion_arguments: dict[str, Any] = {
                    "model": self._litellm_model,
                    "messages": [
                        {"role": "system", "content": request.system_instruction},
                        {"role": "user", "content": request.user_content},
                    ],
                    "base_url": self._base_url,
                    "api_key": self._api_key,
                    "timeout": float(self._timeout_seconds),
                    "num_retries": 0,
                    "max_tokens": request.max_output_tokens,
                }
                if request.temperature is not None:
                    completion_arguments["temperature"] = request.temperature
                response = self._completion(
                    **completion_arguments,
                )
            except _RETRYABLE_EXCEPTIONS as exc:
                if attempt > self._max_retries:
                    message = Messages.litellm_transient_gateway_failure(type(exc).__name__)
                    raise LlmClientError(
                        message,
                        is_retryable=True,
                        attempt=attempt,
                        latency_ms=_elapsed_ms(operation_started),
                    ) from exc
                self._sleeper(float(2 ** (attempt - 1)))
                continue
            except APIResponseValidationError as exc:
                message = Messages.LITELLM_RESPONSE_VALIDATION_FAILED
                raise InvalidLlmOutputError(
                    message,
                    attempt=attempt,
                    latency_ms=_elapsed_ms(operation_started),
                ) from exc
            except (APIError, AuthenticationError, BadRequestError) as exc:
                message = Messages.litellm_gateway_request_failed(type(exc).__name__)
                raise LlmClientError(
                    message,
                    attempt=attempt,
                    latency_ms=_elapsed_ms(operation_started),
                ) from exc

            latency_ms = _elapsed_ms(operation_started)
            document: dict[str, Any] | None = None
            try:
                document = _response_document(response)
                return self._parse_response(
                    document,
                    response=response,
                    latency_ms=latency_ms,
                    attempt=attempt,
                )
            except InvalidLlmOutputError as exc:
                usage = _response_usage(response, document or {}, latency_ms)
                raise InvalidLlmOutputError(
                    str(exc),
                    attempt=attempt,
                    latency_ms=usage.latency_ms,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost_usd=usage.cost_usd,
                ) from exc

    def _parse_response(
        self,
        document: dict[str, Any],
        *,
        response: object,
        latency_ms: int,
        attempt: int,
    ) -> LlmResult:
        """LiteLLM 応答をプロバイダー非依存のアプリケーション型へ変換する."""
        try:
            choices = document["choices"]
            message = choices[0]["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            error_message = Messages.LITELLM_RESPONSE_CONTENT_MISSING
            raise InvalidLlmOutputError(error_message) from exc
        if not isinstance(content, str):
            error_message = Messages.LITELLM_RESPONSE_CONTENT_NOT_JSON_STRING
            raise InvalidLlmOutputError(error_message)
        payload = _decode_json_object(content)

        usage = _response_usage(response, document, latency_ms)
        response_model = document.get("model")
        return LlmResult(
            payload=payload,
            model=response_model if isinstance(response_model, str) else self._model,
            usage=usage,
            attempt=attempt,
        )


def _openai_base_url(base_url: str) -> str:
    """OpenAI チャット補完用に ``/v1`` で終わるベース URL を返す."""
    normalized = base_url.rstrip("/")
    return normalized if normalized.endswith("/v1") else f"{normalized}/v1"


def _decode_json_object(content: str) -> dict[str, Any]:
    """モデル応答から JSON オブジェクトを 1 つだけ抽出する.

    一部の OpenAI 互換 AI ゲートウェイは構造化出力の要求パラメーターに
    対応せず、小規模モデルは有効な JSON を Markdown コードフェンスや短い
    説明で囲む場合がある. アプリケーションは通信層での解析後も、返された
    オブジェクトを閉じたレビュースキーマに照らして検証する.
    """
    stripped = content.strip()
    decoder = json.JSONDecoder()
    for start, character in enumerate(stripped):
        if character not in "{[":
            continue
        try:
            payload, end = decoder.raw_decode(stripped, start)
        except json.JSONDecodeError:
            continue
        remainder = stripped[end:]
        if "{" in remainder or "}" in remainder:
            continue
        if isinstance(payload, dict):
            return payload
    error_message = Messages.LITELLM_RESPONSE_JSON_OBJECT_MISSING
    raise InvalidLlmOutputError(error_message)


def _response_document(response: object) -> dict[str, Any]:
    """LiteLLM ModelResponse またはテスト用マッピングを辞書へ変換する."""
    if isinstance(response, dict):
        return response
    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        document = model_dump()
        if isinstance(document, dict):
            return document
    message = Messages.LITELLM_RESPONSE_NOT_OBJECT
    raise InvalidLlmOutputError(message)


def _elapsed_ms(started: float) -> int:
    """開始時刻からの経過時間をミリ秒で返す."""
    return max(int((time.perf_counter() - started) * 1000), 0)


def _response_usage(
    response: object,
    document: dict[str, Any],
    latency_ms: int,
) -> LlmUsage:
    """LiteLLM 応答から取得可能な使用量を安全に変換する."""
    usage_document = document.get("usage", {})
    usage_mapping = usage_document if isinstance(usage_document, dict) else {}
    return LlmUsage(
        input_tokens=_safe_int(usage_mapping.get("prompt_tokens")),
        output_tokens=_safe_int(usage_mapping.get("completion_tokens")),
        latency_ms=latency_ms,
        cost_usd=_response_cost(response, usage_mapping),
    )


def _response_cost(response: object, usage: dict[str, Any]) -> Decimal:
    """LiteLLM が算出した応答コストを取得できる場合に返す."""
    hidden = getattr(response, "_hidden_params", None)
    if isinstance(hidden, dict) and hidden.get("response_cost") is not None:
        return _safe_decimal(hidden["response_cost"])
    return _safe_decimal(usage.get("cost_usd"))


def _safe_int(value: object) -> int:
    """任意の使用量を非負整数として返す."""
    return value if isinstance(value, int) and value >= 0 else 0


def _safe_decimal(value: object) -> Decimal:
    """任意のコスト値を非負の Decimal として返す."""
    try:
        parsed = Decimal(str(value if value is not None else "0"))
    except InvalidOperation:
        return Decimal("0")
    return parsed if parsed >= 0 else Decimal("0")
