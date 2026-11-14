"""集中管理されたメッセージの単体テスト."""

from pathlib import Path

import pytest

from self_improving_code_review.messages import Messages


@pytest.mark.case("UT-M1-015", "UT-M2-024")
def test_fixed_messages_are_japanese() -> None:
    assert Messages.API_KEY_REQUIRED == "AI ゲートウェイの認証情報が設定されていません。"
    assert Messages.MODEL_REQUIRED == "AI ゲートウェイで使用する固定モデル ID を指定してください。"
    assert Messages.HTTPS_REQUIRED == ("AI ゲートウェイの URL には HTTPS を指定してください。")


@pytest.mark.case("UT-M1-015", "UT-M2-024")
def test_dynamic_messages_include_only_safe_context() -> None:
    assert Messages.file_not_found("config/review.yaml") == (
        "対象ファイルが見つかりません: config/review.yaml"
    )
    assert Messages.llm_request_failed("Timeout") == ("LLM の呼び出しに失敗しました: Timeout")
    assert Messages.litellm_transient_gateway_failure("RateLimitError") == (
        "LiteLLM の一時的なゲートウェイ障害です: RateLimitError"
    )
    assert Messages.litellm_gateway_request_failed("AuthenticationError") == (
        "LiteLLM のゲートウェイ要求に失敗しました: AuthenticationError"
    )


@pytest.mark.case("UT-M1-015", "UT-M2-024")
def test_dataset_and_path_messages_reference_only_locations() -> None:
    dataset = Path("datasets/test/review_cases.json")

    assert Messages.invalid_llm_dataset(dataset) == (
        f"LLM データセットを読み込めないか、形式が不正です: {dataset}"
    )
    assert Messages.duplicate_dataset_case_id(dataset) == (
        f"LLM データセットの case_id が重複しています: {dataset}"
    )
    assert Messages.configured_path_outside_repository("データベースのパス") == (
        "データベースのパスはリポジトリ内を指定してください。"
    )
