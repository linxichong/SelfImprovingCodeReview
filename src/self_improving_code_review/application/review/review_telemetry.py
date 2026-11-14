"""安全な属性と集約値だけでレビュー完了を観測する."""

from __future__ import annotations

from self_improving_code_review.application.ports.telemetry import Telemetry
from self_improving_code_review.application.review.models import (
    ReviewResult,
)
from self_improving_code_review.domain.common.enums import (
    RunStatus,
)


def record_run_metrics(result: ReviewResult, telemetry: Telemetry, *, provider: str) -> None:
    """実行結果と担当別集計を安全な遠隔測定値として記録する."""
    common = {
        "run_id": str(result.run.run_id),
        "project_id": str(result.run.project_id),
        "status": result.run.status.value,
        "model": result.run.model,
        "provider": provider,
        "prompt_version": result.run.versions.prompt_version,
    }
    telemetry.add_event("review.completed", common)
    telemetry.record_counter(
        "review.input_tokens",
        result.metrics.input_tokens,
        common,
    )
    telemetry.record_counter(
        "review.output_tokens",
        result.metrics.output_tokens,
        common,
    )
    telemetry.record_counter(
        "review.valid_findings",
        result.metrics.main_finding_count,
        common,
    )
    telemetry.record_counter(
        "review.blocked",
        int(result.run.status is RunStatus.BLOCKED),
        common,
    )
    telemetry.record_histogram(
        "review.duration_ms",
        result.metrics.latency_ms,
        common,
    )
    telemetry.record_histogram(
        "review.cost_usd",
        float(result.metrics.cost_usd),
        common,
    )
    for profile_metrics in result.metrics.reviewer_profile_metrics:
        profile = {
            **common,
            "reviewer_profile": profile_metrics.reviewer_type.value,
        }
        telemetry.record_counter(
            "reviewer.input_tokens",
            profile_metrics.input_tokens,
            profile,
        )
        telemetry.record_counter(
            "reviewer.output_tokens",
            profile_metrics.output_tokens,
            profile,
        )
        telemetry.record_counter(
            "reviewer.valid_findings",
            profile_metrics.valid_finding_count,
            profile,
        )
        telemetry.record_counter(
            "reviewer.duplicates",
            profile_metrics.duplicate_count,
            profile,
        )
        telemetry.record_histogram(
            "reviewer.duration_ms",
            profile_metrics.latency_ms,
            profile,
        )
        telemetry.record_histogram(
            "reviewer.cost_usd",
            float(profile_metrics.cost_usd),
            profile,
        )
