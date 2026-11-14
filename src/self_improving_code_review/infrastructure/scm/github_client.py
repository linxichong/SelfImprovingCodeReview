"""GitHub REST API を SCM ポートへ変換するアダプター."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import cast
from urllib.parse import urlencode

from self_improving_code_review.application.config.settings import ScmConfig
from self_improving_code_review.application.ports.scm_client import ScmClient
from self_improving_code_review.application.scm_metrics import ScmApiMetrics
from self_improving_code_review.domain.common.errors import ScmClientError
from self_improving_code_review.domain.scm.models import (
    ChangeRef,
    CommentEvent,
    CreateComment,
    EventPage,
    PublicationKind,
    PublishedComment,
    ScmChange,
    ScmChangeState,
    UpdateComment,
)
from self_improving_code_review.infrastructure.scm.http_transport import (
    HttpResponse,
    HttpTransport,
    UrllibHttpTransport,
)
from self_improving_code_review.messages import Messages

__all__ = ["GitHubScmClient"]

_API_VERSION = "2022-11-28"
_JSON_CONTENT_TYPE = "application/vnd.github+json"
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
# 提供元が長い待機を要求した場合は CLI を停止させず失敗として返す。
_MAX_RATE_LIMIT_WAIT_SECONDS = 60.0


class GitHubScmClient(ScmClient):
    """GitHub の Pull Request とコメント API を安全な領域型へ変換する."""

    def __init__(
        self,
        config: ScmConfig,
        token: str,
        *,
        transport: HttpTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now_epoch: Callable[[], float] = time.time,
    ) -> None:
        """検証済み設定、環境由来トークンおよび任意の転送実装を受け取る."""
        if not token.strip():
            message = Messages.SCM_TOKEN_REQUIRED
            raise ScmClientError(message)
        self._config = config
        self._token = token
        self._transport = transport or UrllibHttpTransport(
            max_response_bytes=config.max_response_bytes
        )
        self._sleep = sleep
        self._now_epoch = now_epoch
        self._call_count = 0
        self._retry_count = 0
        self._retry_wait_ms = 0

    def get_metrics(self) -> ScmApiMetrics:
        """生成後の API 呼出し、再試行および待機時間を返す."""
        return ScmApiMetrics(
            call_count=self._call_count,
            retry_count=self._retry_count,
            retry_wait_ms=self._retry_wait_ms,
        )

    def get_change(self, ref: ChangeRef) -> ScmChange:
        """Pull Request を取得し、レビュー入力を固定する領域型へ変換する."""
        document = self._request_json("GET", self._change_path(ref), expected_status=200)
        base = _required_mapping(document, "base")
        head = _required_mapping(document, "head")
        head_repository = _required_mapping(head, "repo")
        try:
            state = ScmChangeState(_required_string(document, "state"))
        except ValueError as exc:
            raise ScmClientError(Messages.SCM_INVALID_RESPONSE) from exc
        return ScmChange(
            ref=ref,
            base_sha=_required_string(base, "sha"),
            head_sha=_required_string(head, "sha"),
            state=state,
            is_draft=_required_boolean(document, "draft"),
            source_repository_external_id=_required_string(head_repository, "full_name"),
            updated_at=_required_datetime(document, "updated_at"),
        )

    def get_authenticated_actor(self) -> str:
        """認証トークンに対応する GitHub ログイン名を返す."""
        document = self._request_json("GET", "/user", expected_status=200)
        return _required_string(document, "login")

    def create_comment(self, ref: ChangeRef, request: CreateComment) -> PublishedComment:
        """概要コメントまたは差分行コメントを作成する."""
        if request.kind is PublicationKind.SUMMARY:
            path = f"{self._issue_path(ref)}/comments"
            body: dict[str, object] = {"body": request.body}
        else:
            position = request.position
            if position is None:
                raise ScmClientError(Messages.SCM_INVALID_RESPONSE)
            path = f"{self._change_path(ref)}/comments"
            body = {
                "body": request.body,
                "commit_id": position.head_sha,
                "path": str(position.path),
                "line": position.line,
                "side": "RIGHT",
            }
        document = self._request_json("POST", path, expected_status=201, payload=body)
        return _published_comment(document)

    def update_comment(self, ref: ChangeRef, request: UpdateComment) -> PublishedComment:
        """既存の概要コメントまたは差分行コメントを更新する."""
        self._validate_ref(ref)
        if request.kind is PublicationKind.SUMMARY:
            path = (
                f"/repos/{ref.repository_external_id}/issues/comments/{request.comment_external_id}"
            )
        else:
            path = (
                f"/repos/{ref.repository_external_id}/pulls/comments/{request.comment_external_id}"
            )
        document = self._request_json(
            "PATCH",
            path,
            expected_status=200,
            payload={"body": request.body},
        )
        return _published_comment(document)

    def find_created_comment(
        self,
        ref: ChangeRef,
        request: CreateComment,
    ) -> PublishedComment | None:
        """同じ本文を持つ設定済みボットの既存コメントを一意に照合する."""
        query = "sort=created&direction=desc&per_page=100"
        if request.kind is PublicationKind.SUMMARY:
            path = f"{self._issue_path(ref)}/comments?{query}"
        else:
            path = f"{self._change_path(ref)}/comments?{query}"
        document = self._request_json_value("GET", path, expected_status=200)
        if not isinstance(document, list):
            raise ScmClientError(Messages.SCM_INVALID_RESPONSE)
        matches: list[PublishedComment] = []
        for value in document:
            item = _required_comment_mapping(value)
            user = _required_mapping(item, "user")
            if (
                _required_string(user, "login") == self._config.bot_actor_external_id
                and _required_string(item, "body") == request.body
            ):
                matches.append(_published_comment(item))
        if len(matches) > 1:
            raise ScmClientError(Messages.SCM_INVALID_RESPONSE)
        return matches[0] if matches else None

    def list_comment_events(
        self,
        ref: ChangeRef,
        *,
        cursor: str | None,
    ) -> EventPage:
        """Pull Request の概要コメントを更新日時順で一頁取得する."""
        since = _parse_cursor(cursor)
        parameters = {"sort": "updated", "direction": "asc", "per_page": 100}
        if since is not None:
            parameters["since"] = since
        query = urlencode(parameters)
        document = self._request_json_value(
            "GET",
            f"{self._change_path(ref)}/comments?{query}",
            expected_status=200,
        )
        if not isinstance(document, list):
            raise ScmClientError(Messages.SCM_INVALID_RESPONSE)
        events = tuple(
            sorted(
                (_comment_event(item) for item in document),
                key=lambda item: (item.updated_at, item.event_external_id),
            )
        )
        latest = max((item.updated_at for item in events), default=None)
        next_cursor = latest.isoformat() if latest is not None else since
        return EventPage(events=events, next_cursor=next_cursor)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        expected_status: int,
        payload: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        """JSON オブジェクト応答を取得する."""
        document = self._request_json_value(
            method,
            path,
            expected_status=expected_status,
            payload=payload,
        )
        if not isinstance(document, dict):
            raise ScmClientError(Messages.SCM_INVALID_RESPONSE)
        return cast(Mapping[str, object], document)

    def _request_json_value(
        self,
        method: str,
        path: str,
        *,
        expected_status: int,
        payload: Mapping[str, object] | None = None,
    ) -> object:
        """再試行方針を適用し、成功応答だけを JSON として復号する."""
        body = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if payload is not None
            else None
        )
        response = self._request_with_retry(
            method,
            path,
            body,
            can_retry=method != "POST",
        )
        if response.status_code != expected_status:
            raise _status_error(response)
        try:
            return json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ScmClientError(Messages.SCM_INVALID_RESPONSE) from exc

    def _request_with_retry(
        self,
        method: str,
        path: str,
        body: bytes | None,
        *,
        can_retry: bool,
    ) -> HttpResponse:
        """接続障害、レート制限および一時的 5xx だけを設定上限まで再試行する."""
        attempt = 0
        while True:
            try:
                self._call_count += 1
                response = self._transport.request(
                    method,
                    f"{self._config.api_base_url.rstrip('/')}{path}",
                    headers=self._headers(),
                    body=body,
                    timeout_seconds=self._config.request_timeout_seconds,
                )
            except ScmClientError as exc:
                if not can_retry or not exc.is_retryable or attempt >= self._config.max_retries:
                    raise
                self._wait_before_retry(attempt)
                attempt += 1
                continue
            if not can_retry or not _is_retryable_response(response):
                return response
            if attempt >= self._config.max_retries:
                return response
            delay_seconds = _rate_limit_delay_seconds(response.headers, self._now_epoch())
            if delay_seconds is not None and delay_seconds > _MAX_RATE_LIMIT_WAIT_SECONDS:
                return response
            self._wait_before_retry(attempt, delay_seconds)
            attempt += 1

    def _wait_before_retry(self, attempt: int, delay_seconds: float | None = None) -> None:
        """再試行待機を実行し、回数と予定待機時間を記録する."""
        if delay_seconds is None:
            delay_seconds = _retry_delay_seconds(attempt)
        self._retry_count += 1
        self._retry_wait_ms += int(delay_seconds * 1000)
        self._sleep(delay_seconds)

    def _headers(self) -> dict[str, str]:
        """GitHub が推奨する媒体型、API 版および認証ヘッダーを返す."""
        return {
            "Accept": _JSON_CONTENT_TYPE,
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "User-Agent": "self-improving-code-review",
            "X-GitHub-Api-Version": _API_VERSION,
        }

    def _change_path(self, ref: ChangeRef) -> str:
        """Pull Request API の相対パスを返す."""
        self._validate_ref(ref)
        return f"/repos/{ref.repository_external_id}/pulls/{ref.change_external_id}"

    def _issue_path(self, ref: ChangeRef) -> str:
        """Pull Request と共有する Issue API の相対パスを返す."""
        self._validate_ref(ref)
        return f"/repos/{ref.repository_external_id}/issues/{ref.change_external_id}"

    def _validate_ref(self, ref: ChangeRef) -> None:
        """設定対象リポジトリと正の Pull Request 番号を検証する."""
        if ref.repository_external_id != self._config.repository_external_id:
            message = Messages.SCM_REPOSITORY_MISMATCH
            raise ScmClientError(message)
        if not ref.change_external_id.isdigit() or int(ref.change_external_id) < 1:
            raise ScmClientError(Messages.SCM_INVALID_CHANGE_ID)


def _required_mapping(document: Mapping[str, object], field: str) -> Mapping[str, object]:
    """必須 JSON オブジェクトフィールドを返す."""
    value = document.get(field)
    if not isinstance(value, dict):
        raise ScmClientError(Messages.SCM_INVALID_RESPONSE)
    return cast(Mapping[str, object], value)


def _required_string(document: Mapping[str, object], field: str) -> str:
    """空でない必須 JSON 文字列フィールドを返す."""
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise ScmClientError(Messages.SCM_INVALID_RESPONSE)
    return value


def _required_boolean(document: Mapping[str, object], field: str) -> bool:
    """必須 JSON 真偽値フィールドを返す."""
    value = document.get(field)
    if type(value) is not bool:
        raise ScmClientError(Messages.SCM_INVALID_RESPONSE)
    return value


def _required_datetime(document: Mapping[str, object], field: str) -> datetime:
    """必須 ISO 8601 日時フィールドを返す."""
    raw_value = _required_string(document, field)
    try:
        value = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScmClientError(Messages.SCM_INVALID_RESPONSE) from exc
    if value.tzinfo is None:
        raise ScmClientError(Messages.SCM_INVALID_RESPONSE)
    return value


def _published_comment(document: Mapping[str, object]) -> PublishedComment:
    """GitHub コメント応答を公開結果へ変換する."""
    identifier = document.get("id")
    if not isinstance(identifier, int) or isinstance(identifier, bool):
        raise ScmClientError(Messages.SCM_INVALID_RESPONSE)
    return PublishedComment(
        comment_external_id=str(identifier),
        url=_required_string(document, "html_url"),
    )


def _comment_event(value: object) -> CommentEvent:
    """GitHub Issue コメントを同期イベントへ変換する."""
    if not isinstance(value, dict):
        raise ScmClientError(Messages.SCM_INVALID_RESPONSE)
    document = cast(Mapping[str, object], value)
    identifier = document.get("id")
    if not isinstance(identifier, int) or isinstance(identifier, bool):
        raise ScmClientError(Messages.SCM_INVALID_RESPONSE)
    user = _required_mapping(document, "user")
    reply_identifier = document.get("in_reply_to_id")
    if reply_identifier is not None and (
        not isinstance(reply_identifier, int) or isinstance(reply_identifier, bool)
    ):
        raise ScmClientError(Messages.SCM_INVALID_RESPONSE)
    return CommentEvent(
        event_external_id=f"review-comment:{identifier}",
        comment_external_id=str(identifier),
        target_comment_external_id=(
            str(reply_identifier) if reply_identifier is not None else None
        ),
        actor_external_id=_required_string(user, "login"),
        body=_required_string(document, "body"),
        updated_at=_required_datetime(document, "updated_at"),
    )


def _required_comment_mapping(value: object) -> Mapping[str, object]:
    """コメント一覧の各要素を JSON オブジェクトとして検証する."""
    if not isinstance(value, dict):
        raise ScmClientError(Messages.SCM_INVALID_RESPONSE)
    return cast(Mapping[str, object], value)


def _parse_cursor(cursor: str | None) -> str | None:
    """未設定またはタイムゾーン付き ISO 8601 カーソルを検証する."""
    if cursor is None:
        return None
    try:
        value = datetime.fromisoformat(cursor)
    except ValueError as exc:
        message = Messages.SCM_EVENT_CURSOR_INVALID
        raise ScmClientError(message) from exc
    if value.tzinfo is None:
        message = Messages.SCM_EVENT_CURSOR_INVALID
        raise ScmClientError(message)
    return cursor


def _is_retryable_status(status_code: int) -> bool:
    """一時的と見なす HTTP 状態の場合は True を返す."""
    return status_code in _RETRYABLE_STATUSES


def _is_rate_limited(response: HttpResponse) -> bool:
    """GitHub の一次レート制限による 403 応答の場合は True を返す."""
    return response.status_code == 403 and _header(response.headers, "X-RateLimit-Remaining") == "0"


def _is_retryable_response(response: HttpResponse) -> bool:
    """再試行対象の一時的障害またはレート制限応答の場合は True を返す."""
    return _is_retryable_status(response.status_code) or _is_rate_limited(response)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """大文字小文字を区別せずにヘッダー値を返す."""
    lowered = name.lower()
    return next(
        (value.strip() for key, value in headers.items() if key.lower() == lowered),
        None,
    )


def _rate_limit_delay_seconds(headers: Mapping[str, str], now_epoch: float) -> float | None:
    """``Retry-After`` または ``X-RateLimit-Reset`` から待機秒数を返す."""
    retry_after = _header(headers, "Retry-After")
    if retry_after is not None and retry_after.isdigit():
        return float(retry_after)
    reset = _header(headers, "X-RateLimit-Reset")
    if reset is not None and reset.isdigit():
        return max(float(reset) - now_epoch, 0.0)
    return None


def _retry_delay_seconds(attempt: int) -> float:
    """再試行回数に応じた短い指数待機時間を返す."""
    return 0.25 * float(2**attempt)


def _status_error(response: HttpResponse) -> ScmClientError:
    """HTTP 状態を安全な SCM 例外へ分類する."""
    status_code = response.status_code
    is_retryable = _is_retryable_response(response)
    if status_code == 429 or _is_rate_limited(response):
        message = Messages.SCM_RATE_LIMIT_EXHAUSTED
    elif status_code in {401, 403}:
        message = Messages.SCM_API_AUTH_FAILED
    elif status_code == 404:
        message = Messages.SCM_RESOURCE_NOT_FOUND
    elif status_code == 422:
        message = Messages.SCM_API_REQUEST_REJECTED
    elif is_retryable:
        message = Messages.SCM_API_TEMPORARY_FAILURE_EXHAUSTED
    else:
        message = Messages.SCM_API_REQUEST_FAILED
    return ScmClientError(
        message,
        is_retryable=is_retryable,
        status_code=status_code,
    )
