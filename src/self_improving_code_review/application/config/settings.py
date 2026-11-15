"""設定スキーマと読み込み.

優先順位は ``CLI > 環境変数 > プロジェクト設定 > 既定値`` とする. 機密情報は
環境変数からだけ読み込み、設定ファイルに API キーを保存してはならない.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.messages import Messages

__all__ = [
    "API_KEY_ENV_VAR",
    "OTEL_HEADERS_ENV_VAR",
    "SCM_TOKEN_ENV_VAR",
    "AppConfig",
    "DatabaseConfig",
    "GovernanceConfig",
    "LlmConfig",
    "MemoryConfig",
    "ReviewConfig",
    "ScmConfig",
    "SecurityConfig",
    "TelemetryConfig",
    "load_config",
    "read_api_key",
    "read_scm_token",
]

API_KEY_ENV_VAR = "REVIEW_LLM_API_KEY"
# 環境変数の識別名であり、認証情報そのものを保持しない。
SCM_TOKEN_ENV_VAR = "REVIEW_SCM_TOKEN"  # nosec B105
OTEL_HEADERS_ENV_VAR = "REVIEW_OTEL_EXPORTER_HEADERS"
_SCHEMA_VERSION = 1
_ENV_PREFIX = "REVIEW_"
_LEASE_MARGIN_SECONDS = 30


class _StrictModel(BaseModel):
    """未知のフィールドを拒否する基底モデル."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DatabaseConfig(_StrictModel):
    """SQLite の保存先."""

    path: Path = Path(".review/review.db")


class LlmConfig(_StrictModel):
    """LLM ゲートウェイ設定. API キーはこのモデルに含めない."""

    provider: Literal["ai_gateway", "dataset"] = "ai_gateway"
    dataset_path: Path | None = None
    base_url: str = "https://aigw.ldjp-ai.link"
    backend_provider: str | None = None
    model_family: str | None = None
    model: str = "unresolved"
    temperature: float | None = Field(default=None, ge=0, le=2)
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    max_retries: int = Field(default=2, ge=0, le=5)

    @property
    def is_resolved(self) -> bool:
        """この設定で TBD-01 が確定済みの場合は True を返す."""
        if self.provider == "dataset":
            return self.dataset_path is not None
        normalized_model = self.model.strip().lower()
        return bool(normalized_model) and normalized_model not in {"unresolved", "latest"}


class ReviewConfig(_StrictModel):
    """予算とモードの既定値."""

    mode: Literal["baseline", "rules", "memory"] = "rules"
    bundle_diff_tokens: int = Field(default=8000, ge=500)
    rule_tokens: int = Field(default=1500, ge=0)
    memory_tokens: int = Field(default=1200, ge=0)
    context_tools_enabled: bool = False
    reflection_enabled: bool = False
    max_workers: int = Field(default=1, ge=1, le=8)
    max_context_file_bytes: int = Field(default=1_048_576, ge=1, le=2_097_152)
    max_tool_results: int = Field(default=20, ge=1, le=20)
    dynamic_context_tokens: int = Field(default=6000, ge=0, le=32_000)
    reflection_tokens: int = Field(default=2000, ge=0, le=16_000)
    max_tool_calls: int = Field(default=5, ge=0, le=10)
    max_output_tokens: int = Field(default=4000, ge=256)
    max_findings_per_bundle: int = Field(default=20, ge=1, le=100)
    rules_directory: Path = Path("config/rules")

    @model_validator(mode="after")
    def validate_m3_budgets(self) -> ReviewConfig:
        """有効化した M3 処理に正の予算があることを検証する."""
        if self.context_tools_enabled and (
            self.dynamic_context_tokens < 1 or self.max_tool_calls < 1
        ):
            raise ValueError(Messages.CONFIG_CONTEXT_BUDGET_REQUIRED)
        if self.reflection_enabled and self.reflection_tokens < 256:
            raise ValueError(Messages.CONFIG_REFLECTION_BUDGET_REQUIRED)
        return self


