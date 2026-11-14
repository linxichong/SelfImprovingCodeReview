from __future__ import annotations

import re
from pathlib import Path

import pytest

from self_improving_code_review.application.config.settings import (
    GovernanceConfig,
    TelemetryConfig,
    load_config,
)
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.messages import Messages


@pytest.mark.case("UT-M5-009")
def test_m5_configuration_defaults_are_disabled() -> None:
    config = load_config(None, environ={})

    assert config.governance == GovernanceConfig()
    assert config.telemetry == TelemetryConfig()
    assert config.pipeline_content_hash == config.content_hash


@pytest.mark.case("UT-M5-009")
@pytest.mark.parametrize(
    "document",
    [
        "governance:\n  memory_lifecycle_enabled: true\n",
        "governance:\n  specialized_reviewers_enabled: true\n",
        "governance:\n  execution_leases_enabled: true\n",
        "governance:\n  heartbeat_seconds: 61\n  lease_seconds: 180\n",
        "telemetry:\n  provider: opentelemetry\n",
        "telemetry:\n  enabled: true\n",
        (
            "telemetry:\n"
            "  enabled: true\n"
            "  provider: opentelemetry\n"
            "  endpoint: http://collector.example.com/v1/traces\n"
            "  allowed_hosts: [collector.example.com]\n"
        ),
        (
            "telemetry:\n"
            "  enabled: true\n"
            "  provider: opentelemetry\n"
            "  endpoint: https://other.example.com/v1/traces\n"
            "  allowed_hosts: [collector.example.com]\n"
        ),
    ],
)
def test_invalid_m5_configuration_is_rejected(tmp_path: Path, document: str) -> None:
    config_file = tmp_path / "invalid.yaml"
    config_file.write_text(document, encoding="utf-8")

    with pytest.raises(InvalidInputError, match=re.escape(Messages.CONFIG_INVALID)):
        load_config(config_file, environ={})


_LEASE_DOCUMENT = (
    "llm:\n  timeout_seconds: {llm}\n"
    "scm:\n  request_timeout_seconds: {scm}\n"
    "governance:\n"
    "  enabled: {enabled}\n"
    "  execution_leases_enabled: {enabled}\n"
    "  lease_seconds: {lease}\n"
    "  heartbeat_seconds: 30\n"
)


@pytest.mark.case("UT-M5-009")
@pytest.mark.parametrize(
    ("llm", "scm", "lease", "enabled", "is_valid"),
    [
        (120, 30, 149, "true", False),
        (120, 30, 150, "true", True),
        (60, 100, 129, "true", False),
        (60, 100, 130, "true", True),
        (120, 30, 90, "false", True),
    ],
)
def test_lease_must_cover_longest_external_timeout(
    tmp_path: Path,
    llm: int,
    scm: int,
    lease: int,
    enabled: str,
    is_valid: bool,
) -> None:
    """有効なリースは LLM と SCM の長い方のタイムアウト + 30 秒以上を必須とする。"""
    config_file = tmp_path / "lease.yaml"
    config_file.write_text(
        _LEASE_DOCUMENT.format(llm=llm, scm=scm, lease=lease, enabled=enabled),
        encoding="utf-8",
    )

    if is_valid:
        assert load_config(config_file, environ={}).governance.lease_seconds == lease
        return
    with pytest.raises(InvalidInputError, match=re.escape(Messages.CONFIG_INVALID)):
        load_config(config_file, environ={})


@pytest.mark.case("UT-M5-009")
def test_enabled_telemetry_accepts_only_allowlisted_https_endpoint(tmp_path: Path) -> None:
    config_file = tmp_path / "telemetry.yaml"
    config_file.write_text(
        """telemetry:
  enabled: true
  provider: opentelemetry
  endpoint: https://collector.example.com/v1/traces
  allowed_hosts: [collector.example.com]
  sample_ratio: 0.5
""",
        encoding="utf-8",
    )

    config = load_config(config_file, environ={})

    assert config.telemetry.enabled
    assert config.telemetry.endpoint == "https://collector.example.com/v1/traces"


@pytest.mark.case("UT-M5-009")
def test_telemetry_credentials_in_configuration_are_rejected(tmp_path: Path) -> None:
    config_file = tmp_path / "secret.yaml"
    config_file.write_text("telemetry:\n  token: forbidden\n", encoding="utf-8")

    with pytest.raises(InvalidInputError, match="REVIEW_OTEL_EXPORTER_HEADERS"):
        load_config(config_file, environ={})


@pytest.mark.case("UT-M5-009")
def test_pipeline_hash_excludes_only_operational_m5_settings(tmp_path: Path) -> None:
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text(
        """governance:
  enabled: true
  execution_leases_enabled: true
  lease_seconds: 180
  heartbeat_seconds: 30
""",
        encoding="utf-8",
    )
    second.write_text(
        """governance:
  enabled: true
  execution_leases_enabled: false
  lease_seconds: 300
  heartbeat_seconds: 60
""",
        encoding="utf-8",
    )

    first_config = load_config(first, environ={})
    second_config = load_config(second, environ={})

    assert first_config.content_hash != second_config.content_hash
    assert first_config.pipeline_content_hash == second_config.pipeline_content_hash


@pytest.mark.case("UT-M5-009")
def test_pipeline_hash_changes_with_specialist_policy(tmp_path: Path) -> None:
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text("governance:\n  enabled: true\n", encoding="utf-8")
    second.write_text(
        "governance:\n  enabled: true\n  specialized_reviewers_enabled: true\n",
        encoding="utf-8",
    )

    assert (
        load_config(first, environ={}).pipeline_content_hash
        != load_config(second, environ={}).pipeline_content_hash
    )
