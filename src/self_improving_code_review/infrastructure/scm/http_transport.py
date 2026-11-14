"""SCM REST API 用の最小 HTTP 転送境界."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from self_improving_code_review.domain.common.errors import ScmClientError
from self_improving_code_review.messages import Messages

__all__ = ["HttpResponse", "HttpTransport", "UrllibHttpTransport"]

_DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class _ReadableBody(Protocol):
    """上限付き読出しに必要な最小応答契約."""

    def read(self, size: int = -1) -> bytes:
        """指定した最大バイト数まで読み出す."""
        ...


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """HTTP 応答の提供元非依存表現."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes


class HttpTransport(Protocol):
    """同期 HTTP 呼び出しをテストで差し替える境界."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
    ) -> HttpResponse:
        """単一 HTTP 要求を実行する."""
        ...


class UrllibHttpTransport:
    """標準ライブラリだけで HTTPS 要求を実行する転送実装."""

    def __init__(self, *, max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES) -> None:
        """単一応答を読み込む最大バイト数を保持する."""
        self._max_response_bytes = max_response_bytes

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
    ) -> HttpResponse:
        """HTTP エラー応答を保持し、接続障害を安全な例外へ変換する."""
        request = Request(_require_https_url(url), data=body, headers=dict(headers), method=method)
        try:
            # 直前に絶対 HTTPS URL だけへ制限しており、ローカルスキームを許可しない。
            with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310
                response_headers = dict(response.headers.items())
                return HttpResponse(
                    status_code=response.status,
                    headers=response_headers,
                    body=_read_limited(response, response_headers, self._max_response_bytes),
                )
        except HTTPError as exc:
            with closing(exc):
                response_headers = dict(exc.headers.items())
                return HttpResponse(
                    status_code=exc.code,
                    headers=response_headers,
                    body=_read_limited(exc, response_headers, self._max_response_bytes),
                )
        except (URLError, TimeoutError, OSError) as exc:
            message = Messages.SCM_API_CONNECTION_FAILED
            raise ScmClientError(message, is_retryable=True) from exc


def _require_https_url(url: str) -> str:
    """絶対 HTTPS URL だけを HTTP 転送境界へ通す."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname is None:
        raise ScmClientError(Messages.SCM_API_URL_NOT_HTTPS)
    return url


def _read_limited(
    response: _ReadableBody,
    headers: Mapping[str, str],
    max_response_bytes: int,
) -> bytes:
    """宣言値と実読出しの両方で応答本文の上限を強制する."""
    raw_content_length = headers.get("Content-Length")
    if raw_content_length is not None:
        try:
            content_length = int(raw_content_length)
        except ValueError as exc:
            raise ScmClientError(Messages.SCM_API_RESPONSE_METADATA_INVALID) from exc
        if content_length < 0:
            raise ScmClientError(Messages.SCM_API_RESPONSE_METADATA_INVALID)
        if content_length > max_response_bytes:
            raise ScmClientError(Messages.SCM_API_RESPONSE_TOO_LARGE)
    body = response.read(max_response_bytes + 1)
    if len(body) > max_response_bytes:
        raise ScmClientError(Messages.SCM_API_RESPONSE_TOO_LARGE)
    return body