class MemoryConfig(_StrictModel):
    """メモリ注入と統制の設定.

    TBD-06 が未解決のため、``maintainers`` はローカル開発環境の
    明示的な一覧に限定し、本番承認を有効化しない.
    """

    is_injection_enabled: bool = False
    maintainers: tuple[str, ...] = ()
    is_production: bool = False
    max_memories: int = Field(default=5, ge=1, le=20)
    per_memory_tokens: int = Field(default=200, ge=50)
    prompt_name: str = "memory_extraction_v1"
    extraction_max_output_tokens: int = Field(default=1200, ge=256)


class SecurityConfig(_StrictModel):
    """信頼境界の設定."""

    allow_external_source: bool = False
    blocked_paths: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()


class ScmConfig(_StrictModel):
    """SCM 変更要求の取得、公開およびフィードバック同期設定."""

    enabled: bool = False
    provider: Literal["github"] = "github"
    repository_external_id: str = ""
    api_base_url: str = "https://api.github.com"
    publish_enabled: bool = False
    feedback_sync_enabled: bool = False
    include_drafts: bool = False
    bot_actor_external_id: str = ""
    feedback_actor_mapping: dict[str, str] = Field(default_factory=dict)
    allowed_api_hosts: tuple[str, ...] = ("api.github.com",)
    max_inline_comments: int = Field(default=20, ge=1, le=100)
    request_timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_response_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024)
    max_retries: int = Field(default=2, ge=0, le=5)

    @model_validator(mode="after")
    def validate_security_boundary(self) -> ScmConfig:
        """SCM API の送信先と有効時の必須識別子を検証する."""
        parsed = urlsplit(self.api_base_url)
        hostname = (parsed.hostname or "").lower()
        allowed_hosts = {host.lower() for host in self.allowed_api_hosts}
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or hostname not in allowed_hosts
        ):
            raise ValueError(Messages.CONFIG_SCM_API_URL_INVALID)
        if self.enabled:
            repository_parts = self.repository_external_id.split("/")
            if len(repository_parts) != 2 or not all(part.strip() for part in repository_parts):
                raise ValueError(Messages.CONFIG_SCM_REPOSITORY_REQUIRED)
            if not self.bot_actor_external_id.strip():
                raise ValueError(Messages.CONFIG_SCM_ACTOR_REQUIRED)
        return self


class GovernanceConfig(_StrictModel):
    """M5 の統制、専門担当および実行リース設定."""

    enabled: bool = False
    memory_lifecycle_enabled: bool = False
    specialized_reviewers_enabled: bool = False
    execution_leases_enabled: bool = False
    max_reviewer_profiles: int = Field(default=2, ge=1, le=2)
    additional_reviewer_risk_threshold: Literal["low", "medium", "high"] = "high"
    specialist_total_tokens: int = Field(default=6000, ge=256, le=32_000)
    specialist_max_output_tokens: int = Field(default=3000, ge=256, le=16_000)
    revalidation_default_days: int = Field(default=180, ge=1, le=3650)
    lease_seconds: int = Field(default=180, ge=30, le=3600)
    heartbeat_seconds: int = Field(default=30, ge=1, le=1200)
    sqlite_busy_timeout_ms: int = Field(default=5000, ge=1, le=60_000)

    @model_validator(mode="after")
    def validate_feature_gates(self) -> GovernanceConfig:
        """親機能が無効な状態の部分有効化と不正なリース間隔を拒否する."""
        subfeatures = (
            self.memory_lifecycle_enabled,
            self.specialized_reviewers_enabled,
            self.execution_leases_enabled,
        )
        if not self.enabled and any(subfeatures):
            raise ValueError(Messages.CONFIG_GOVERNANCE_DISABLED)
        if self.heartbeat_seconds * 3 > self.lease_seconds:
            raise ValueError(Messages.CONFIG_GOVERNANCE_HEARTBEAT_INVALID)
        return self


