"""ローカルデータセット複数ターンプロトコルの契約テスト."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from self_improving_code_review.application.ports.llm_client import LlmRequest
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.infrastructure.llm.dataset_client import DatasetLlmClient


@pytest.mark.case("CT-M3-005")
def test_multiturn_case_consumes_review_and_reflection_responses(tmp_path: Path) -> None:
    first = _request("CASE_A first", "review-agent:B001")
    second = _request("CASE_A second", "review-agent:B001")
    reflection = _request("CASE_A reflection", "reflection:B001")
    dataset = _write_dataset(
        tmp_path,
        [
            _response(first, "B001", "review-agent", 1, "tool-request"),
            _response(second, "B001", "review-agent", 2, "final"),
            _response(reflection, "B001", "reflection", 1, "accepted"),
        ],
    )
    client = DatasetLlmClient(dataset)

    assert client.generate_structured(first).payload == {"value": "tool-request"}
    assert client.generate_structured(second).payload == {"value": "final"}
    assert client.generate_structured(reflection).payload == {"value": "accepted"}
    client.close()
    client.close()


@pytest.mark.case("CT-M3-005")
def test_multiturn_case_uses_default_when_no_case_matches(tmp_path: Path) -> None:
    request = _request("OTHER", "review-agent:B001")
    dataset = _write_dataset(
        tmp_path,
        [_response(_request("CASE_A", "review-agent:B001"), "B001", "review-agent", 1, "x")],
    )
    client = DatasetLlmClient(dataset)

    result = client.generate_structured(request)

    assert result.payload == {"value": "default"}
    client.close()


@pytest.mark.case("CT-M3-005")
def test_multiturn_case_rejects_invalid_request_label(tmp_path: Path) -> None:
    request = _request("CASE_A", "review-agent")
    dataset = _write_dataset(
        tmp_path,
        [_response(request, "B001", "review-agent", 1, "x")],
    )
    client = DatasetLlmClient(dataset)

    with pytest.raises(InvalidInputError, match="多轮请求标签无效"):
        client.generate_structured(request)


@pytest.mark.case("CT-M3-005")
def test_multiturn_case_rejects_missing_response(tmp_path: Path) -> None:
    request = _request("CASE_A", "review-agent:B002")
    dataset = _write_dataset(
        tmp_path,
        [_response(request, "B001", "review-agent", 1, "x")],
    )
    client = DatasetLlmClient(dataset)

    with pytest.raises(InvalidInputError, match="缺少与请求匹配的多轮响应"):
        client.generate_structured(request)


@pytest.mark.case("CT-M3-005")
def test_multiturn_case_rejects_request_hash_mismatch(tmp_path: Path) -> None:
    request = _request("CASE_A", "review-agent:B001")
    response = _response(request, "B001", "review-agent", 1, "x")
    response["request_hash"] = "0" * 64
    dataset = _write_dataset(tmp_path, [response])
    client = DatasetLlmClient(dataset)

    with pytest.raises(InvalidInputError, match="请求哈希不一致"):
        client.generate_structured(request)


@pytest.mark.case("CT-M3-005")
def test_multiturn_case_detects_unused_response_on_close(tmp_path: Path) -> None:
    first = _request("CASE_A first", "review-agent:B001")
    second = _request("CASE_A second", "review-agent:B001")
    dataset = _write_dataset(
        tmp_path,
        [
            _response(first, "B001", "review-agent", 1, "first"),
            _response(second, "B001", "review-agent", 2, "second"),
        ],
    )
    client = DatasetLlmClient(dataset)
    client.generate_structured(first)

    with pytest.raises(InvalidInputError, match="未使用的响应"):
        client.close()

    client.close()


@pytest.mark.case("CT-M3-005")
def test_multiturn_case_rejects_duplicate_response_key(tmp_path: Path) -> None:
    request = _request("CASE_A", "review-agent:B001")
    response = _response(request, "B001", "review-agent", 1, "x")
    dataset = _write_dataset(tmp_path, [response, response])

    with pytest.raises(InvalidInputError, match="多轮响应键重复"):
        DatasetLlmClient(dataset)


@pytest.mark.case("CT-M3-005")
def test_multiturn_client_rejects_request_after_close(tmp_path: Path) -> None:
    request = _request("OTHER", "review-agent:B001")
    dataset = _write_dataset(
        tmp_path,
        [_response(_request("CASE_A", "review-agent:B001"), "B001", "review-agent", 1, "x")],
    )
    client = DatasetLlmClient(dataset)
    client.close()

    with pytest.raises(InvalidInputError, match="LLM 数据集已关闭"):
        client.generate_structured(request)


def _request(content: str, label: str) -> LlmRequest:
    """固定ハッシュを計算できる LLM 要求を作成する."""
    return LlmRequest("system", content, {"type": "object"}, 128, request_label=label)


def _response(
    request: LlmRequest,
    review_unit_id: str,
    call_kind: str,
    turn_number: int,
    value: str,
) -> dict[str, object]:
    """要求と一致する複数ターン応答を作成する."""
    request_text = f"{request.system_instruction}\n{request.user_content}"
    return {
        "review_unit_id": review_unit_id,
        "call_kind": call_kind,
        "turn_number": turn_number,
        "request_hash": hash_text(request_text),
        "result": {
            "payload": {"value": value},
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "latency_ms": 1,
                "cost_usd": "0",
            },
        },
    }


def _write_dataset(tmp_path: Path, responses: list[dict[str, object]]) -> Path:
    """複数ターンデータセットを一時ファイルへ保存する."""
    path = tmp_path / "multiturn.json"
    document = {
        "schema_version": 2,
        "model": "local-multiturn-v2",
        "default_result": {
            "payload": {"value": "default"},
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "latency_ms": 0,
                "cost_usd": "0",
            },
        },
        "cases": [{"case_id": "case-a", "match_all": ["CASE_A"], "responses": responses}],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path
