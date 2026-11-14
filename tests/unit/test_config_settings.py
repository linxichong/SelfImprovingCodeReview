from __future__ import annotations

import re
from pathlib import Path

import pytest

from self_improving_code_review.application.config.settings import (
    API_KEY_ENV_VAR,
    SCM_TOKEN_ENV_VAR,
    AppConfig,
    LlmConfig,
    load_config,
    read_api_key,
    read_scm_token,
)
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.messages import Messages


@pytest.mark.case("UT-M1-001")
def test_defaults_are_applied_when_no_file_or_environment_is_given() -> None:
    config = load_config(None, environ={})

    assert config.schema_version == 1
    assert config.project_id == "default-project"
    assert config.database.path == Path(".review/review.db")
    assert config.llm.provider == "ai_gateway"
    assert config.llm.base_url == "https://aigw.ldjp-ai.link"
    assert config.llm.backend_provider is None
    assert config.llm.model_family is None
    assert config.llm.temperature is None
    assert config.review.mode == "rules"
    assert config.review.rules_directory == Path("config/rules")
    assert not config.security.allow_external_source
    assert not config.scm.enabled
    assert not config.scm.publish_enabled
    assert config.scm.api_base_url == "https://api.github.com"


@pytest.mark.case("UT-M1-001")
def test_empty_configuration_file_falls_back_to_defaults(tmp_path: Path) -> None:
    config_file = tmp_path / "empty.yaml"
    config_file.write_text("", encoding="utf-8")

    config = load_config(config_file, environ={})

    assert config.project_id == "default-project"


@pytest.mark.case("UT-M1-001")
def test_missing_configuration_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvalidInputError, match=re.escape(Messages.CONFIG_FILE_READ_FAILED)):
        load_config(tmp_path / "missing.yaml", environ={})


@pytest.mark.case("UT-M1-001")
def test_non_utf8_configuration_file_is_rejected(tmp_path: Path) -> None:
    config_file = tmp_path / "binary.yaml"
    config_file.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(InvalidInputError, match=re.escape(Messages.CONFIG_FILE_READ_FAILED)):
        load_config(config_file, environ={})


@pytest.mark.case("UT-M1-001")
def test_malformed_yaml_is_rejected(tmp_path: Path) -> None:
    config_file = tmp_path / "broken.yaml"
    config_file.write_text("llm: [unterminated\n", encoding="utf-8")

    with pytest.raises(InvalidInputError, match=re.escape(Messages.CONFIG_FILE_YAML_INVALID)):
        load_config(config_file, environ={})


@pytest.mark.case("UT-M1-001")
def test_non_mapping_document_is_rejected(tmp_path: Path) -> None:
    config_file = tmp_path / "sequence.yaml"
    config_file.write_text("- not-a-mapping\n", encoding="utf-8")

    with pytest.raises(InvalidInputError, match=re.escape(Messages.CONFIG_FILE_NOT_MAPPING)):
        load_config(config_file, environ={})


@pytest.mark.case("UT-M1-001")
@pytest.mark.parametrize(
    "credential_field",
    ["api_key", "apikey", "token", "password", "secret"],
)
def test_credential_fields_in_the_configuration_are_rejected(
    tmp_path: Path,
    credential_field: str,
) -> None:
    config_file = tmp_path / "with_secret.yaml"
    config_file.write_text(f"llm:\n  {credential_field}: value\n", encoding="utf-8")

    with pytest.raises(InvalidInputError, match=API_KEY_ENV_VAR):
        load_config(config_file, environ={})


@pytest.mark.case("UT-M1-001")
def test_non_mapping_llm_section_skips_the_credential_check(tmp_path: Path) -> None:
    config_file = tmp_path / "invalid_llm.yaml"
    config_file.write_text("llm: not-a-mapping\n", encoding="utf-8")

    with pytest.raises(InvalidInputError, match=re.escape(Messages.CONFIG_INVALID)):
        load_config(config_file, environ={})