class TelemetryConfig(_StrictModel):
    """機密情報を含めない M5 遠隔測定設定."""

    enabled: bool = False
    provider: Literal["noop", "opentelemetry"] = "noop"
    endpoint: str | None = None
    allowed_hosts: tuple[str, ...] = ()
    service_name: str = "self-improving-code-review"
    sample_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    export_timeout_seconds: int = Field(default=5, ge=1, le=60)
    max_queue_size: int = Field(default=2048, ge=1, le=65_536)

    @model_validator(mode="after")
    def validate_security_boundary(self) -> TelemetryConfig:
        """有効状態と送信先の HTTPS 許可一覧を検証する."""
        if not self.enabled:
            if self.provider != "noop" or self.endpoint is not None:
                raise ValueError(Messages.CONFIG_TELEMETRY_PROVIDER_MISMATCH)
            return self
        if self.provider != "opentelemetry" or self.endpoint is None:
            raise ValueError(Messages.CONFIG_TELEMETRY_PROVIDER_MISMATCH)
        parsed = urlsplit(self.endpoint)
        hostname = (parsed.hostname or "").lower()
        allowed_hosts = {host.lower() for host in self.allowed_hosts}
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or hostname not in allowed_hosts
        ):
            raise ValueError(Messages.CONFIG_TELEMETRY_ENDPOINT_INVALID)
        return self


class AppConfig(_StrictModel):
    """検証済みのアプリケーション設定."""

    schema_version: int = _SCHEMA_VERSION
    project_id: str = "default-project"
    database: DatabaseConfig = DatabaseConfig()
    llm: LlmConfig = LlmConfig()
    review: ReviewConfig = ReviewConfig()
    memory: MemoryConfig = MemoryConfig()
    security: SecurityConfig = SecurityConfig()
    scm: ScmConfig = ScmConfig()
    governance: GovernanceConfig = GovernanceConfig()
    telemetry: TelemetryConfig = TelemetryConfig()

    @model_validator(mode="after")
    def validate_lease_covers_external_calls(self) -> AppConfig:
        """実行リースが外部呼出し 1 回分より先に失効しないことを検証する."""
        required_seconds = (
            max(self.llm.timeout_seconds, self.scm.request_timeout_seconds) + _LEASE_MARGIN_SECONDS
        )
        if (
            self.governance.execution_leases_enabled
            and self.governance.lease_seconds < required_seconds
        ):
            raise ValueError(Messages.CONFIG_GOVERNANCE_LEASE_TOO_SHORT)
        return self

    @property
    def content_hash(self) -> str:
        """有効な設定の SHA-256 ダイジェストを返す.

        結果を生成した正確な設定と結果を関連付けられるよう、
        ダイジェストを実行に記録する.
        """
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hash_text(serialized)

    @property
    def pipeline_content_hash(self) -> str:
        """レビュー結果に影響する設定だけの SHA-256 ダイジェストを返す."""
        if not self.governance.enabled:
            return self.content_hash
        document = self.model_dump(mode="json")
        document.pop("telemetry", None)
        for name in (
            "execution_leases_enabled",
            "lease_seconds",
            "heartbeat_seconds",
            "sqlite_busy_timeout_ms",
        ):
            document["governance"].pop(name, None)
        document["scm"].pop("publish_enabled", None)
        serialized = json.dumps(document, sort_keys=True, separators=(",", ":"))
        return hash_text(serialized)


