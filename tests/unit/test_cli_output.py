from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from self_improving_code_review.application.review.models import (
    BundleOutcome,
    ReviewMetrics,
    ReviewResult,
    RunRecord,
    RunVersions,
)
from self_improving_code_review.cli.output import result_to_document, write_error, write_result
from self_improving_code_review.domain.change.file_filter import FileFilterResult
from self_improving_code_review.domain.common.enums import (
    BundleStatus,
    FileDecision,
    FilterReason,
    ReflectionStatus,
    ReviewMode,
    RunStatus,
    Severity,
    ValidationStatus,
)
from self_improving_code_review.domain.common.values import (
    BundleId,
    FindingId,
    LineRange,
    NormalizedPath,
    ProjectId,
    RunId,
)
from self_improving_code_review.domain.finding.models import (
    CandidateFinding,
    Evidence,
    Finding,
    ValidationOutcome,
)
from self_improving_code_review.security.models import SecretFinding

RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")
BUNDLE_ID = BundleId(run_id=RUN_ID, sequence=1)
_RENAME_FAILED = "rename failed"


@pytest.mark.case("UT-M1-014")
def test_document_exposes_the_designed_top_level_keys() -> None:
    document = result_to_document(_result())

    assert set(document) == {
        "schema_version",
        "run",
        "findings",
        "reference_findings",
        "filtered_files",
        "metrics",
        "errors",
        "secret_findings",
    }
    assert document["schema_version"] == 1


@pytest.mark.case("UT-M1-014")
def test_run_section_reports_reproduction_metadata() -> None:
    run = result_to_document(_result())["run"]

    assert run["run_id"] == str(RUN_ID)
    assert run["project_id"] == "test-project"
    assert run["mode"] == "rules"
    assert run["status"] == "success"
    assert run["source_revision"] == "a" * 40
    assert run["model"] == "claude-fixed-v1"
    assert run["finished_at"] == "2026-01-02T03:05:00+00:00"
    assert run["versions"]["rule_set_hash"] == "rule-set-hash"
    assert run["versions"]["prompt_version"] == "review_general_v1@1"
    assert run["versions"]["memory_snapshot_id"] == "memory-snapshot-id"


@pytest.mark.case("UT-M1-014")
def test_unfinished_run_reports_a_null_finish_time() -> None:
    result = _result(finished_at=None)

    assert result_to_document(result)["run"]["finished_at"] is None


@pytest.mark.case("UT-M1-014")
def test_filtered_files_and_errors_are_rendered_with_reason_codes() -> None:
    document = result_to_document(_result())

    assert document["filtered_files"] == [
        {"file": "uv.lock", "decision": "filter", "reason": "lock-file"},
        {"file": "docs/readme.md", "decision": "lightweight", "reason": None},
    ]
    assert document["errors"] == [
        {"bundle_id": "B001", "code": "LlmClientError", "message": "transient failure"}
    ]


@pytest.mark.case("UT-M1-014")
def test_secret_findings_expose_only_locations() -> None:
    document = result_to_document(_result())

    assert document["secret_findings"] == [
        {"detector": "TestSecret", "file": "src/a.py", "line": 2, "line_side": "new"}
    ]


@pytest.mark.case("UT-M1-014")
def test_metrics_are_serialized_with_string_cost() -> None:
    metrics = result_to_document(_result())["metrics"]

    assert metrics["cost_usd"] == "0.02"
    assert metrics["bundle_count"] == 1
    assert metrics["accepted_candidate_count"] == 0
    assert metrics["rejected_candidate_count"] == 0
    assert metrics["rejection_reasons"] == {}
    assert metrics["validation_pass_rate"] == 0.0
    assert "pipeline_metrics" not in result_to_document(_result())


@pytest.mark.case("UT-M1-014")
def test_m3_output_adds_pipeline_metrics_and_prompt_versions() -> None:
    result = _result()
    result = replace(
        result,
        run=replace(
            result.run,
            versions=replace(
                result.run.versions,
                agent_prompt_version="review_agent_v1@2",
                agent_prompt_hash="agent-hash",
                reflection_prompt_version="reflection_v1@2",
                reflection_prompt_hash="reflection-hash",
            ),
        ),
        metrics=replace(
            result.metrics,
            tool_call_count=3,
            tool_success_count=1,
            tool_latency_ms=45,
            tool_reason_counts=(("NOT_FOUND", 2), ("SUCCESS", 1)),
            dynamic_context_tokens=80,
            reflection_call_count=2,
            reflection_supported_count=1,
            reflection_contradicted_count=1,
            reflection_needs_context_count=0,
            resumed_bundle_count=1,
            reexecuted_bundle_count=2,
            is_m3_enabled=True,
        ),
    )

    document = result_to_document(result)

    assert document["run"]["versions"]["agent_prompt_version"] == "review_agent_v1@2"
    assert document["run"]["versions"]["reflection_prompt_hash"] == "reflection-hash"
    assert document["pipeline_metrics"] == {
        "tool_call_count": 3,
        "tool_success_count": 1,
        "tool_latency_ms": 45,
        "tool_reason_counts": {"NOT_FOUND": 2, "SUCCESS": 1},
        "dynamic_context_tokens": 80,
        "reflection_call_count": 2,
        "reflection_supported_count": 1,
        "reflection_contradicted_count": 1,
        "reflection_needs_context_count": 0,
        "resumed_bundle_count": 1,
        "reexecuted_bundle_count": 2,
    }
    assert "pipeline_metrics" not in document["metrics"]


