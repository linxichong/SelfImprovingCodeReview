"""レビュー結果の JSON およびターミナル出力."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from self_improving_code_review.application.review.models import ReviewResult
from self_improving_code_review.domain.finding.models import Finding

__all__ = ["result_to_document", "write_document", "write_error", "write_result"]


def result_to_document(result: ReviewResult) -> dict[str, Any]:
    """レビュー結果の公開用 JSON 文書を返す."""
    document = {
        "schema_version": 1,
        "run": {
            "run_id": str(result.run.run_id),
            "project_id": str(result.run.project_id),
            "mode": result.run.mode.value,
            "status": result.run.status.value,
            "source_revision": result.run.source_revision,
            "model": result.run.model,
            "started_at": result.run.started_at.isoformat(),
            "finished_at": (result.run.finished_at.isoformat() if result.run.finished_at else None),
            "versions": {
                "package": result.run.versions.package_version,
                "config_hash": result.run.versions.config_hash,
                "prompt_version": result.run.versions.prompt_version,
                "prompt_hash": result.run.versions.prompt_hash,
                "rule_snapshot_id": result.run.versions.rule_snapshot_id,
                "rule_set_hash": result.run.versions.rule_set_hash,
                "secret_engine_version": result.run.versions.secret_engine_version,
                "memory_snapshot_id": result.run.versions.memory_snapshot_id,
                "agent_prompt_version": result.run.versions.agent_prompt_version,
                "agent_prompt_hash": result.run.versions.agent_prompt_hash,
                "reflection_prompt_version": result.run.versions.reflection_prompt_version,
                "reflection_prompt_hash": result.run.versions.reflection_prompt_hash,
            },
        },
        "findings": [_finding_document(item) for item in result.main_findings],
        "reference_findings": [_finding_document(item) for item in result.reference_findings],
        "filtered_files": [
            {
                "file": item.path.value,
                "decision": item.decision.value,
                "reason": item.reason.value if item.reason else None,
            }
            for item in result.filtered_files
        ],
        "metrics": {
            "input_tokens": result.metrics.input_tokens,
            "output_tokens": result.metrics.output_tokens,
            "latency_ms": result.metrics.latency_ms,
            "cost_usd": str(result.metrics.cost_usd),
            "candidate_count": result.metrics.candidate_count,
            "accepted_candidate_count": result.metrics.accepted_candidate_count,
            "rejected_candidate_count": result.metrics.rejected_candidate_count,
            "rejection_reasons": dict(result.metrics.rejection_reason_counts),
            "main_finding_count": result.metrics.main_finding_count,
            "reference_finding_count": result.metrics.reference_finding_count,
            "invalid_position_count": result.metrics.invalid_position_count,
            "invalid_evidence_count": result.metrics.invalid_evidence_count,
            "duplicate_count": result.metrics.duplicate_count,
            "bundle_count": result.metrics.bundle_count,
            "failed_bundle_count": result.metrics.failed_bundle_count,
            "validation_pass_rate": result.metrics.validation_pass_rate,
        },
        "errors": [
            {
                "bundle_id": str(item.bundle_id),
                "code": item.error_code,
                "message": item.error_message,
            }
            for item in result.bundle_outcomes
            if item.error_code is not None
        ],
        "secret_findings": [
            {
                "detector": item.detector,
                "file": item.path,
                "line": item.line_number,
                "line_side": item.line_side,
            }
            for item in result.secret_findings
        ],
    }
    if result.metrics.is_m3_enabled:
        document["pipeline_metrics"] = {
            "tool_call_count": result.metrics.tool_call_count,
            "tool_success_count": result.metrics.tool_success_count,
            "tool_latency_ms": result.metrics.tool_latency_ms,
            "tool_reason_counts": dict(result.metrics.tool_reason_counts),
            "dynamic_context_tokens": result.metrics.dynamic_context_tokens,
            "reflection_call_count": result.metrics.reflection_call_count,
            "reflection_supported_count": result.metrics.reflection_supported_count,
            "reflection_contradicted_count": result.metrics.reflection_contradicted_count,
            "reflection_needs_context_count": result.metrics.reflection_needs_context_count,
            "resumed_bundle_count": result.metrics.resumed_bundle_count,
            "reexecuted_bundle_count": result.metrics.reexecuted_bundle_count,
        }
    if result.metrics.reviewer_profile_metrics:
        document["schema_version"] = 2
        document["reviewer_metrics"] = [
            {
                "profile": item.reviewer_type.value,
                "input_tokens": item.input_tokens,
                "output_tokens": item.output_tokens,
                "latency_ms": item.latency_ms,
                "cost_usd": str(item.cost_usd),
                "valid_finding_count": item.valid_finding_count,
                "duplicate_count": item.duplicate_count,
            }
            for item in result.metrics.reviewer_profile_metrics
        ]
    return document


def write_result(result: ReviewResult, output_path: Path | None) -> None:
    """結果をファイルへアトミックに書き込むか、標準出力へ表示する."""
    write_document(result_to_document(result), output_path)


def write_document(document: dict[str, Any], output_path: Path | None) -> None:
    """任意の公開 JSON 文書をアトミックに保存するか標準出力へ表示する."""
    rendered = json.dumps(document, ensure_ascii=False, indent=2)
    if output_path is None:
        print(rendered)
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    try:
        temporary.write_text(rendered + "\n", encoding="utf-8")
        temporary.replace(output_path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def write_error(code: str, message: str) -> None:
    """安定した形式のエラーオブジェクトを標準エラー出力へ書き込む."""
    print(
        json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False),
        file=sys.stderr,
    )


def _finding_document(finding: Finding) -> dict[str, Any]:
    """1件の公開用指摘文書を返す."""
    candidate = finding.candidate
    return {
        "id": str(finding.finding_id),
        "bundle_id": str(finding.bundle_id),
        "file": candidate.file.value,
        "start_line": candidate.line_range.start_line,
        "end_line": candidate.line_range.end_line,
        "category": candidate.category,
        "issue_type": candidate.issue_type,
        "rule_id": candidate.rule_id,
        "primary_entity": candidate.primary_entity,
        "severity": candidate.severity.value,
        "title": candidate.title,
        "description": candidate.description,
        "evidence": {
            "quote": candidate.evidence.quote,
            "reason": candidate.evidence.reason,
        },
        "suggestion": candidate.suggestion,
        "fingerprint": finding.fingerprint,
        "enclosing_symbol": finding.enclosing_symbol,
        "validation": {
            "position": finding.validation.position_status.value,
            "evidence": finding.validation.evidence_status.value,
            "reflection": finding.validation.reflection_status.value,
            "reason_codes": list(finding.validation.reason_codes),
        },
    }
