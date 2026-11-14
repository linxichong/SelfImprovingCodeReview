"""ドメインおよびインフラストラクチャの例外階層.

この階層は開発標準が要求する 2 つの基底構造を反映する.
回復可能なルール違反は :class:`DomainError` から、
アダプター障害は :class:`InfrastructureError` から派生する.
"""

from decimal import Decimal

__all__ = [
    "AuthorizationError",
    "ConcurrencyConflictError",
    "DatabaseError",
    "DomainError",
    "GitCommandError",
    "InfrastructureError",
    "InvalidDiffError",
    "InvalidFindingError",
    "InvalidInputError",
    "InvalidLlmOutputError",
    "InvalidStateTransitionError",
    "LlmClientError",
    "PolicyViolationError",
    "ReflectionBudgetExceededError",
    "ScmClientError",
    "TokenBudgetExceededError",
    "ToolExecutionError",
]


class DomainError(Exception):
    """ドメインルールまたは入力契約違反の基底クラス."""


class InvalidInputError(DomainError):
    """呼び出し元が指定した値が入力契約に違反した場合に送出する."""


class InvalidDiffError(DomainError):
    """統一差分を解析できない場合、または内部整合性がない場合に送出する."""


class InvalidFindingError(DomainError):
    """指摘候補をドメイン指摘へ変換できない場合に送出する."""


class TokenBudgetExceededError(DomainError):
    """送信内容を設定済みトークン予算内へ縮小できない場合に送出する."""


class ReflectionBudgetExceededError(TokenBudgetExceededError):
    """自己検証の入力が ``reflection_tokens`` を超える場合に送出する."""


class PolicyViolationError(DomainError):
    """セキュリティ方針が後続処理を遮断した場合に送出する.

    機密情報検出ではこの例外を使用し、アダプター内部を調べずに
    プロセス境界で遮断終了コードへ対応付ける.
    """


class AuthorizationError(DomainError):
    """実行者を特定できない、または権限が不足する場合に送出する.

    非対話式実行でも承認者を特定できない限り状態遷移を
    成功させてはならない.
    """


class InvalidStateTransitionError(DomainError):
    """許容されない状態遷移を要求された場合に送出する."""


class ConcurrencyConflictError(DomainError):
    """楽観ロックが同じバージョンの二重更新を検出した場合に送出する."""


class InfrastructureError(Exception):
    """外向きアダプターが送出する障害の基底クラス."""


class _LlmCallError(InfrastructureError):
    """失敗した LLM 呼び出しの安全な測定値を保持する."""

    def __init__(
        self,
        message: str,
        *,
        attempt: int = 1,
        latency_ms: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: Decimal = Decimal("0"),
    ) -> None:
        """機密情報を含まない診断情報と呼び出し測定値を保存する."""
        super().__init__(message)
        self.attempt = max(attempt, 1)
        self.latency_ms = max(latency_ms, 0)
        self.input_tokens = max(input_tokens, 0)
        self.output_tokens = max(output_tokens, 0)
        self.cost_usd = max(cost_usd, Decimal("0"))


class LlmClientError(_LlmCallError):
    """LLM 呼び出しが通信層またはプロトコル層で失敗した場合に送出する."""

    def __init__(
        self,
        message: str,
        *,
        is_retryable: bool = False,
        attempt: int = 1,
        latency_ms: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: Decimal = Decimal("0"),
    ) -> None:
        """呼び出し元が再試行可否と失敗時の測定値を取得できるようにする."""
        super().__init__(
            message,
            attempt=attempt,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )
        self.is_retryable = is_retryable


class InvalidLlmOutputError(_LlmCallError):
    """LLM 応答が要求した出力スキーマを満たさない場合に送出する."""


class GitCommandError(InfrastructureError):
    """Git コマンドがゼロ以外の状態で終了した場合に送出する."""


class DatabaseError(InfrastructureError):
    """永続化処理が失敗した場合に送出する."""


class ToolExecutionError(InfrastructureError):
    """コンテキスト取得ツールが結果を生成できない場合に送出する."""


class ScmClientError(InfrastructureError):
    """SCM API の通信または応答契約違反を安全な情報だけで表す."""

    def __init__(
        self,
        message: str,
        *,
        is_retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        """再試行可否と任意の HTTP 状態コードを保存する."""
        super().__init__(message)
        self.is_retryable = is_retryable
        self.status_code = status_code
