"""機密情報を外部へ公開しない遠隔測定ポート."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from self_improving_code_review.messages import Messages

__all__ = [
    "ALLOWED_TELEMETRY_ATTRIBUTES",
    "Telemetry",
    "TelemetryAttributes",
    "TelemetryHealth",
    "TelemetrySpan",
    "TelemetryValue",
    "validate_telemetry_attributes",
]

type TelemetryValue = str | int | float | bool
type TelemetryAttributes = Mapping[str, TelemetryValue]

ALLOWED_TELEMETRY_ATTRIBUTES = frozenset(
    {
        "run_id",
        "bundle_id",
        "project_id",
        "status",
        "error_code",
        "model",
        "prompt_version",
        "reviewer_profile",
        "provider",
        "input_tokens",
        "output_tokens",
        "duration_ms",
        "retry_count",
        "finding_count",
        "valid_finding_count",
        "duplicate_count",
        "blocked_count",
    }
)
_MAX_ATTRIBUTE_COUNT = len(ALLOWED_TELEMETRY_ATTRIBUTES)
_MAX_STRING_LENGTH = 128


@dataclass(frozen=True, slots=True)
class TelemetryHealth:
    """遠隔測定で破棄した記録の安全な集計値."""

    error_code: str | None = None
    dropped_count: int = 0


class TelemetrySpan(AbstractContextManager[None], Protocol):
    """SDK 型を公開しないスパンのコンテキスト管理契約."""

    def __enter__(self) -> None:
        """スパンを開始する."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> bool | None:
        """スパンを終了する."""


class Telemetry(Protocol):
    """レビュー処理が利用する共通遠隔測定契約."""

    def start_span(self, name: str, attributes: TelemetryAttributes | None = None) -> TelemetrySpan:
        """指定した処理区間のスパンを開始する."""

    def add_event(self, name: str, attributes: TelemetryAttributes | None = None) -> None:
        """現在の処理へ安全なイベントを追加する."""

    def record_counter(
        self, name: str, value: int, attributes: TelemetryAttributes | None = None
    ) -> None:
        """非負の加算値を記録する."""

    def record_histogram(
        self,
        name: str,
        value: int | float,
        attributes: TelemetryAttributes | None = None,
    ) -> None:
        """非負の分布値を記録する."""

    @property
    def health(self) -> TelemetryHealth:
        """破棄件数と安全なエラーコードを返す."""

    def close(self) -> None:
        """設定された上限時間内で保留中の記録を終了処理する."""


def validate_telemetry_attributes(
    attributes: TelemetryAttributes | None,
) -> dict[str, TelemetryValue]:
    """属性名、属性数および文字列長を許可された範囲へ限定する."""
    if attributes is None:
        return {}
    if len(attributes) > _MAX_ATTRIBUTE_COUNT:
        raise ValueError(Messages.TELEMETRY_ATTRIBUTE_COUNT_EXCEEDED)
    validated: dict[str, TelemetryValue] = {}
    for name, value in attributes.items():
        if name not in ALLOWED_TELEMETRY_ATTRIBUTES:
            raise ValueError(Messages.TELEMETRY_ATTRIBUTE_NAME_FORBIDDEN)
        if isinstance(value, str) and len(value) > _MAX_STRING_LENGTH:
            raise ValueError(Messages.TELEMETRY_ATTRIBUTE_TOO_LONG)
        validated[name] = value
    return validated
