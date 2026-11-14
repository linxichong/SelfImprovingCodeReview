from __future__ import annotations

import pytest

from self_improving_code_review.security.models import ScanProfile, SourceKind, SourcePayload
from self_improving_code_review.security.secret_scanner import (
    DETECT_SECRETS_VERSION,
    DetectSecretsScanner,
)


@pytest.mark.case("UT-M1-012")
def test_engine_version_matches_the_pinned_dependency() -> None:
    assert DetectSecretsScanner().engine_version == DETECT_SECRETS_VERSION


@pytest.mark.case("UT-M1-012")
def test_configured_engine_version_is_recorded_in_the_result() -> None:
    scanner = DetectSecretsScanner(engine_version="test-engine-9")

    result = scanner.scan(
        SourcePayload(SourceKind.DIFF, "src/a.py", "value = 1"), ScanProfile.STRICT
    )

    assert scanner.engine_version == "test-engine-9"
    assert result.engine_version == "test-engine-9"
    assert result.profile is ScanProfile.STRICT


@pytest.mark.case("UT-M1-012")
def test_clean_payload_is_sealed_for_the_llm_call() -> None:
    payload = SourcePayload(SourceKind.DIFF, "bundle-diff", "+value = 1")

    result, sealed = DetectSecretsScanner().scan_and_seal(payload)

    assert result.is_passed
    assert sealed is not None
    assert sealed.payload_hash == payload.content_hash
    assert sealed.engine_version == result.engine_version
    assert sealed.scan_id == f"{payload.content_hash[:16]}-strict"
    assert sealed.matches(payload.text)


@pytest.mark.case("UT-M1-012")
def test_payload_with_a_secret_candidate_is_not_sealed() -> None:
    payload = SourcePayload(
        SourceKind.DIFF,
        "bundle-diff",
        '+key = "AKIAIOSFODNN7EXAMPLE"',
    )

    result, sealed = DetectSecretsScanner().scan_and_seal(payload)

    assert not result.is_passed
    assert sealed is None
    assert "AKIAIOSFODNN7EXAMPLE" not in repr(result)