@pytest.mark.case("UT-M1-014")
def test_findings_are_rendered_with_validation_and_fingerprint() -> None:
    finding = _finding()
    document = result_to_document(_result(main_findings=(finding,)))

    rendered = document["findings"][0]
    assert rendered["id"] == str(finding.finding_id)
    assert rendered["bundle_id"] == "B001"
    assert rendered["file"] == "src/app.py"
    assert rendered["start_line"] == 2
    assert rendered["end_line"] == 2
    assert rendered["severity"] == "high"
    assert rendered["evidence"] == {
        "quote": "user.name",
        "reason": "None reaches this line.",
    }
    assert rendered["fingerprint"] == finding.fingerprint
    assert rendered["enclosing_symbol"] == "load_user"
    assert rendered["validation"]["position"] == "valid"
    assert rendered["validation"]["reflection"] == "reflection-skipped"


@pytest.mark.case("UT-M1-014")
def test_reference_findings_use_the_same_document_shape() -> None:
    document = result_to_document(_result(reference_findings=(_finding(),)))

    assert document["findings"] == []
    assert document["reference_findings"][0]["file"] == "src/app.py"


@pytest.mark.case("UT-M1-014")
def test_result_is_printed_when_no_output_path_is_given(
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_result(_result(), None)

    printed = json.loads(capsys.readouterr().out)
    assert printed["run"]["run_id"] == str(RUN_ID)


@pytest.mark.case("IT-M1-012", "UT-M1-014")
def test_result_file_is_written_atomically(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "result.json"

    write_result(_result(), output)

    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == 1
    assert not output.with_name(".result.json.tmp").exists()


@pytest.mark.case("IT-M1-012", "UT-M1-014")
def test_temporary_file_is_removed_when_the_rename_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.json"

    def fail(self: Path, _target: Path) -> Path:
        raise OSError(_RENAME_FAILED)

    monkeypatch.setattr(Path, "replace", fail)

    with pytest.raises(OSError, match="rename failed"):
        write_result(_result(), output)

    assert not output.exists()
    assert not output.with_name(".result.json.tmp").exists()


@pytest.mark.case("UT-M1-014")
def test_error_is_written_to_standard_error(capsys: pytest.CaptureFixture[str]) -> None:
    write_error("InvalidInputError", "bad input")

    captured = capsys.readouterr()
    assert json.loads(captured.err) == {
        "error": {"code": "InvalidInputError", "message": "bad input"}
    }
    assert captured.out == ""


def _finding() -> Finding:
    candidate = CandidateFinding(
        file=NormalizedPath("src/app.py"),
        line_range=LineRange(2, 2),
        category="correctness",
        issue_type="null-dereference",
        rule_id="PYTHON.NULL_DEREFERENCE",
        primary_entity="user",
        severity=Severity.HIGH,
        title="Guard nullable user",
        description="The value can be None.",
        evidence=Evidence(quote="user.name", reason="None reaches this line."),
        suggestion="Return before dereferencing user.",
        needs_context=False,
    )
    return Finding(
        finding_id=FindingId(bundle_id=BUNDLE_ID, sequence=1),
        bundle_id=BUNDLE_ID,
        candidate=candidate,
        fingerprint="PYTHON.NULL_DEREFERENCE:src/app.py:load_user:user",
        validation=ValidationOutcome(
            position_status=ValidationStatus.VALID,
            evidence_status=ValidationStatus.VALID,
            reflection_status=ReflectionStatus.REFLECTION_SKIPPED,
        ),
        enclosing_symbol="load_user",
    )


def _result(
    *,
    finished_at: datetime | None = datetime(2026, 1, 2, 3, 5, tzinfo=UTC),
    main_findings: tuple[Finding, ...] = (),
    reference_findings: tuple[Finding, ...] = (),
) -> ReviewResult:
    run = RunRecord(
        run_id=RUN_ID,
        project_id=ProjectId("test-project"),
        mode=ReviewMode.RULES,
        status=RunStatus.SUCCESS,
        source_revision="a" * 40,
        model="claude-fixed-v1",
        versions=RunVersions(
            package_version="0.1.0",
            config_hash="config-hash",
            prompt_version="review_general_v1@1",
            prompt_hash="prompt-hash",
            rule_snapshot_id="snapshot-id",
            rule_set_hash="rule-set-hash",
            is_rule_set_empty=False,
            secret_engine_version="1.5.0",
            memory_snapshot_id="memory-snapshot-id",
        ),
        idempotency_key="key",
        started_at=datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
        finished_at=finished_at,
    )
    return ReviewResult(
        run=run,
        main_findings=main_findings,
        reference_findings=reference_findings,
        filtered_files=(
            FileFilterResult(
                path=NormalizedPath("uv.lock"),
                decision=FileDecision.FILTER,
                reason=FilterReason.LOCK_FILE,
            ),
            FileFilterResult(
                path=NormalizedPath("docs/readme.md"),
                decision=FileDecision.LIGHTWEIGHT,
                reason=None,
            ),
        ),
        metrics=ReviewMetrics(
            input_tokens=100,
            output_tokens=20,
            latency_ms=5,
            cost_usd=Decimal("0.02"),
            candidate_count=0,
            main_finding_count=0,
            reference_finding_count=0,
            invalid_position_count=0,
            invalid_evidence_count=0,
            duplicate_count=0,
            bundle_count=1,
            failed_bundle_count=1,
        ),
        bundle_outcomes=(
            BundleOutcome(
                bundle_id=BUNDLE_ID,
                status=BundleStatus.FAILED,
                findings=(),
                error_code="LlmClientError",
                error_message="transient failure",
            ),
        ),
        secret_findings=(
            SecretFinding(
                detector="TestSecret",
                path="src/a.py",
                line_number=2,
                line_side="new",
            ),
        ),
    )
