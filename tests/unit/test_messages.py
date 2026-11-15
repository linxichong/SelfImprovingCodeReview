"""集中管理されたメッセージの単体テスト."""

from pathlib import Path

import pytest

from self_improving_code_review.messages import Messages


@pytest.mark.case("UT-M1-015", "UT-M2-024")
def test_fixed_messages_are_japanese() -> None:
    assert Messages.API_KEY_REQUIRED == "未配置 AI 网关凭据。"
    assert Messages.MODEL_REQUIRED == "请指定 AI 网关使用的固定模型 ID。"
    assert Messages.HTTPS_REQUIRED == ("AI 网关 URL 必须使用 HTTPS。")


@pytest.mark.case("UT-M1-015", "UT-M2-024")
def test_dynamic_messages_include_only_safe_context() -> None:
    assert Messages.file_not_found("config/review.yaml") == ("找不到目标文件: config/review.yaml")
    assert Messages.llm_request_failed("Timeout") == ("LLM 调用失败: Timeout")
    assert Messages.litellm_transient_gateway_failure("RateLimitError") == (
        "LiteLLM 网关出现临时故障: RateLimitError"
    )
    assert Messages.litellm_gateway_request_failed("AuthenticationError") == (
        "LiteLLM 网关请求失败: AuthenticationError"
    )


@pytest.mark.case("UT-M1-015", "UT-M2-024")
def test_dataset_and_path_messages_reference_only_locations() -> None:
    dataset = Path("datasets/test/review_cases.json")

    assert Messages.invalid_llm_dataset(dataset) == (
        f"无法读取 LLM 数据集，或数据集格式无效: {dataset}"
    )
    assert Messages.duplicate_dataset_case_id(dataset) == (f"LLM 数据集的 case_id 重复: {dataset}")
    assert Messages.configured_path_outside_repository("数据库路径") == (
        "数据库路径必须位于仓库内。"
    )
