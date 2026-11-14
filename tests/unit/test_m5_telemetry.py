from __future__ import annotations

import builtins
import re
from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any

import pytest

from self_improving_code_review.application.config.settings import TelemetryConfig
from self_improving_code_review.application.ports.telemetry import (
    ALLOWED_TELEMETRY_ATTRIBUTES,
    TelemetryHealth,
    validate_telemetry_attributes,
)
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.infrastructure.telemetry import (
    FailureIsolatedTelemetry,
    NoopTelemetry,
)
from self_improving_code_review.infrastructure.telemetry.opentelemetry_adapter import (
    OpenTelemetryTelemetry,
    _OpenTelemetryBackend,
    _parse_headers,
    _signal_endpoint,
    build_opentelemetry,
)
from self_improving_code_review.messages import Messages


class RecordingSpan:
    def __init__(self, *, enter_error: bool = False, exit_error: bool = False) -> None:
        self.enter_error = enter_error
        self.exit_error = exit_error
        self.entered = False
        self.exited = False

    def __enter__(self) -> None:
        if self.enter_error:
            raise RuntimeError("enter")
        self.entered = True

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: object | None,
    ) -> bool:
        self.exited = True
        if self.exit_error:
            raise RuntimeError("exit")
        return True


class RecordingTelemetry:
    def __init__(self, span: RecordingSpan | None = None) -> None:
        self.span = span or RecordingSpan()
        self.calls: list[tuple[str, object]] = []
        self.is_closed = False

    def start_span(
        self, name: str, attributes: Mapping[str, object] | None = None
    ) -> RecordingSpan:
        self.calls.append((name, attributes))
        return self.span

    def add_event(self, name: str, attributes: Mapping[str, object] | None = None) -> None:
        self.calls.append((name, attributes))

    def record_counter(
        self, name: str, value: int, attributes: Mapping[str, object] | None = None
    ) -> None:
        self.calls.append((name, (value, attributes)))

    def record_histogram(
        self,
        name: str,
        value: int | float,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        self.calls.append((name, (value, attributes)))

    @property
    def health(self) -> TelemetryHealth:
        return TelemetryHealth()

    def close(self) -> None:
        self.is_closed = True


class ExplodingTelemetry(RecordingTelemetry):
    def __init__(self, failure: type[Exception]) -> None:
        super().__init__()
        self._failure = failure

    def start_span(
        self, name: str, attributes: Mapping[str, object] | None = None
    ) -> RecordingSpan:
        del name, attributes
        raise self._failure("start")

    def add_event(self, name: str, attributes: Mapping[str, object] | None = None) -> None:
        del name, attributes
        raise self._failure("event")

    def record_counter(
        self, name: str, value: int, attributes: Mapping[str, object] | None = None
    ) -> None:
        del name, value, attributes
        raise self._failure("counter")

    def record_histogram(
        self,
        name: str,
        value: int | float,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        del name, value, attributes
        raise self._failure("histogram")

    def close(self) -> None:
        raise self._failure("close")


class FakeCounter:
    def __init__(self) -> None:
        self.values: list[tuple[int, Mapping[str, object] | None]] = []

    def add(self, amount: int, attributes: Mapping[str, object] | None = None) -> None:
        self.values.append((amount, attributes))


class FakeHistogram:
    def __init__(self) -> None:
        self.values: list[tuple[int | float, Mapping[str, object] | None]] = []

    def record(self, amount: int | float, attributes: Mapping[str, object] | None = None) -> None:
        self.values.append((amount, attributes))


class FakeMeter:
    def __init__(self) -> None:
        self.counter = FakeCounter()
        self.histogram = FakeHistogram()
        self.counter_creations = 0
        self.histogram_creations = 0

    def create_counter(self, _name: str) -> FakeCounter:
        self.counter_creations += 1
        return self.counter

    def create_histogram(self, _name: str) -> FakeHistogram:
        self.histogram_creations += 1
        return self.histogram


class FakeCurrentSpan:
    def __init__(self) -> None:
        self.events: list[tuple[str, Mapping[str, object] | None]] = []

    def add_event(self, name: str, attributes: Mapping[str, object] | None = None) -> None:
        self.events.append((name, attributes))


class FakeTracer:
    def __init__(self) -> None:
        self.spans: list[tuple[str, Mapping[str, object] | None]] = []

    def start_as_current_span(
        self, name: str, attributes: Mapping[str, object] | None = None
    ) -> Any:
        self.spans.append((name, attributes))
        return nullcontext()


class FakeProvider:
    def __init__(self, *, flush_result: bool = True) -> None:
        self.flush_result = flush_result
        self.flush_timeouts: list[int] = []
        self.shutdown_timeouts: list[int | None] = []

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        self.flush_timeouts.append(timeout_millis)
        return self.flush_result

    def shutdown(self, timeout_millis: int | None = None) -> None:
        self.shutdown_timeouts.append(timeout_millis)


@pytest.mark.case("UT-M5-007")
def test_attribute_allowlist_accepts_only_bounded_safe_values() -> None:
    assert validate_telemetry_attributes(None) == {}
    assert validate_telemetry_attributes({"status": "success", "input_tokens": 1}) == {
        "status": "success",
        "input_tokens": 1,
    }

    with pytest.raises(ValueError, match=re.escape(Messages.TELEMETRY_ATTRIBUTE_NAME_FORBIDDEN)):
        validate_telemetry_attributes({"path": "src/secret.py"})
    with pytest.raises(ValueError, match=re.escape(Messages.TELEMETRY_ATTRIBUTE_TOO_LONG)):
        validate_telemetry_attributes({"status": "x" * 129})
    too_many = dict.fromkeys(ALLOWED_TELEMETRY_ATTRIBUTES, 1)
    too_many["unknown"] = 1
    with pytest.raises(ValueError, match=re.escape(Messages.TELEMETRY_ATTRIBUTE_COUNT_EXCEEDED)):
        validate_telemetry_attributes(too_many)


@pytest.mark.case("UT-M5-007")
def test_noop_telemetry_has_no_side_effects() -> None:
    telemetry = NoopTelemetry()

    with telemetry.start_span("review.run", {"status": "success"}):
        telemetry.add_event("event")
        telemetry.record_counter("counter", 1)
        telemetry.record_histogram("histogram", 1.5)
    telemetry.close()

    assert telemetry.health == TelemetryHealth()


@pytest.mark.case("UT-M5-007")
def test_failure_isolation_validates_values_and_forwards_safe_records() -> None:
    delegate = RecordingTelemetry()
    telemetry = FailureIsolatedTelemetry(delegate)

    with telemetry.start_span("review.run", {"status": "success"}):
        telemetry.add_event("done", {"finding_count": 1})
    telemetry.record_counter("counter", 2, {"input_tokens": 2})
    telemetry.record_histogram("histogram", 3.5, {"duration_ms": 3})
    telemetry.close()

    assert delegate.span.entered
    assert delegate.span.exited
    assert delegate.is_closed
    assert telemetry.health == TelemetryHealth()
    assert len(delegate.calls) == 4


@pytest.mark.case("UT-M5-007")
@pytest.mark.parametrize("failure", [ValueError, RuntimeError])
def test_failure_isolation_drops_delegate_failures(failure: type[Exception]) -> None:
    telemetry = FailureIsolatedTelemetry(ExplodingTelemetry(failure))

    with telemetry.start_span("review.run"):
        telemetry.add_event("event")
    telemetry.record_counter("counter", 1)
    telemetry.record_histogram("histogram", 1)
    telemetry.close()

    assert telemetry.health.dropped_count == 5
    expected = (
        "TELEMETRY_ATTRIBUTE_REJECTED" if failure is ValueError else "TELEMETRY_EXPORT_FAILED"
    )
    assert telemetry.health.error_code == expected


@pytest.mark.case("UT-M5-007")
def test_failure_isolation_drops_invalid_values_and_span_lifecycle_failures() -> None:
    enter_telemetry = FailureIsolatedTelemetry(RecordingTelemetry(RecordingSpan(enter_error=True)))
    with enter_telemetry.start_span("review.run"):
        pass

    exit_telemetry = FailureIsolatedTelemetry(RecordingTelemetry(RecordingSpan(exit_error=True)))
    with exit_telemetry.start_span("review.run"):
        pass
    exit_telemetry.add_event("unsafe", {"path": "src/secret.py"})
    exit_telemetry.record_counter("negative", -1)
    exit_telemetry.record_histogram("negative", -1.0)

    assert enter_telemetry.health.dropped_count == 1
    assert exit_telemetry.health.dropped_count == 4


@pytest.mark.case("UT-M5-007")
def test_opentelemetry_backend_reuses_instruments_and_flushes() -> None:
    meter = FakeMeter()
    current_span = FakeCurrentSpan()
    trace_provider = FakeProvider()
    meter_provider = FakeProvider()
    tracer = FakeTracer()
    backend = _OpenTelemetryBackend(
        tracer=tracer,
        meter=meter,
        trace_provider=trace_provider,
        meter_provider=meter_provider,
        current_span=lambda: current_span,
        timeout_millis=2500,
    )
    adapter = OpenTelemetryTelemetry(backend)

    with adapter.start_span("review.run", {"status": "success"}):
        adapter.add_event("done", {"finding_count": 1})
    adapter.record_counter("tokens", 1, {"input_tokens": 1})
    adapter.record_counter("tokens", 2, {"input_tokens": 2})
    adapter.record_histogram("duration", 3, {"duration_ms": 3})
    adapter.record_histogram("duration", 4, {"duration_ms": 4})

    adapter.close()
    assert adapter.health == TelemetryHealth()
    assert meter.counter_creations == 1
    assert meter.histogram_creations == 1
    assert current_span.events == [("done", {"finding_count": 1})]
    assert trace_provider.shutdown_timeouts == [None]
    assert meter_provider.shutdown_timeouts == [2500]


@pytest.mark.case("UT-M5-007")
def test_opentelemetry_adapter_records_health_and_closes_once() -> None:

    class FailedBackend:
        def close(self) -> bool:
            return False

    adapter = OpenTelemetryTelemetry(FailedBackend())
    adapter.close()
    adapter.close()

    assert adapter.health == TelemetryHealth(
        error_code="TELEMETRY_EXPORT_FAILED",
        dropped_count=1,
    )


@pytest.mark.case("UT-M5-007")
@pytest.mark.parametrize(
    ("endpoint", "signal", "expected"),
    [
        ("https://otel.example.com", "traces", "https://otel.example.com/v1/traces"),
        (
            "https://otel.example.com/v1/traces",
            "metrics",
            "https://otel.example.com/v1/metrics",
        ),
        (
            "https://otel.example.com/v1/metrics",
            "traces",
            "https://otel.example.com/v1/traces",
        ),
    ],
)
def test_signal_endpoint_is_normalized(endpoint: str, signal: str, expected: str) -> None:
    assert _signal_endpoint(endpoint, signal) == expected


@pytest.mark.case("UT-M5-007")
def test_otel_headers_are_read_only_from_valid_environment_format() -> None:
    assert _parse_headers(None) == {}
    assert _parse_headers("  ") == {}
    assert _parse_headers("authorization=Bearer%20token,x-project=test") == {
        "authorization": "Bearer token",
        "x-project": "test",
    }

    for invalid in ("authorization", "authorization=\nvalue", "=value", "key="):
        with pytest.raises(InvalidInputError):
            _parse_headers(invalid)


@pytest.mark.case("UT-M5-007")
def test_build_opentelemetry_uses_optional_sdk_without_network() -> None:
    config = TelemetryConfig.model_validate(
        {
            "enabled": True,
            "provider": "opentelemetry",
            "endpoint": "https://otel.example.com",
            "allowed_hosts": ["otel.example.com"],
            "sample_ratio": 0,
        }
    )

    telemetry = build_opentelemetry(config, environ={})
    telemetry.close()

    assert telemetry.health == TelemetryHealth()


@pytest.mark.case("UT-M5-007")
def test_build_opentelemetry_rejects_missing_endpoint_and_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = TelemetryConfig.model_construct(
        enabled=True,
        provider="opentelemetry",
        endpoint=None,
    )
    with pytest.raises(InvalidInputError):
        build_opentelemetry(invalid, environ={})

    original_import = builtins.__import__

    def reject_opentelemetry(
        name: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if name == "opentelemetry":
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_opentelemetry)
    valid = TelemetryConfig.model_validate(
        {
            "enabled": True,
            "provider": "opentelemetry",
            "endpoint": "https://otel.example.com",
            "allowed_hosts": ["otel.example.com"],
        }
    )
    with pytest.raises(InvalidInputError):
        build_opentelemetry(valid, environ={})
