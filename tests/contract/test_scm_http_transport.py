from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.message import Message
from http.client import HTTPResponse
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from self_improving_code_review.domain.common.errors import ScmClientError
from self_improving_code_review.infrastructure.scm import http_transport
from self_improving_code_review.infrastructure.scm.http_transport import UrllibHttpTransport
from self_improving_code_review.messages import Messages


@dataclass
class _Response:
    """urllib 応答の文脈管理と読み出し契約を再現する."""

    status: int = 200
    body: bytes = b'{"ok":true}'
    content_length: str | None = None
    read_sizes: list[int] = field(default_factory=list)

    @property
    def headers(self) -> Message:
        """固定の応答ヘッダーを返す."""
        values = Message()
        values["X-Test"] = "value"
        if self.content_length is not None:
            values["Content-Length"] = self.content_length
        return values

    def read(self, size: int = -1) -> bytes:
        """要求された上限を記録して固定本文を返す."""
        self.read_sizes.append(size)
        return self.body if size < 0 else self.body[:size]

    def __enter__(self) -> _Response:
        """応答自身を文脈値として返す."""
        return self

    def __exit__(self, *args: object) -> None:
        """終了時に追加処理を行わない."""


@pytest.mark.case("CT-M4-001")
def test_urllib_transport_returns_success_response(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[Request, int]] = []

    def fake_urlopen(request: Request, *, timeout: int) -> _Response:
        captured.append((request, timeout))
        return _Response()

    monkeypatch.setattr(http_transport, "urlopen", fake_urlopen)

    response = UrllibHttpTransport().request(
        "POST",
        "https://api.github.com/test",
        headers={"Authorization": "Bearer token"},
        body=b"{}",
        timeout_seconds=12,
    )

    assert response.status_code == 200
    assert response.headers == {"X-Test": "value"}
    assert response.body == b'{"ok":true}'
    assert captured[0][0].get_method() == "POST"
    assert captured[0][1] == 12


@pytest.mark.case("CT-M4-001")
@pytest.mark.parametrize(
    "url",
    ["http://api.github.com/test", "https:///missing-host"],
)
def test_urllib_transport_rejects_non_https_or_relative_url(url: str) -> None:
    """HTTPS 以外またはホストなしの要求先を外部接続前に拒否する."""
    with pytest.raises(ScmClientError, match=re.escape(Messages.SCM_API_URL_NOT_HTTPS)):
        UrllibHttpTransport().request(
            "GET",
            url,
            headers={},
            body=None,
            timeout_seconds=10,
        )


@pytest.mark.case("CT-M4-001")
def test_urllib_transport_accepts_response_at_exact_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(body=b"1234", content_length="4")
    monkeypatch.setattr(http_transport, "urlopen", lambda *_args, **_kwargs: response)

    result = UrllibHttpTransport(max_response_bytes=4).request(
        "GET", "https://api.github.com/test", headers={}, body=None, timeout_seconds=10
    )

    assert result.body == b"1234"
    assert response.read_sizes == [5]


@pytest.mark.case("CT-M4-001")
def test_urllib_transport_rejects_declared_oversized_response_before_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(body=b"12345", content_length="5")
    monkeypatch.setattr(http_transport, "urlopen", lambda *_args, **_kwargs: response)

    with pytest.raises(
        ScmClientError, match=re.escape(Messages.SCM_API_RESPONSE_TOO_LARGE)
    ) as raised:
        UrllibHttpTransport(max_response_bytes=4).request(
            "GET", "https://api.github.com/test", headers={}, body=None, timeout_seconds=10
        )

    assert not raised.value.is_retryable
    assert response.read_sizes == []


@pytest.mark.case("CT-M4-001")
def test_urllib_transport_rejects_oversized_response_without_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(body=b"12345")
    monkeypatch.setattr(http_transport, "urlopen", lambda *_args, **_kwargs: response)

    with pytest.raises(ScmClientError, match=re.escape(Messages.SCM_API_RESPONSE_TOO_LARGE)):
        UrllibHttpTransport(max_response_bytes=4).request(
            "GET", "https://api.github.com/test", headers={}, body=None, timeout_seconds=10
        )

    assert response.read_sizes == [5]


@pytest.mark.case("CT-M4-001")
@pytest.mark.parametrize("content_length", ["invalid", "-1"])
def test_urllib_transport_rejects_invalid_content_length(
    monkeypatch: pytest.MonkeyPatch,
    content_length: str,
) -> None:
    response = _Response(content_length=content_length)
    monkeypatch.setattr(http_transport, "urlopen", lambda *_args, **_kwargs: response)

    with pytest.raises(ScmClientError, match=re.escape(Messages.SCM_API_RESPONSE_METADATA_INVALID)):
        UrllibHttpTransport(max_response_bytes=4).request(
            "GET", "https://api.github.com/test", headers={}, body=None, timeout_seconds=10
        )

    assert response.read_sizes == []