@pytest.mark.case("UT-M1-001", "UT-M4-001")
@pytest.mark.parametrize(
    "credential_field",
    ["api_key", "apikey", "token", "password", "secret"],
)
def test_scm_credentials_in_configuration_are_rejected(
    tmp_path: Path,
    credential_field: str,
) -> None:
    config_file = tmp_path / "with_scm_secret.yaml"
    config_file.write_text(f"scm:\n  {credential_field}: value\n", encoding="utf-8")

    with pytest.raises(InvalidInputError, match=SCM_TOKEN_ENV_VAR):
        load_config(config_file, environ={})


@pytest.mark.case("UT-M1-001")
def test_environment_overrides_every_supported_field(tmp_path: Path) -> None:
    config = load_config(
        None,
        environ={
            "REVIEW_PROJECT_ID": "env-project",
            "REVIEW_DATABASE_PATH": ".review/env.db",
            "REVIEW_LLM_PROVIDER": "dataset",
            "REVIEW_LLM_DATASET_PATH": "datasets/test/review_cases.json",
            "REVIEW_LLM_MODEL": "claude-env-v1",
            "REVIEW_LLM_BASE_URL": "https://example.invalid",
            "REVIEW_REVIEW_MODE": "baseline",
        },
    )

    assert config.project_id == "env-project"
    assert config.database.path == Path(".review/env.db")
    assert config.llm.provider == "dataset"
    assert config.llm.dataset_path == Path("datasets/test/review_cases.json")
    assert config.llm.model == "claude-env-v1"
    assert config.llm.base_url == "https://example.invalid"
    assert config.review.mode == "baseline"


@pytest.mark.case("UT-M1-001")
def test_blank_environment_values_do_not_override_configured_values(tmp_path: Path) -> None:
    config_file = tmp_path / "review.yaml"
    config_file.write_text("project_id: from-file\n", encoding="utf-8")

    config = load_config(config_file, environ={"REVIEW_PROJECT_ID": "   "})

    assert config.project_id == "from-file"


@pytest.mark.case("UT-M1-001")
def test_environment_override_of_a_non_mapping_section_is_ignored(tmp_path: Path) -> None:
    config_file = tmp_path / "review.yaml"
    config_file.write_text("database: not-a-mapping\n", encoding="utf-8")

    with pytest.raises(InvalidInputError, match=re.escape(Messages.CONFIG_INVALID)):
        load_config(config_file, environ={"REVIEW_DATABASE_PATH": ".review/env.db"})


@pytest.mark.case("UT-M1-001")
@pytest.mark.parametrize(
    "content",
    [
        "review:\n  bundle_diff_tokens: 100\n",
        "review:\n  max_output_tokens: 10\n",
        "review:\n  max_findings_per_bundle: 0\n",
        "review:\n  max_findings_per_bundle: 101\n",
        "review:\n  mode: unknown\n",
        "llm:\n  timeout_seconds: 0\n",
        "llm:\n  max_retries: 6\n",
        "llm:\n  temperature: -0.1\n",
        "llm:\n  temperature: 2.1\n",
        "llm:\n  provider: openai\n",
        "scm:\n  enabled: true\n",
        "scm:\n  api_base_url: http://api.github.com\n",
        "scm:\n  api_base_url: https://api.example.com\n",
        "scm:\n  api_base_url: https://user@api.github.com\n",
        "scm:\n  api_base_url: https://api.github.com?token=value\n",
        "scm:\n  api_base_url: https://api.github.com#fragment\n",
        "scm:\n  max_inline_comments: 0\n",
        "scm:\n  request_timeout_seconds: 121\n",
        "scm:\n  max_response_bytes: 1023\n",
        "scm:\n  max_response_bytes: 20971521\n",
        "scm:\n  max_retries: 6\n",
        (
            "scm:\n"
            "  enabled: true\n"
            "  repository_external_id: owner/repository\n"
            "  bot_actor_external_id: '  '\n"
        ),
    ],
)
def test_out_of_range_values_are_rejected(tmp_path: Path, content: str) -> None:
    config_file = tmp_path / "invalid.yaml"
    config_file.write_text(content, encoding="utf-8")

    with pytest.raises(InvalidInputError, match=re.escape(Messages.CONFIG_INVALID)):
        load_config(config_file, environ={})