def load_config(config_path: Path | None, *, environ: Mapping[str, str] | None = None) -> AppConfig:
    """設定を読み込み、統合して検証する.

    Args:
        config_path: 任意のプロジェクト設定ファイル.
        environ: 環境変数の対応表. 既定値は :data:`os.environ`.

    Returns:
        検証済みの設定.

    Raises:
        InvalidInputError: ファイルを読み取れない、対応表ではない、
            未対応の ``schema_version`` を宣言している、未知フィールドを含む、
            API キーを埋め込んでいる場合.
    """
    env = os.environ if environ is None else environ
    document = _read_document(config_path)
    _reject_embedded_secret(document)
    _apply_environment(document, env)
    try:
        config = AppConfig.model_validate(document)
    except ValidationError as exc:
        message = f"{Messages.CONFIG_INVALID}: {exc.error_count()} 件"
        raise InvalidInputError(message) from exc
    if config.schema_version != _SCHEMA_VERSION:
        message = f"{Messages.CONFIG_SCHEMA_VERSION_UNSUPPORTED}: {config.schema_version}"
        raise InvalidInputError(message)
    return config


def read_api_key(environ: Mapping[str, str] | None = None) -> str | None:
    """環境変数から LLM API キーを返す。未設定の場合は None を返す."""
    env = os.environ if environ is None else environ
    value = env.get(API_KEY_ENV_VAR, "").strip()
    return value or None


def read_scm_token(environ: Mapping[str, str] | None = None) -> str | None:
    """環境変数から SCM 認証トークンを返す。未設定の場合は None を返す."""
    env = os.environ if environ is None else environ
    value = env.get(SCM_TOKEN_ENV_VAR, "").strip()
    return value or None


def _read_document(config_path: Path | None) -> dict[str, Any]:
    """未加工の設定マッピングを返す.

    Raises:
        InvalidInputError: ファイルを読み取れない、または YAML の対応表ではない場合.
    """
    if config_path is None:
        return {}
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        message = f"{Messages.CONFIG_FILE_READ_FAILED}: {config_path}: {exc}"
        raise InvalidInputError(message) from exc
    try:
        document = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        message = f"{Messages.CONFIG_FILE_YAML_INVALID}: {config_path}"
        raise InvalidInputError(message) from exc
    if document is None:
        return {}
    if not isinstance(document, dict):
        message = f"{Messages.CONFIG_FILE_NOT_MAPPING}: {config_path}"
        raise InvalidInputError(message)
    return document


def _reject_embedded_secret(document: dict[str, Any]) -> None:
    """設定ファイル内の認証情報に似たキーを拒否する.

    Raises:
        InvalidInputError: 認証情報フィールドが存在する場合.
    """
    for section_name, environment_name in (
        ("llm", API_KEY_ENV_VAR),
        ("scm", SCM_TOKEN_ENV_VAR),
        ("telemetry", OTEL_HEADERS_ENV_VAR),
    ):
        section = document.get(section_name)
        if not isinstance(section, dict):
            continue
        forbidden = {"api_key", "apikey", "token", "password", "secret"} & set(section)
        if forbidden:
            message = (
                f"{Messages.CONFIG_CREDENTIALS_EMBEDDED}: {sorted(forbidden)}。"
                f"{Messages.CONFIG_CREDENTIALS_ENV_HINT.format(name=environment_name)}"
            )
            raise InvalidInputError(message)


def _apply_environment(document: dict[str, Any], environ: Mapping[str, str]) -> None:
    """``REVIEW_*`` 環境変数による上書きを ``document`` に反映する."""
    for env_name, (section, field_name) in _ENV_OVERRIDES.items():
        raw_value = environ.get(f"{_ENV_PREFIX}{env_name}")
        if raw_value is None or not raw_value.strip():
            continue
        if section is None:
            document[field_name] = raw_value.strip()
            continue
        target = document.setdefault(section, {})
        if isinstance(target, dict):
            target[field_name] = raw_value.strip()


_ENV_OVERRIDES: dict[str, tuple[str | None, str]] = {
    "PROJECT_ID": (None, "project_id"),
    "DATABASE_PATH": ("database", "path"),
    "LLM_PROVIDER": ("llm", "provider"),
    "LLM_DATASET_PATH": ("llm", "dataset_path"),
    "LLM_MODEL": ("llm", "model"),
    "LLM_BASE_URL": ("llm", "base_url"),
    "REVIEW_MODE": ("review", "mode"),
}
