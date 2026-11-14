"""限定コンテキストツールの信頼境界モデルとセッション予算."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic
from typing import Protocol

from self_improving_code_review.application.ports.context_source import ContextSource
from self_improving_code_review.application.ports.secret_scanner import SecretScanner
from self_improving_code_review.domain.common.enums import ToolStatus
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_lines
from self_improving_code_review.domain.common.tokens import estimate_tokens
from self_improving_code_review.domain.common.values import BundleId, NormalizedPath, RunId
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.models import (
    SecretFinding,
    SourceKind,
    SourcePayload,
)
from self_improving_code_review.security.path_policy import PathPolicy
from self_improving_code_review.security.prompt_boundary import encode_untrusted_block

__all__ = [
    "ContextFragment",
    "ContextToolCoordinator",
    "ContextToolOutcome",
    "ContextToolSession",
    "ToolIntent",
    "ToolResult",
    "ValidatedToolRequest",
]

_TOOL_NAMES = frozenset({"read_file", "get_changed_file", "find_symbol", "search_code"})
_MAX_QUERY_LENGTH = 256
_MAX_PATH_LENGTH = 512
_MAX_SCOPE_COUNT = 10
_MAX_READ_LINES = 200


@dataclass(frozen=True, slots=True)
class ToolIntent:
    """LLM から受け取った未信頼なツール意図."""

    tool_name: str
    path: str | None = None
    path_scope: tuple[str, ...] = ()
    query: str | None = None
    start_line: int | None = None
    end_line: int | None = None


@dataclass(frozen=True, slots=True)
class ValidatedToolRequest:
    """調停処理が信頼済み値を追加したツール要求."""

    run_id: RunId
    bundle_id: BundleId
    repository_root: Path
    source_revision: str | None
    tool_name: str
    path: NormalizedPath | None
    path_scope: tuple[NormalizedPath, ...]
    query: str | None
    start_line: int | None
    end_line: int | None
    request_hash: str


@dataclass(frozen=True, slots=True)
class ContextFragment:
    """コード入力元に含まれる連続したテキスト断片."""

    path: NormalizedPath
    start_line: int
    end_line: int
    text: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    """ツール実装から返す未検査結果."""

    status: ToolStatus
    fragments: tuple[ContextFragment, ...] = ()
    reason_code: str = "SUCCESS"


@dataclass(frozen=True, slots=True)
class ContextToolOutcome:
    """予算と機密情報検査を適用したツール結果."""

    status: ToolStatus
    tool_name: str
    request_hash: str
    prompt_block: str | None
    consumed_tokens: int
    result_count: int
    reason_code: str
    secret_findings: tuple[SecretFinding, ...] = ()
    latency_ms: int = 0

    @property
    def is_blocked(self) -> bool:
        """機密情報によって全体を遮断すべき場合は True."""
        return self.status is ToolStatus.BLOCKED


class ContextToolCoordinator:
    """LLM の意図をプロジェクト内部の安全な要求へ変換する."""

    def __init__(self, repository_root: Path, path_policy: PathPolicy) -> None:
        """固定したリポジトリルートとパス方針を保持する."""
        self._repository_root = repository_root.resolve()
        self._path_policy = path_policy

    def validate(
        self,
        intent: ToolIntent,
        *,
        run_id: RunId,
        bundle_id: BundleId,
        source_revision: str | None,
    ) -> ValidatedToolRequest:
        """未信頼意図を検証し、実行可能な要求へ変換する."""
        if intent.tool_name not in _TOOL_NAMES:
            raise InvalidInputError(Messages.CONTEXT_TOOL_INTENT_INVALID)
        path = self._path(intent.path) if intent.path is not None else None
        if len(intent.path_scope) > _MAX_SCOPE_COUNT:
            raise InvalidInputError(Messages.CONTEXT_TOOL_INTENT_INVALID)
        path_scope = tuple(self._path(item) for item in intent.path_scope)
        query = intent.query
        if query is not None and (not query or len(query) > _MAX_QUERY_LENGTH or "\x00" in query):
            raise InvalidInputError(Messages.CONTEXT_TOOL_INTENT_INVALID)
        self._validate_arguments(intent, path, query)
        request_hash = hash_lines(
            (
                intent.tool_name,
                path.value if path is not None else "",
                *(item.value for item in path_scope),
                query or "",
                str(intent.start_line or ""),
                str(intent.end_line or ""),
                source_revision or "workspace",
            )
        )
        return ValidatedToolRequest(
            run_id=run_id,
            bundle_id=bundle_id,
            repository_root=self._repository_root,
            source_revision=source_revision,
            tool_name=intent.tool_name,
            path=path,
            path_scope=path_scope,
            query=query,
            start_line=intent.start_line,
            end_line=intent.end_line,
            request_hash=request_hash,
        )

    def _path(self, value: str) -> NormalizedPath:
        """文字列を方針検証済みパスへ変換する."""
        if not value or len(value) > _MAX_PATH_LENGTH or any(char in value for char in "*?[]"):
            raise InvalidInputError(Messages.CONTEXT_TOOL_INTENT_INVALID)
        try:
            path = NormalizedPath(value)
        except InvalidInputError as exc:
            raise InvalidInputError(Messages.CONTEXT_TOOL_INTENT_INVALID) from exc
        if self._path_policy.is_blocked(path):
            raise InvalidInputError(Messages.CONTEXT_TOOL_INTENT_INVALID)
        resolved = (self._repository_root / path.value).resolve()
        if not resolved.is_relative_to(self._repository_root):
            raise InvalidInputError(Messages.CONTEXT_TOOL_INTENT_INVALID)
        return path

    def _validate_arguments(
        self,
        intent: ToolIntent,
        path: NormalizedPath | None,
        query: str | None,
    ) -> None:
        """ツール別の必須・禁止引数を検証する."""
        if intent.tool_name in {"read_file", "get_changed_file"} and path is None:
            raise InvalidInputError(Messages.CONTEXT_TOOL_INTENT_INVALID)
        if intent.tool_name in {"find_symbol", "search_code"} and query is None:
            raise InvalidInputError(Messages.CONTEXT_TOOL_INTENT_INVALID)
        if intent.tool_name == "read_file":
            start = intent.start_line
            end = intent.end_line
            if start is None or end is None or start < 1 or end < start:
                raise InvalidInputError(Messages.CONTEXT_TOOL_INTENT_INVALID)
            if end - start + 1 > _MAX_READ_LINES:
                raise InvalidInputError(Messages.CONTEXT_TOOL_INTENT_INVALID)
        elif intent.start_line is not None or intent.end_line is not None:
            raise InvalidInputError(Messages.CONTEXT_TOOL_INTENT_INVALID)


class ContextToolSession:
    """レビュー単位内のツール回数、重複、トークン予算を管理する."""

    def __init__(
        self,
        *,
        source: ContextSource,
        executor: ContextToolExecutor,
        secret_scanner: SecretScanner,
        max_calls: int,
        token_budget: int,
        timer: Callable[[], float] = monotonic,
    ) -> None:
        """固定済み依存と会話単位の予算を保持する."""
        self._source = source
        self._executor = executor
        self._secret_scanner = secret_scanner
        self._remaining_calls = max_calls
        self._remaining_tokens = token_budget
        self._initial_calls = max_calls
        self._successful_calls = 0
        self._consumed_tokens = 0
        self._request_hashes: set[str] = set()
        self._timer = timer

    @property
    def has_remaining_call(self) -> bool:
        """次の要求枠が残っている場合は True."""
        return self._remaining_calls > 0

    @property
    def call_count(self) -> int:
        """消費済みの呼出枠数を返す."""
        return self._initial_calls - self._remaining_calls

    @property
    def success_count(self) -> int:
        """安全な本文を利用できた呼出数を返す."""
        return self._successful_calls

    @property
    def consumed_tokens(self) -> int:
        """プロンプトへ追加した動的コンテキストのトークン数を返す."""
        return self._consumed_tokens

    def reject_invalid(self, intent: ToolIntent) -> ContextToolOutcome:
        """意味検証に失敗した要求の呼出枠を消費する."""
        request_hash = hash_lines(
            (
                intent.tool_name,
                intent.path or "",
                *intent.path_scope,
                intent.query or "",
                str(intent.start_line or ""),
                str(intent.end_line or ""),
            )
        )
        if self._remaining_calls <= 0:
            return ContextToolOutcome(
                ToolStatus.BUDGET_EXCEEDED,
                intent.tool_name,
                request_hash,
                None,
                0,
                0,
                "CALL_BUDGET_EXCEEDED",
            )
        self._remaining_calls -= 1
        return ContextToolOutcome(
            ToolStatus.INVALID_REQUEST,
            intent.tool_name,
            request_hash,
            None,
            0,
            0,
            "INVALID_REQUEST",
        )

    def execute(self, request: ValidatedToolRequest) -> ContextToolOutcome:
        """呼出枠を消費し、安全に送信できる結果だけを返す."""
        started_at = self._timer()

        def measured(outcome: ContextToolOutcome) -> ContextToolOutcome:
            """ツール処理時間を負数にならないミリ秒値で付与する."""
            elapsed_ms = max(0, round((self._timer() - started_at) * 1000))
            return replace(outcome, latency_ms=elapsed_ms)

        if self._remaining_calls <= 0:
            return measured(
                _empty_outcome(request, ToolStatus.BUDGET_EXCEEDED, "CALL_BUDGET_EXCEEDED")
            )
        self._remaining_calls -= 1
        if request.request_hash in self._request_hashes:
            return measured(
                _empty_outcome(request, ToolStatus.REPEATED_REQUEST, "REPEATED_REQUEST")
            )
        self._request_hashes.add(request.request_hash)
        try:
            result = self._executor.execute(request, self._source)
        except InvalidInputError, OSError, UnicodeDecodeError:
            return measured(_empty_outcome(request, ToolStatus.FAILED, "TOOL_FAILED"))
        if result.status is not ToolStatus.SUCCESS:
            return measured(_empty_outcome(request, result.status, result.reason_code))
        fragments = tuple(
            sorted(
                result.fragments,
                key=lambda item: (item.path.value, item.start_line, item.end_line),
            )
        )
        selected: list[ContextFragment] = []
        used_tokens = 0
        for fragment in fragments:
            fragment_tokens = estimate_tokens(fragment.text)
            if used_tokens + fragment_tokens > self._remaining_tokens:
                break
            selected.append(fragment)
            used_tokens += fragment_tokens
        if not selected and fragments:
            return measured(
                _empty_outcome(request, ToolStatus.BUDGET_EXCEEDED, "TOKEN_BUDGET_EXCEEDED")
            )
        rendered = _render_fragments(tuple(selected))
        scan_result, sealed = self._secret_scanner.scan_and_seal(
            SourcePayload(
                kind=SourceKind.CONTEXT_TOOL,
                label=f"context-tool:{request.tool_name}",
                text=rendered,
            )
        )
        if sealed is None:
            return measured(
                ContextToolOutcome(
                    status=ToolStatus.BLOCKED,
                    tool_name=request.tool_name,
                    request_hash=request.request_hash,
                    prompt_block=None,
                    consumed_tokens=0,
                    result_count=0,
                    reason_code="SECRET_DETECTED",
                    secret_findings=scan_result.findings,
                )
            )
        self._remaining_tokens -= used_tokens
        self._successful_calls += 1
        self._consumed_tokens += used_tokens
        return measured(
            ContextToolOutcome(
                status=ToolStatus.SUCCESS,
                tool_name=request.tool_name,
                request_hash=request.request_hash,
                prompt_block=encode_untrusted_block("context-tool-result", sealed.text),
                consumed_tokens=used_tokens,
                result_count=len(selected),
                reason_code="SUCCESS",
            )
        )


class ContextToolExecutor(Protocol):
    """具体的な限定ツールを実行するポート."""

    def execute(
        self,
        request: ValidatedToolRequest,
        source: ContextSource,
    ) -> ToolResult:
        """要求を実行して未検査の結果を返す."""
        ...


def _empty_outcome(
    request: ValidatedToolRequest,
    status: ToolStatus,
    reason_code: str,
) -> ContextToolOutcome:
    """本文を持たない結果を返す."""
    return ContextToolOutcome(
        status=status,
        tool_name=request.tool_name,
        request_hash=request.request_hash,
        prompt_block=None,
        consumed_tokens=0,
        result_count=0,
        reason_code=reason_code,
    )


def _render_fragments(fragments: tuple[ContextFragment, ...]) -> str:
    """断片を安定形式へ直列化する."""
    blocks = [
        (
            f"file={fragment.path.value};lines={fragment.start_line}-{fragment.end_line}\n"
            f"{fragment.text}"
        )
        for fragment in fragments
    ]
    return "\n\n".join(blocks)