@pytest.mark.case("CT-M4-001")
def test_urllib_transport_preserves_http_error_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = Message()
    headers["Retry-After"] = "1"
    error = HTTPError(
        "https://api.github.com/test",
        429,
        "rate limited",
        headers,
        BytesIO(b'{"error":"rate"}'),
    )

    def fake_urlopen(request: Request, *, timeout: int) -> _Response:
        del request, timeout
        raise error

    monkeypatch.setattr(http_transport, "urlopen", fake_urlopen)

    response = UrllibHttpTransport().request(
        "GET",
        "https://api.github.com/test",
        headers={},
        body=None,
        timeout_seconds=10,
    )

    assert response.status_code == 429
    assert response.headers == {"Retry-After": "1"}
    assert response.body == b'{"error":"rate"}'
    assert error.closed


class _Socket:
    """実通信せず標準 HTTPResponse の解放を観測する最小ソケット。"""

    def __init__(self, raw_response: bytes) -> None:
        """ヘッダーと本文を含む固定応答ストリームを保持する。"""
        self.stream = BytesIO(raw_response)

    def makefile(self, mode: str) -> BytesIO:
        """HTTPResponse が要求した読取りストリームを返す。"""
        assert mode == "rb"
        return self.stream


@pytest.mark.case("CT-M4-001")
@pytest.mark.parametrize("is_http_error", [False, True])
@pytest.mark.parametrize(
    ("content_length", "body_size", "expected_message"),
    [
        ("5", 5, None),
        ("1024", 1024, None),
        ("2048", 2048, Messages.SCM_API_RESPONSE_TOO_LARGE),
        (None, 2048, Messages.SCM_API_RESPONSE_TOO_LARGE),
        ("invalid", 5, Messages.SCM_API_RESPONSE_METADATA_INVALID),
        ("-1", 5, Messages.SCM_API_RESPONSE_METADATA_INVALID),
    ],
)
def test_transport_closes_standard_response_on_every_read_path(
    monkeypatch: pytest.MonkeyPatch,
    is_http_error: bool,
    content_length: str | None,
    body_size: int,
    expected_message: str | None,
) -> None:
    """正常・HTTP エラー応答を上限拒否やメタデータ不正でも確実に解放する。"""
    status = "429 Too Many Requests" if is_http_error else "200 OK"
    header = "" if content_length is None else f"Content-Length: {content_length}\r\n"
    socket = _Socket(f"HTTP/1.1 {status}\r\n{header}\r\n".encode() + b"x" * body_size)
    response = HTTPResponse(socket)
    response.begin()
    error = HTTPError("https://example.invalid/", 429, "rate limited", response.headers, response)

    def fake_urlopen(request: Request, *, timeout: int) -> HTTPResponse:
        """実通信の代わりに同じ標準応答またはそれを所有する例外を返す。"""
        assert request.full_url == "https://example.invalid/"
        assert timeout == 3
        if is_http_error:
            raise error
        return response

    monkeypatch.setattr(http_transport, "urlopen", fake_urlopen)
    transport = UrllibHttpTransport(max_response_bytes=1024)
    try:
        if expected_message is None:
            result = transport.request(
                "GET", "https://example.invalid/", headers={}, body=None, timeout_seconds=3
            )
            assert result.status_code == (429 if is_http_error else 200)
            assert result.body == b"x" * body_size
        else:
            with pytest.raises(ScmClientError, match=re.escape(expected_message)):
                transport.request(
                    "GET", "https://example.invalid/", headers={}, body=None, timeout_seconds=3
                )
        assert response.isclosed()
        assert socket.stream.closed
    finally:
        error.close()
        response.close()


@pytest.mark.case("CT-M4-001")
@pytest.mark.parametrize(
    "error",
    [URLError("offline"), TimeoutError("timeout"), OSError("network")],
)
def test_urllib_transport_maps_connection_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    def fake_urlopen(request: Request, *, timeout: int) -> _Response:
        del request, timeout
        raise error

    monkeypatch.setattr(http_transport, "urlopen", fake_urlopen)

    with pytest.raises(
        ScmClientError, match=re.escape(Messages.SCM_API_CONNECTION_FAILED)
    ) as raised:
        UrllibHttpTransport().request(
            "GET",
            "https://api.github.com/test",
            headers={},
            body=None,
            timeout_seconds=10,
        )

    assert raised.value.is_retryable
