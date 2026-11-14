from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

import pytest

from self_improving_code_review.application.ports.llm_client import LlmRequest
from self_improving_code_review.domain.common.errors import (
    InvalidInputError,
    InvalidLlmOutputError,
)
from self_improving_code_review.infrastructure.llm.ai_gateway_client import AiGatewayLlmClient
from self_improving_code_review.messages import Messages


@pytest.mark.case("CT-M3-006")
@pytest.mark.parametrize(
    ("api_key", "model", "base_url", "expected_message"),
    [
        ("   ", "claude-fixed-v1", "https://gateway", Messages.API_KEY_REQUIRED),
        ("key", "   ", "https://gateway", Messages.MODEL_REQUIRED),
        ("key", "unresolved", "https://gateway", Messages.MODEL_REQUIRED),
        ("key", "LATEST", "https://gateway", Messages.MODEL_REQUIRED),
        ("key", "claude-fixed-v1", "http://gateway", Messages.HTTPS_REQUIRED),
    ],
)
def test_construction_requires_credentials_exact_model_and_https(
    api_key: str,
    model: str,
    base_url: str,
    expected_message: str,
) -> None:
    with pytest.raises(InvalidInputError, match=expected_message):
        AiGatewayLlmClient(base_url=base_url, model=model, api_key=api_key)


@pytest.mark.case("CT-M3-006")
@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("https://gateway", "https://gateway/v1"),
        ("https://gateway/", "https://gateway/v1"),
        ("https://gateway/v1", "https://gateway/v1"),
        ("https://gateway/v1/", "https://gateway/v1"),
    ],
)
def test_base_url_is_normalized_to_openai_chat_completions(
    configured: str,
    expected: str,
) -> None:
    received: list[dict[str, object]] = []

    client = _client(
        _responder({"schema_version": 1, "findings": []}, received), base_url=configured
    )
    client.generate_structured(_request())

    assert received[0]["base_url"] == expected


@pytest.mark.case("CT-M3-006")
def test_model_property_excludes_the_provider_prefix() -> None:
    client = _client(_responder({"schema_version": 1, "findings": []}, []))

    assert client.model == "claude-fixed-v1"
    client.close()


@pytest.mark.case("CT-M3-006")
def test_response_model_is_preferred_over_the_configured_model() -> None:
    def completion(**_kwargs: object) -> object:
        return {
            "model": "claude-gateway-reported",
            "choices": [{"message": {"content": '{"schema_version": 1, "findings": []}'}}],
            "usage": {},
        }

    result = _client(completion).generate_structured(_request())

    assert result.model == "claude-gateway-reported"


@pytest.mark.case("CT-M3-006")
def test_configured_model_is_used_when_the_response_omits_it() -> None:
    def completion(**_kwargs: object) -> object:
        return {
            "choices": [{"message": {"content": '{"schema_version": 1, "findings": []}'}}],
            "usage": {},
        }

    result = _client(completion).generate_structured(_request())

    assert result.model == "claude-fixed-v1"


@pytest.mark.case("CT-M3-006")
@pytest.mark.parametrize(
    "document",
    [
        {"usage": {}},
        {"choices": [], "usage": {}},
        {"choices": [{}], "usage": {}},
        {"choices": "not-a-list", "usage": {}},
    ],
)
def test_missing_message_content_is_rejected(document: dict[str, object]) -> None:
    with pytest.raises(InvalidLlmOutputError, match="メッセージ本文がありません"):
        _client(lambda **_kwargs: document).generate_structured(_request())


@pytest.mark.case("CT-M3-006")
def test_non_string_message_content_is_rejected() -> None:
    document = {"choices": [{"message": {"content": {"findings": []}}}], "usage": {}}

    with pytest.raises(InvalidLlmOutputError, match="JSON 文字列"):
        _client(lambda **_kwargs: document).generate_structured(_request())


@pytest.mark.case("CT-M3-006")
@pytest.mark.parametrize(
    "content",
    [
        "no json here",
        "{not json}",
        '{"a": 1} {',
        "[1, 2, 3]",
        '{"unclosed": ',
    ],
)
def test_content_without_exactly_one_json_object_is_rejected(content: str) -> None:
    document = {"choices": [{"message": {"content": content}}], "usage": {}}

    with pytest.raises(InvalidLlmOutputError, match="JSON オブジェクトがありません"):
        _client(lambda **_kwargs: document).generate_structured(_request())


