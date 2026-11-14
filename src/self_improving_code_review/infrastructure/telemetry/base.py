"""遠隔測定の安全境界と既定実装."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from threading import Lock
from typing import Literal

from self_improving_code_review.application.ports.telemetry import (
    Telemetry,
    TelemetryAttributes,
    TelemetryHealth,
    TelemetrySpan,
    validate_telemetry_attributes,
)

__all__ = ["FailureIsolatedTelemetry", "NoopTelemetry"]

_VALIDATION_ERROR = "TELEMETRY_ATTRIBUTE_REJECTED"
_EXPORT_ERROR = "TELEMETRY_EXPORT_FAILED"


class NoopTelemetry:
    """外部送信も状態変更も行わない既定の遠隔測定実装."""

    def start_span(self, name: str, attributes: TelemetryAttributes | None = None) -> TelemetrySpan:
        """何も記録しない空の処理区間を返す."""
        del name, attributes
        return nullcontext()

    def add_event(self, name: str, attributes: TelemetryAttributes | None = None) -> None:
        """イベントを記録せずに終了する."""

    def record_counter(
        self, name: str, value: int, attributes: TelemetryAttributes | None = None
    ) -> None:
        """加算値を記録せずに終了する."""

    def record_histogram(
        self,
        name: str,
        value: int | float,
        attributes: TelemetryAttributes | None = None,
    ) -> None:
        """分布値を記録せずに終了する."""

    @property
    def health(self) -> TelemetryHealth:
        """破棄がない健全状態を返す."""
        return TelemetryHealth()

    def close(self) -> None:
        """終了処理を行わずに返す."""


class _FailureIsolatedSpan:
    """スパン開始・終了時の送信障害を業務例外から分離する."""

    def __init__(
        self,
        delegate: TelemetrySpan,
        record_drop: Callable[[str], None],
    ) -> None:
        """下位スパンと安全な破棄集計処理を保持する."""
        self._delegate = delegate
        self._record_drop = record_drop
        self._is_entered = False

    def __enter__(self) -> None:
        """開始障害を記録し、レビュー処理を継続可能にする."""
        try:
            self._delegate.__enter__()
            self._is_entered = True
        except Exception:
            self._record_drop(_EXPORT_ERROR)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> Literal[False]:
        """終了障害を記録し、業務例外は常に呼び出し元へ伝播させる."""
        if not self._is_entered:
            return False
        try:
            self._delegate.__exit__(exc_type, exc_value, traceback)
        except Exception:
            self._record_drop(_EXPORT_ERROR)
        return False


class FailureIsolatedTelemetry:
    """属性違反と実行中の送信失敗を業務結果から分離する装飾実装."""

    def __init__(self, delegate: Telemetry) -> None:
        """安全境界の内側で呼び出す遠隔測定実装を保持する."""
        self._delegate = delegate
        self._lock = Lock()
        self._dropped_count = 0
        self._error_code: str | None = None

    def start_span(self, name: str, attributes: TelemetryAttributes | None = None) -> TelemetrySpan:
        """不正属性または開始失敗時に空の処理区間へ安全に置き換える."""
        try:
            validated = validate_telemetry_attributes(attributes)
            span = self._delegate.start_span(name, validated)
            return _FailureIsolatedSpan(span, self._record_drop)
        except ValueError:
            self._record_drop(_VALIDATION_ERROR)
        except Exception:
            self._record_drop(_EXPORT_ERROR)
        return nullcontext()

    def add_event(self, name: str, attributes: TelemetryAttributes | None = None) -> None:
        """検証済みイベントを送り、失敗した記録だけを破棄する."""
        self._call(
            lambda: self._delegate.add_event(name, validate_telemetry_attributes(attributes))
        )

    def record_counter(
        self, name: str, value: int, attributes: TelemetryAttributes | None = None
    ) -> None:
        """検証済みの非負加算値を送り、失敗をレビューから分離する."""
        if value < 0:
            self._record_drop(_VALIDATION_ERROR)
            return
        self._call(
            lambda: self._delegate.record_counter(
                name,
                value,
                validate_telemetry_attributes(attributes),
            )
        )

    def record_histogram(
        self,
        name: str,
        value: int | float,
        attributes: TelemetryAttributes | None = None,
    ) -> None:
        """検証済みの非負分布値を送り、失敗をレビューから分離する."""
        if value < 0:
            self._record_drop(_VALIDATION_ERROR)
            return
        self._call(
            lambda: self._delegate.record_histogram(
                name,
                value,
                validate_telemetry_attributes(attributes),
            )
        )

    @property
    def health(self) -> TelemetryHealth:
        """この安全境界と下位実装の破棄件数を合算して返す."""
        delegate_health = self._delegate.health
        with self._lock:
            return TelemetryHealth(
                error_code=self._error_code or delegate_health.error_code,
                dropped_count=self._dropped_count + delegate_health.dropped_count,
            )

    def close(self) -> None:
        """終了処理の失敗もレビュー結果へ伝播させずに記録する."""
        self._call(self._delegate.close)

    def _call(self, operation: Callable[[], None]) -> None:
        """一つの遠隔測定操作を安全に実行する."""
        try:
            operation()
        except ValueError:
            self._record_drop(_VALIDATION_ERROR)
        except Exception:
            self._record_drop(_EXPORT_ERROR)

    def _record_drop(self, error_code: str) -> None:
        """生の例外を保持せず安全なコードと破棄件数だけを更新する."""
        with self._lock:
            self._error_code = error_code
            self._dropped_count += 1
