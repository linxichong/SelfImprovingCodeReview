"""OpenTelemetry SDK を隔離する遠隔測定アダプター."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from threading import Lock
from typing import Protocol, cast
from urllib.parse import parse_qsl

from self_improving_code_review.application.config.settings import (
    OTEL_HEADERS_ENV_VAR,
    TelemetryConfig,
)
from self_improving_code_review.application.ports.telemetry import (
    TelemetryAttributes,
    TelemetryHealth,
    TelemetrySpan,
)
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.messages import Messages

__all__ = ["OpenTelemetryTelemetry", "build_opentelemetry"]

_EXPORT_ERROR = "TELEMETRY_EXPORT_FAILED"


class _Counter(Protocol):
    """OpenTelemetry の加算計器に必要な最小契約."""

    def add(self, amount: int, attributes: Mapping[str, object] | None = None) -> None:
        """加算値を記録する."""


class _Histogram(Protocol):
    """OpenTelemetry の分布計器に必要な最小契約."""

    def record(self, amount: int | float, attributes: Mapping[str, object] | None = None) -> None:
        """分布値を記録する."""


class _Meter(Protocol):
    """必要な計器だけを生成する OpenTelemetry メーター契約."""

    def create_counter(self, name: str) -> _Counter:
        """加算計器を生成する."""

    def create_histogram(self, name: str) -> _Histogram:
        """分布計器を生成する."""


class _Span(Protocol):
    """現在のスパンへイベントを追加する最小契約."""

    def add_event(self, name: str, attributes: Mapping[str, object] | None = None) -> None:
        """イベントを追加する."""


class _Tracer(Protocol):
    """SDK スパンを開始する最小契約."""

    def start_as_current_span(
        self, name: str, attributes: Mapping[str, object] | None = None
    ) -> AbstractContextManager[object]:
        """現在のコンテキストを親としてスパンを開始する."""


class _Provider(Protocol):
    """SDK プロバイダーの終了処理に必要な最小契約."""

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """保留中の記録を制限時間内に送る."""

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        """プロバイダーを終了する."""


class _OpenTelemetryBackend:
    """OpenTelemetry SDK オブジェクトをアプリケーション型から隔離する."""

    def __init__(
        self,
        *,
        tracer: _Tracer,
        meter: _Meter,
        trace_provider: _Provider,
        meter_provider: _Provider,
        current_span: Callable[[], _Span],
        timeout_millis: int,
    ) -> None:
        """SDK のトレーサー、メーターおよび終了処理を保持する."""
        self._tracer = tracer
        self._meter = meter
        self._trace_provider = trace_provider
        self._meter_provider = meter_provider
        self._current_span = current_span
        self._timeout_millis = timeout_millis
        self._counters: dict[str, _Counter] = {}
        self._histograms: dict[str, _Histogram] = {}
        self._lock = Lock()

    def start_span(self, name: str, attributes: TelemetryAttributes) -> TelemetrySpan:
        """SDK の現在スパン用コンテキスト管理オブジェクトを返す."""
        return cast(
            TelemetrySpan,
            self._tracer.start_as_current_span(name, attributes=dict(attributes)),
        )

    def add_event(self, name: str, attributes: TelemetryAttributes) -> None:
        """SDK の現在スパンへイベントを追加する."""
        self._current_span().add_event(name, attributes=dict(attributes))

    def record_counter(self, name: str, value: int, attributes: TelemetryAttributes) -> None:
        """名前ごとに再利用する SDK 加算計器へ値を渡す."""
        with self._lock:
            counter = self._counters.get(name)
            if counter is None:
                counter = self._meter.create_counter(name)
                self._counters[name] = counter
        counter.add(value, attributes=dict(attributes))

    def record_histogram(
        self, name: str, value: int | float, attributes: TelemetryAttributes
    ) -> None:
        """名前ごとに再利用する SDK 分布計器へ値を渡す."""
        with self._lock:
            histogram = self._histograms.get(name)
            if histogram is None:
                histogram = self._meter.create_histogram(name)
                self._histograms[name] = histogram
        histogram.record(value, attributes=dict(attributes))

    def close(self) -> bool:
        """トレースと計測値を上限時間内にフラッシュして終了する."""
        trace_flushed = self._trace_provider.force_flush(self._timeout_millis)
        metric_flushed = self._meter_provider.force_flush(self._timeout_millis)
        self._trace_provider.shutdown()
        self._meter_provider.shutdown(timeout_millis=self._timeout_millis)
        return trace_flushed and metric_flushed


class OpenTelemetryTelemetry:
    """OpenTelemetry バックエンドへ共通遠隔測定契約を変換する."""

    def __init__(self, backend: _OpenTelemetryBackend) -> None:
        """構築済みでネットワーク境界を持つ SDK バックエンドを保持する."""
        self._backend = backend
        self._health = TelemetryHealth()
        self._is_closed = False

    def start_span(self, name: str, attributes: TelemetryAttributes | None = None) -> TelemetrySpan:
        """OpenTelemetry の処理区間を開始する."""
        return self._backend.start_span(name, attributes or {})

    def add_event(self, name: str, attributes: TelemetryAttributes | None = None) -> None:
        """OpenTelemetry の現在スパンへイベントを追加する."""
        self._backend.add_event(name, attributes or {})

    def record_counter(
        self, name: str, value: int, attributes: TelemetryAttributes | None = None
    ) -> None:
        """OpenTelemetry の加算計器へ値を記録する."""
        self._backend.record_counter(name, value, attributes or {})

    def record_histogram(
        self,
        name: str,
        value: int | float,
        attributes: TelemetryAttributes | None = None,
    ) -> None:
        """OpenTelemetry の分布計器へ値を記録する."""
        self._backend.record_histogram(name, value, attributes or {})

    @property
    def health(self) -> TelemetryHealth:
        """フラッシュ失敗による安全な破棄集計を返す."""
        return self._health

    def close(self) -> None:
        """一度だけ SDK を終了し、フラッシュ失敗を安全な集計へ変換する."""
        if self._is_closed:
            return
        self._is_closed = True
        if not self._backend.close():
            self._health = TelemetryHealth(error_code=_EXPORT_ERROR, dropped_count=1)


def build_opentelemetry(
    config: TelemetryConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> OpenTelemetryTelemetry:
    """検証済み設定から任意依存の OpenTelemetry 実装を構築する."""
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
    except ImportError as exc:
        raise InvalidInputError(Messages.OTEL_DEPENDENCY_REQUIRED) from exc

    endpoint = config.endpoint
    if endpoint is None:
        raise InvalidInputError(Messages.OTEL_ENDPOINT_REQUIRED)
    headers = _parse_headers((environ or os.environ).get(OTEL_HEADERS_ENV_VAR))
    timeout_millis = config.export_timeout_seconds * 1000
    resource = Resource.create({"service.name": config.service_name})
    span_exporter = OTLPSpanExporter(
        endpoint=_signal_endpoint(endpoint, "traces"),
        headers=headers,
        timeout=config.export_timeout_seconds,
    )
    trace_provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(config.sample_ratio)),
        shutdown_on_exit=False,
    )
    trace_provider.add_span_processor(
        BatchSpanProcessor(
            span_exporter,
            max_queue_size=config.max_queue_size,
            max_export_batch_size=min(config.max_queue_size, 512),
            export_timeout_millis=timeout_millis,
        )
    )
    metric_exporter = OTLPMetricExporter(
        endpoint=_signal_endpoint(endpoint, "metrics"),
        headers=headers,
        timeout=config.export_timeout_seconds,
    )
    metric_reader = PeriodicExportingMetricReader(
        metric_exporter,
        export_timeout_millis=timeout_millis,
    )
    meter_provider = MeterProvider(
        metric_readers=(metric_reader,),
        resource=resource,
        shutdown_on_exit=False,
    )
    backend = _OpenTelemetryBackend(
        tracer=cast(_Tracer, trace_provider.get_tracer(config.service_name)),
        meter=cast(_Meter, meter_provider.get_meter(config.service_name)),
        trace_provider=cast(_Provider, trace_provider),
        meter_provider=cast(_Provider, meter_provider),
        current_span=cast(Callable[[], _Span], trace.get_current_span),
        timeout_millis=timeout_millis,
    )
    return OpenTelemetryTelemetry(backend)


def _signal_endpoint(endpoint: str, signal: str) -> str:
    """共通または個別 OTLP HTTP URL を信号別 URL へ正規化する."""
    normalized = endpoint.rstrip("/")
    for suffix in ("/v1/traces", "/v1/metrics"):
        if normalized.endswith(suffix):
            return f"{normalized[: -len(suffix)]}/v1/{signal}"
    return f"{normalized}/v1/{signal}"


def _parse_headers(raw_headers: str | None) -> dict[str, str]:
    """固定環境変数の OTLP ヘッダーを本文を露出せず検証する."""
    if raw_headers is None or not raw_headers.strip():
        return {}
    if any(character in raw_headers for character in "\r\n") or "=" not in raw_headers:
        raise InvalidInputError(Messages.OTEL_HEADERS_INVALID)
    parsed = dict(parse_qsl(raw_headers.replace(",", "&"), keep_blank_values=True))
    if not parsed or any(not key or not value for key, value in parsed.items()):
        raise InvalidInputError(Messages.OTEL_HEADERS_INVALID)
    return parsed