@pytest.mark.case("CT-M3-006")
def test_trailing_json_object_is_selected_when_it_is_the_only_complete_one() -> None:
    content = '{"schema_version": 0} {"schema_version": 1, "findings": []}'
    document = {"choices": [{"message": {"content": content}}], "usage": {}}

    result = _client(lambda **_kwargs: document).generate_structured(_request())

    assert result.payload == {"schema_version": 1, "findings": []}


@pytest.mark.case("CT-M3-006")
def test_non_object_response_is_rejected() -> None:
    with pytest.raises(InvalidLlmOutputError, match="オブジェクトである必要があります"):
        _client(lambda **_kwargs: "not-a-response").generate_structured(_request())


@pytest.mark.case("CT-M3-006")
def test_model_dump_responses_are_accepted() -> None:
    class ModelResponse:
        def model_dump(self) -> dict[str, object]:
            return {
                "choices": [{"message": {"content": '{"schema_version": 1, "findings": []}'}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 1},
            }

    result = _client(lambda **_kwargs: ModelResponse()).generate_structured(_request())

    assert result.payload == {"schema_version": 1, "findings": []}
    assert result.usage.input_tokens == 4


@pytest.mark.case("CT-M3-006")
def test_model_dump_returning_a_non_mapping_is_rejected() -> None:
    class BadResponse:
        def model_dump(self) -> str:
            return "not-a-mapping"

    with pytest.raises(InvalidLlmOutputError, match="オブジェクトである必要があります"):
        _client(lambda **_kwargs: BadResponse()).generate_structured(_request())


@pytest.mark.case("CT-M3-006")
def test_hidden_response_cost_takes_precedence_over_usage_cost() -> None:
    class ResponseWithCost(dict[str, object]):
        _hidden_params: ClassVar[dict[str, str]] = {"response_cost": "0.25"}

    document = ResponseWithCost(
        {
            "choices": [{"message": {"content": '{"schema_version": 1, "findings": []}'}}],
            "usage": {"cost_usd": "0.01"},
        }
    )

    result = _client(lambda **_kwargs: document).generate_structured(_request())

    assert result.usage.cost_usd == Decimal("0.25")


@pytest.mark.case("CT-M3-006")
@pytest.mark.parametrize(
    ("usage", "expected_input", "expected_output", "expected_cost"),
    [
        ({}, 0, 0, Decimal("0")),
        ({"prompt_tokens": -5, "completion_tokens": "x"}, 0, 0, Decimal("0")),
        ({"cost_usd": "not-a-number"}, 0, 0, Decimal("0")),
        ({"cost_usd": "-1"}, 0, 0, Decimal("0")),
        ({"prompt_tokens": 7, "completion_tokens": 2, "cost_usd": "0.03"}, 7, 2, Decimal("0.03")),
    ],
)
def test_usage_values_are_normalized_to_safe_numbers(
    usage: dict[str, object],
    expected_input: int,
    expected_output: int,
    expected_cost: Decimal,
) -> None:
    document = {
        "choices": [{"message": {"content": '{"schema_version": 1, "findings": []}'}}],
        "usage": usage,
    }

    result = _client(lambda **_kwargs: document).generate_structured(_request())

    assert result.usage.input_tokens == expected_input
    assert result.usage.output_tokens == expected_output
    assert result.usage.cost_usd == expected_cost


@pytest.mark.case("CT-M3-006")
def test_non_mapping_usage_section_is_tolerated() -> None:
    document = {
        "choices": [{"message": {"content": '{"schema_version": 1, "findings": []}'}}],
        "usage": "not-a-mapping",
    }

    result = _client(lambda **_kwargs: document).generate_structured(_request())

    assert result.usage.input_tokens == 0


def _client(
    completion: object, *, base_url: str = "https://aigw.ldjp-ai.link"
) -> AiGatewayLlmClient:
    return AiGatewayLlmClient(
        base_url=base_url,
        model="claude-fixed-v1",
        api_key="test-only",
        completion_function=completion,  # type: ignore[arg-type]
        sleeper=lambda _seconds: None,
    )


def _responder(payload: dict[str, object], received: list[dict[str, object]]) -> object:
    def completion(**kwargs: object) -> object:
        received.append(kwargs)
        import json

        return {
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {},
        }

    return completion


def _request() -> LlmRequest:
    return LlmRequest(
        system_instruction="system",
        user_content="review",
        output_schema={"type": "object"},
        max_output_tokens=256,
    )