@pytest.mark.case("UT-M1-001")
@pytest.mark.parametrize(
    ("model", "expected_is_resolved"),
    [
        ("claude-fixed-v1", True),
        ("unresolved", False),
        ("latest", False),
        ("LATEST", False),
        ("  ", False),
    ],
)
def test_gateway_model_resolution_check(model: str, expected_is_resolved: bool) -> None:
    assert LlmConfig(model=model).is_resolved is expected_is_resolved


@pytest.mark.case("UT-M1-001")
@pytest.mark.parametrize(
    ("dataset_path", "expected_is_resolved"),
    [(Path("datasets/test/review_cases.json"), True), (None, False)],
)
def test_dataset_resolution_requires_a_dataset_path(
    dataset_path: Path | None,
    expected_is_resolved: bool,
) -> None:
    config = LlmConfig(provider="dataset", dataset_path=dataset_path)

    assert config.is_resolved is expected_is_resolved


@pytest.mark.case("UT-M1-001")
def test_content_hash_is_stable_and_sensitive_to_changes() -> None:
    first = load_config(None, environ={})
    second = load_config(None, environ={})
    changed = load_config(None, environ={"REVIEW_PROJECT_ID": "other"})

    assert first.content_hash == second.content_hash
    assert first.content_hash != changed.content_hash


@pytest.mark.case("UT-M1-001")
def test_unsupported_schema_version_is_rejected(tmp_path: Path) -> None:
    config_file = tmp_path / "future.yaml"
    config_file.write_text("schema_version: 99\n", encoding="utf-8")

    with pytest.raises(
        InvalidInputError, match=re.escape(Messages.CONFIG_SCHEMA_VERSION_UNSUPPORTED)
    ):
        load_config(config_file, environ={})


@pytest.mark.case("UT-M1-001")
def test_configuration_is_frozen_and_rejects_unknown_fields() -> None:
    config = AppConfig()

    with pytest.raises(Exception, match=r"frozen|validation"):
        config.project_id = "changed"  # type: ignore[misc]


@pytest.mark.case("UT-M1-001")
@pytest.mark.parametrize(
    ("environment", "expected_key"),
    [
        ({API_KEY_ENV_VAR: "  credential  "}, "credential"),
        ({API_KEY_ENV_VAR: "   "}, None),
        ({}, None),
    ],
)
def test_api_key_is_read_only_from_the_environment(
    environment: dict[str, str],
    expected_key: str | None,
) -> None:
    assert read_api_key(environment) == expected_key


@pytest.mark.case("UT-M1-001", "UT-M4-001")
@pytest.mark.parametrize(
    ("environment", "expected_token"),
    [
        ({SCM_TOKEN_ENV_VAR: "  scm-credential  "}, "scm-credential"),
        ({SCM_TOKEN_ENV_VAR: "   "}, None),
        ({}, None),
    ],
)
def test_scm_token_is_read_only_from_the_environment(
    environment: dict[str, str],
    expected_token: str | None,
) -> None:
    assert read_scm_token(environment) == expected_token


@pytest.mark.case("UT-M1-001", "UT-M4-001")
def test_enabled_scm_accepts_complete_identifiers(tmp_path: Path) -> None:
    config_file = tmp_path / "scm.yaml"
    config_file.write_text(
        "scm:\n"
        "  enabled: true\n"
        "  repository_external_id: owner/repository\n"
        "  bot_actor_external_id: review-bot\n",
        encoding="utf-8",
    )

    config = load_config(config_file, environ={})

    assert config.scm.enabled
    assert config.scm.repository_external_id == "owner/repository"


@pytest.mark.case("UT-M1-001", "UT-M4-001")
def test_m4_github_example_configuration_is_valid() -> None:
    repository = Path(__file__).parents[2]

    config = load_config(repository / "config/m4.github.example.yaml", environ={})

    assert not config.scm.enabled
    assert config.scm.provider == "github"
    assert config.scm.feedback_actor_mapping == {}
