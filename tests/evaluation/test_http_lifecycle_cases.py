"""HTTP 安全対照例の正常・異常時の所有権をネットワークなしで検証する。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import TracebackType
from urllib.request import Request

import pytest


class TrackedResponse:
    """読取失敗後も生存させ、明示的な close を観測する応答模擬。"""

    def __init__(self, body: bytes, *, should_raise: bool) -> None:
        """本文と読取失敗条件を保持する。"""
        self.body = body
        self.should_raise = should_raise
        self.is_closed = False

    def __enter__(self) -> TrackedResponse:
        """所有権を持つ応答を返す。"""
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """例外の有無にかかわらず応答を閉じる。"""
        self.is_closed = True

    def read(self) -> bytes:
        """指定された失敗または本文を返す。"""
        if self.should_raise:
            raise TimeoutError
        return self.body


@pytest.mark.smoke
@pytest.mark.parametrize(
    "function_name", ["fetch_catalog", "fetch_metadata", "fetch_version", "fetch_status"]
)
@pytest.mark.parametrize("should_raise", [False, True])
def test_http_safe_example_closes_response_after_read(
    monkeypatch: pytest.MonkeyPatch, function_name: str, should_raise: bool
) -> None:
    """実行後も模擬応答を保持し、GC に依存せず正常・失敗時に閉じることを確認する。"""
    spec = importlib.util.spec_from_file_location(
        "http_lifecycle_cases", Path("datasets/development/http_lifecycle_cases.py")
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    response = TrackedResponse(b"data", should_raise=should_raise)

    def open_response(_url: str | Request, *, timeout: int) -> TrackedResponse:
        """外部通信を行わず所有権の検証対象を返す。"""
        assert timeout == 5
        return response

    monkeypatch.setattr(module, "urlopen", open_response)
    if should_raise:
        with pytest.raises(TimeoutError):
            getattr(module, function_name)()
    else:
        expected = "data" if function_name == "fetch_metadata" else b"data"
        assert getattr(module, function_name)() == expected
    assert response.is_closed


@pytest.mark.smoke
def test_http_metadata_closes_response_after_decode_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """読取後の UTF-8 復号エラーでも応答を閉じることを確認する。"""
    spec = importlib.util.spec_from_file_location(
        "http_lifecycle_cases", Path("datasets/development/http_lifecycle_cases.py")
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    response = TrackedResponse(b"\xff", should_raise=False)
    monkeypatch.setattr(module, "urlopen", lambda *_args, **_kwargs: response)
    with pytest.raises(UnicodeDecodeError):
        module.fetch_metadata()
    assert response.is_closed
