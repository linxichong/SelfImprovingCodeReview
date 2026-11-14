from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path

import pytest
from litellm.exceptions import (
    APIConnectionError,
    APIError,
    APIResponseValidationError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

from self_improving_code_review.application.ports.llm_client import LlmRequest
from self_improving_code_review.domain.common.errors import (
    InvalidInputError,
    InvalidLlmOutputError,
    LlmClientError,
)
from self_improving_code_review.infrastructure.git.git_diff_adapter import GitDiffAdapter
from self_improving_code_review.infrastructure.llm.ai_gateway_client import AiGatewayLlmClient
from self_improving_code_review.infrastructure.persistence.sqlite import initialize_database
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.models import ScanProfile, SourceKind, SourcePayload
from self_improving_code_review.security.secret_scanner import (
    DetectSecretsScanner,
    build_strict_config,
)


def test_detect_secrets_strict_config_preserves_entropy_thresholds() -> None:
    plugins = {str(plugin["name"]): plugin for plugin in build_strict_config()["plugins_used"]}

    assert plugins["Base64HighEntropyString"]["limit"] == 4.5
    assert plugins["HexHighEntropyString"]["limit"] == 3.0


def test_repository_secret_baseline_matches_strict_scanner() -> None:
    """基準値の検出機能を製品の厳格検査と一致させ、全項目の監査を要求する。"""
    baseline_path = Path(__file__).parents[2] / ".secrets.baseline"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    assert baseline["version"] == "1.5.0"
    baseline_plugins = {item["name"]: item.get("limit") for item in baseline["plugins_used"]}
    strict_plugins = {
        item["name"]: item.get("limit") for item in build_strict_config()["plugins_used"]
    }
    assert baseline_plugins == strict_plugins

    findings = [
        finding for file_findings in baseline["results"].values() for finding in file_findings
    ]
    assert findings
    assert all(finding.get("is_secret") is False for finding in findings)


@pytest.mark.parametrize(
    "line",
    [
        "# SelfImprovingCodeReview",
        "hello world",
        "value = 1",
        "export REVIEW_LLM_API_KEY=placeholder",
        "LLM を利用したコードレビュー基盤です。",
    ],
)
def test_detect_secrets_strict_scan_ignores_benign_text(line: str) -> None:
    payload = SourcePayload(SourceKind.DIFF, "README.md", line)

    result = DetectSecretsScanner().scan(payload, ScanProfile.STRICT)

    assert result.is_passed


@pytest.mark.parametrize(
    "line",
    ['API_KEY_ENV_VAR = "REVIEW_LLM_API_KEY"', '+API_KEY_ENV_VAR = "REVIEW_LLM_API_KEY"'],
)
def test_detect_secrets_strict_scan_ignores_environment_variable_name_constant(
    line: str,
) -> None:
    payload = SourcePayload(
        SourceKind.DIFF,
        "settings.py",
        line,
    )

    result = DetectSecretsScanner().scan(payload, ScanProfile.STRICT)

    assert result.is_passed


@pytest.mark.parametrize(
    "line",
    ['DETECT_SECRETS_VERSION = "1.5.0"', '+DETECT_SECRETS_VERSION = "1.5.0"'],
)
def test_detect_secrets_strict_scan_ignores_version_metadata_constant(line: str) -> None:
    payload = SourcePayload(SourceKind.DIFF, "secret_scanner.py", line)

    result = DetectSecretsScanner().scan(payload, ScanProfile.STRICT)

    assert result.is_passed


@pytest.mark.case("UT-M1-012")
def test_detect_secrets_strict_scan_does_not_store_value() -> None:
    secret_value = "AKIAIOSFODNN7EXAMPLE"
    payload = SourcePayload(SourceKind.DIFF, "src/settings.py", f'key = "{secret_value}"')

    result = DetectSecretsScanner().scan(payload, ScanProfile.STRICT)

    assert not result.is_passed
    assert result.findings[0].path == "src/settings.py"
    assert secret_value not in repr(result)
    identities = {(item.detector, item.path, item.line_number) for item in result.findings}
    assert len(result.findings) == len(identities)


@pytest.mark.case("CT-M3-006", "UT-M1-012")
def test_gateway_retries_transient_status_and_parses_structured_output() -> None:
    calls = 0
    received: list[dict[str, object]] = []

    def completion(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        received.append(kwargs)
        if calls == 1:
            raise RateLimitError("limited", "openai", "claude-fixed-v1")
        return {
            "model": "claude-fixed-v1",
            "choices": [
                {"message": {"content": json.dumps({"schema_version": 1, "findings": []})}}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost_usd": "0.01"},
        }

    client = AiGatewayLlmClient(
        base_url="https://aigw.ldjp-ai.link",
        model="claude-fixed-v1",
        api_key="test-only",
        max_retries=1,
        completion_function=completion,
        sleeper=lambda _seconds: None,
    )

    result = client.generate_structured(_request())

    assert calls == 2
    assert result.payload == {"schema_version": 1, "findings": []}
    assert result.usage.input_tokens == 10
    assert received[0]["model"] == "openai/claude-fixed-v1"
    assert received[0]["base_url"] == "https://aigw.ldjp-ai.link/v1"
    assert received[0]["num_retries"] == 0
    assert "temperature" not in received[0]
    assert "response_format" not in received[0]
    assert "enable_json_schema_validation" not in received[0]


def test_gateway_forwards_an_explicit_temperature() -> None:
    received: list[dict[str, object]] = []

    def completion(**kwargs: object) -> object:
        received.append(kwargs)
        return {
            "choices": [{"message": {"content": '{"schema_version": 1, "findings": []}'}}],
            "usage": {},
        }

    client = AiGatewayLlmClient(
        base_url="https://aigw.ldjp-ai.link",
        model="gpt-5.6-luna",
        api_key="test-only",
        completion_function=completion,
    )

    client.generate_structured(_request(temperature=1.0))

    assert received[0]["temperature"] == 1.0


@pytest.mark.case("UT-M1-012")
@pytest.mark.parametrize(
    "exception_type",
    [
        APIConnectionError,
        Timeout,
        RateLimitError,
        InternalServerError,
        ServiceUnavailableError,
    ],
)
def test_gateway_maps_all_transient_litellm_errors_to_retryable_error(
    exception_type: type[Exception],
) -> None:
    calls = 0
    sleep_seconds: list[float] = []

    def completion(**_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise exception_type(
            "transient-secret-detail",
            llm_provider="openai",
            model="claude-fixed-v1",
        )

    client = AiGatewayLlmClient(
        base_url="https://aigw.ldjp-ai.link",
        model="claude-fixed-v1",
        api_key="test-only",
        max_retries=1,
        completion_function=completion,
        sleeper=sleep_seconds.append,
    )

    with pytest.raises(LlmClientError) as captured:
        client.generate_structured(_request())

    assert calls == 2
    assert sleep_seconds == [1.0]
    assert captured.value.is_retryable
    assert captured.value.attempt == 2
    assert "transient-secret-detail" not in str(captured.value)


def test_gateway_maps_response_validation_error_to_invalid_output() -> None:
    def completion(**_kwargs: object) -> object:
        raise APIResponseValidationError(
            "invalid-secret-detail",
            llm_provider="openai",
            model="claude-fixed-v1",
        )

    client = AiGatewayLlmClient(
        base_url="https://aigw.ldjp-ai.link",
        model="claude-fixed-v1",
        api_key="test-only",
        completion_function=completion,
    )

    with pytest.raises(InvalidLlmOutputError) as captured:
        client.generate_structured(_request())

    assert captured.value.attempt == 1
    assert "invalid-secret-detail" not in str(captured.value)


@pytest.mark.case("CT-M3-006", "UT-M1-012")
@pytest.mark.parametrize(
    "exception",
    [
        AuthenticationError(
            "authentication-secret-detail",
            llm_provider="openai",
            model="claude-fixed-v1",
        ),
        BadRequestError(
            "request-secret-detail",
            llm_provider="openai",
            model="claude-fixed-v1",
        ),
        APIError(
            500,
            "api-secret-detail",
            llm_provider="openai",
            model="claude-fixed-v1",
        ),
    ],
)
def test_gateway_maps_non_retryable_litellm_errors_without_retry(
    exception: Exception,
) -> None:
    calls = 0

    def completion(**_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise exception

    client = AiGatewayLlmClient(
        base_url="https://aigw.ldjp-ai.link",
        model="claude-fixed-v1",
        api_key="test-only",
        max_retries=2,
        completion_function=completion,
        sleeper=lambda _seconds: pytest.fail("再試行してはならない"),
    )

    with pytest.raises(LlmClientError) as captured:
        client.generate_structured(_request())

    assert calls == 1
    assert not captured.value.is_retryable
    assert captured.value.attempt == 1
    assert "secret-detail" not in str(captured.value)


@pytest.mark.case("CT-M3-006")
def test_gateway_preserves_usage_when_response_content_is_invalid() -> None:
    def completion(**_kwargs: object) -> object:
        return {
            "model": "claude-fixed-v1",
            "choices": [{"message": {"content": "not-json"}}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 3, "cost_usd": "0.02"},
        }

    client = AiGatewayLlmClient(
        base_url="https://aigw.ldjp-ai.link",
        model="claude-fixed-v1",
        api_key="test-only",
        completion_function=completion,
    )

    with pytest.raises(InvalidLlmOutputError) as captured:
        client.generate_structured(_request())

    assert captured.value.input_tokens == 8
    assert captured.value.output_tokens == 3
    assert captured.value.cost_usd == Decimal("0.02")


def test_gateway_requires_https_and_exact_model() -> None:
    with pytest.raises(InvalidInputError):
        AiGatewayLlmClient(base_url="http://gateway", model="latest", api_key="x")


@pytest.mark.parametrize(
    "content",
    [
        '~~~json\n{"schema_version": 1, "findings": []}\n~~~',
        'Here is the result:\n{"schema_version": 1, "findings": []}',
    ],
)
def test_gateway_extracts_one_json_object_from_wrapped_content(content: str) -> None:
    def completion(**_kwargs: object) -> object:
        return {
            "model": "claude-fixed-v1",
            "choices": [{"message": {"content": content}}],
            "usage": {},
        }

    client = AiGatewayLlmClient(
        base_url="https://aigw.ldjp-ai.link",
        model="claude-fixed-v1",
        api_key="test-only",
        completion_function=completion,
    )

    result = client.generate_structured(_request())

    assert result.payload == {"schema_version": 1, "findings": []}


def test_sqlite_schema_has_m1_tables(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    initialize_database(database)

    with closing(sqlite3.connect(database)) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    assert {
        "review_runs",
        "bundles",
        "bundle_files",
        "findings",
        "rule_snapshots",
        "rule_snapshot_items",
        "finding_rules",
        "llm_calls",
    } <= tables


def test_sql_migration_artifact_is_executable(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    migration = Path(__file__).parents[2] / "migrations" / "0001_m1.sql"
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(migration.read_text(encoding="utf-8"))
        version = connection.execute("SELECT version FROM schema_migrations").fetchone()[0]

    assert version == 1


def test_patch_file_source_is_bounded_and_utf8(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.patch"
    invalid.write_bytes(b"\xff")

    with pytest.raises(InvalidInputError, match=re.escape(Messages.PATCH_FILE_READ_FAILED)):
        GitDiffAdapter()._read_patch(invalid)


def _request(*, temperature: float | None = None) -> LlmRequest:
    return LlmRequest(
        system_instruction="system",
        user_content="review",
        output_schema={"type": "object"},
        max_output_tokens=256,
        temperature=temperature,
    )
