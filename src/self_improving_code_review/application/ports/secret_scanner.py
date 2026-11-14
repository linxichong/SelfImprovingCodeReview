"""機密情報検査ポート."""

from __future__ import annotations

from typing import Protocol

from self_improving_code_review.security.models import (
    ScannedPayload,
    ScanProfile,
    ScanResult,
    SourcePayload,
)

__all__ = ["SecretScanner"]


class SecretScanner(Protocol):
    """未信頼データがプロセス外へ出る前に機密情報候補を検出する."""

    @property
    def engine_version(self) -> str:
        """固定済みの検出エンジンバージョンを返す."""
        ...

    def scan(self, payload: SourcePayload, profile: ScanProfile) -> ScanResult:
        """``payload`` の検査結果を返す."""
        ...

    def scan_and_seal(self, payload: SourcePayload) -> tuple[ScanResult, ScannedPayload | None]:
        """``payload`` を検査し、合格した場合は封印する."""
        ...
