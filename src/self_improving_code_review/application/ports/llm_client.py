"""LLM クライアントポート.

ゲートウェイや SDK の変更がドメイン層やアプリケーション層へ波及しないよう、
このポートは意図的にプロバイダー非依存の型だけを公開する.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

__all__ = ["LlmClient", "LlmRequest", "LlmResult", "LlmUsage"]


@dataclass(frozen=True, slots=True)
class LlmRequest:
    """単一の構造化出力要求.

    Attributes:
        system_instruction: 信頼済みの命令ブロック.
        user_content: 区切られた未信頼データを含むプロンプト本文.
        output_schema: 応答が満たすべき JSON スキーマ.
        max_output_tokens: 生成トークン数の上限.
        temperature: 明示する場合のサンプリング温度。None はモデル既定値を使用する.
        request_label: ログに使用する短いラベル. プロンプト自体は使用しない.
    """

    system_instruction: str
    user_content: str
    output_schema: dict[str, Any]
    max_output_tokens: int
    temperature: float | None = None
    request_label: str = "review"


@dataclass(frozen=True, slots=True)
class LlmUsage:
    """1 回の呼び出しにおけるトークン数、遅延、コストの測定値."""

    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))


@dataclass(frozen=True, slots=True)
class LlmResult:
    """検証済みの単一呼び出し構造化応答."""

    payload: dict[str, Any]
    model: str
    usage: LlmUsage
    attempt: int


class LlmClient(Protocol):
    """LLM を呼び出し、スキーマ検証済みの構造化データを返す."""

    @property
    def model(self) -> str:
        """固定済みのモデル識別子を返す."""
        ...

    def generate_structured(self, request: LlmRequest) -> LlmResult:
        """``request`` に対する構造化応答を返す.

        Raises:
            LlmClientError: 通信が失敗した場合.
            InvalidLlmOutputError: 応答が要求したスキーマに違反する場合.
        """
        ...

    def close(self) -> None:
        """クライアントが保持する外部資源を解放する."""
        ...
