"""FN-03 で使用する detect-secrets アダプター.

製品内の検査処理はリポジトリのコミット前検査と同じエンジンバージョンおよび
プラグイン構成を使用するが、厳格なプロファイルを適用する. 基準値の結果、
行内の許可リストコメント、パス固有の抑制は適用しない.
検査はプロセスメモリ内で行い、候補値をどこにも書き込まない.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from detect_secrets.core.plugins.util import get_mapping_from_secret_type_to_class
from detect_secrets.settings import get_plugins, transient_settings

from self_improving_code_review.security.models import (
    ScannedPayload,
    ScanProfile,
    ScanResult,
    SecretFinding,
    SourcePayload,
)

__all__ = ["DETECT_SECRETS_VERSION", "DetectSecretsScanner", "build_strict_config"]

DETECT_SECRETS_VERSION = "1.5.0"
_ENV_VAR_REFERENCE = re.compile(
    r"^\s*[+-]?\s*[A-Z][A-Z0-9_]*_ENV_VAR\s*=\s*[\"'][A-Z][A-Z0-9_]*[\"']\s*$"
)
_VERSION_REFERENCE = re.compile(
    r"^\s*[+-]?\s*[A-Z][A-Z0-9_]*_VERSION\s*=\s*[\"']v?\d+(?:\.\d+){1,3}[\"']\s*$"
)


def build_strict_config() -> dict[str, Any]:
    """厳格な検査設定を返す.

    同梱された検出プラグインをすべて有効化し、フィルターを登録しないため、
    許可リストコメントやパスルールで候補が抑制されることはない.
    """
    plugin_classes: list[type[Any]] = list(get_mapping_from_secret_type_to_class().values())
    plugin_names = sorted({plugin_class.__name__ for plugin_class in plugin_classes})
    plugins_used: list[dict[str, Any]] = []
    for name in plugin_names:
        plugin_config: dict[str, Any] = {"name": name}
        # 高エントロピー検出プラグインには、一時設定内で明示的なしきい値が必要となる。
        # 省略すると上限がゼロで初期化され、実質的に空でないすべての文字列が
        # 機密情報として報告される。
        if name == "Base64HighEntropyString":
            plugin_config["limit"] = 4.5
        elif name == "HexHighEntropyString":
            plugin_config["limit"] = 3.0
        plugins_used.append(plugin_config)

    return {
        "plugins_used": plugins_used,
        "filters_used": [],
    }


@contextmanager
def _strict_settings() -> Iterator[None]:
    """検査中に厳格な detect-secrets 設定を有効化する."""
    with transient_settings(build_strict_config()):
        yield


class DetectSecretsScanner:
    """未信頼の送信内容がプロセス外へ出る前に機密情報候補を検査する."""

    def __init__(self, *, engine_version: str = DETECT_SECRETS_VERSION) -> None:
        """検査結果に記録する固定済みエンジンバージョンを保持する.

        Args:
            engine_version: 結果に記録し、契約テストでリポジトリ側の検査と
                比較するバージョン.
        """
        self._engine_version = engine_version

    @property
    def engine_version(self) -> str:
        """固定済みの detect-secrets バージョンを返す."""
        return self._engine_version

    def scan(self, payload: SourcePayload, profile: ScanProfile) -> ScanResult:
        """``payload`` を行単位で検査し、集約結果を返す.

        Args:
            payload: 信頼境界を越えようとしている未信頼テキスト.
            profile: 抑制プロファイル. M1 では ``STRICT`` のみ対応する.

        Returns:
            検出機能名と行番号だけを保持する検査結果.
        """
        findings: dict[tuple[str, str, int], SecretFinding] = {}
        with _strict_settings():
            for line_number, line in enumerate(payload.text.splitlines(), start=1):
                for plugin in get_plugins():
                    secrets = plugin.analyze_line(
                        filename=payload.label,
                        line=line,
                        line_number=line_number,
                    )
                    for secret in secrets:
                        if secret.type == "Secret Keyword" and (
                            _ENV_VAR_REFERENCE.match(line) or _VERSION_REFERENCE.match(line)
                        ):
                            continue
                        finding = SecretFinding(
                            detector=secret.type,
                            path=payload.label,
                            line_number=line_number,
                        )
                        key = (finding.detector, finding.path, finding.line_number)
                        findings.setdefault(key, finding)
        return ScanResult(
            payload_hash=payload.content_hash,
            engine_version=self._engine_version,
            profile=profile,
            findings=tuple(findings.values()),
        )

    def scan_and_seal(self, payload: SourcePayload) -> tuple[ScanResult, ScannedPayload | None]:
        """``payload`` を検査し、候補が検出されなければ封印する.

        Returns:
            検査結果と、合格した場合には後続処理が LLM 呼び出し前に提示すべき
            封印済み送信内容.
        """
        result = self.scan(payload, ScanProfile.STRICT)
        if not result.is_passed:
            return result, None
        sealed = ScannedPayload(
            scan_id=f"{result.payload_hash[:16]}-{ScanProfile.STRICT.value}",
            engine_version=result.engine_version,
            payload_hash=result.payload_hash,
            text=payload.text,
        )
        return result, sealed
