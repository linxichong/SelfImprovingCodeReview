"""採用済み結果と監査を既存の短時間トランザクション境界で保存する."""

from __future__ import annotations

from datetime import datetime

from self_improving_code_review.application.ports.clock import Clock
from self_improving_code_review.application.ports.run_lease import (
    RunLeaseSession,
)
from self_improving_code_review.application.ports.telemetry import Telemetry
from self_improving_code_review.application.ports.unit_of_work import (
    UnitOfWorkFactory,
)
from self_improving_code_review.application.review.bundle_review_models import (
    BundleReview,
)
from self_improving_code_review.application.review.models import (
    BundleOutcome,
    ReviewMetrics,
)
from self_improving_code_review.application.review.review_identity import (
    outcome_hash,
    result_hash,
)
from self_improving_code_review.domain.change.file_filter import (
    FileFilterResult,
)
from self_improving_code_review.domain.common.enums import (
    CheckpointStage,
    RunStatus,
)
from self_improving_code_review.domain.common.values import RunId
from self_improving_code_review.domain.memory.models import MemorySnapshot
from self_improving_code_review.domain.rule.models import RuleSnapshot


class ReviewResultWriter:
    """単位保存と最終確定を担当し、外部LLM呼出を行わない."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        clock: Clock,
        telemetry: Telemetry,
        *,
        is_specialized_review_enabled: bool,
    ) -> None:
        """既存の保存ポート、時刻、観測と担当監査の条件を保持する."""
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._telemetry = telemetry
        self._is_specialized_review_enabled = is_specialized_review_enabled

    def save_review(
        self,
        review: BundleReview,
        outcome: BundleOutcome,
        file_results: tuple[FileFilterResult, ...],
        snapshot: RuleSnapshot,
        run_id: RunId,
        *,
        is_m3_enabled: bool,
        is_blocked: bool,
        lease_session: RunLeaseSession,
    ) -> None:
        """採用指摘、呼出記録と監査を同一の単位トランザクションで保存する."""
        bundle = review.task.bundle
        plan = review.task.plan
        kept_finding_ids = {finding.finding_id for finding in outcome.findings}
        lease_session.assert_owned()
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.bundles.save(bundle, outcome, file_results)
            unit_of_work.findings.save_all(run_id, outcome.findings)
            unit_of_work.rule_snapshots.link_findings(snapshot, outcome.findings)
            if self._is_specialized_review_enabled:
                unit_of_work.reviewer_audit.save_executions(
                    run_id,
                    bundle.bundle_id,
                    review.reviewer_executions,
                )
                if not is_blocked:
                    unit_of_work.reviewer_audit.save_finding_sources(
                        run_id,
                        review.finding_sources,
                    )
            reflection_call_id: int | None = None
            for llm_record in review.llm_records:
                call_id = unit_of_work.llm_calls.save(llm_record)
                if llm_record.call_kind == "reflection":
                    reflection_call_id = call_id
            if is_m3_enabled:
                unit_of_work.m3_audit.save_tool_outcomes(
                    run_id,
                    bundle.bundle_id,
                    review.tool_outcomes,
                    self._clock.now_utc(),
                )
                unit_of_work.m3_audit.save_bundle_metadata(
                    bundle,
                    plan,
                    review.task.input_hash,
                    outcome_hash(outcome),
                )
                unit_of_work.m3_audit.save_context_sources(
                    run_id,
                    bundle.bundle_id,
                    review.source_fingerprints,
                )
                if reflection_call_id is not None and not is_blocked:
                    unit_of_work.m3_audit.save_reflections(
                        run_id,
                        tuple(
                            finding
                            for finding in review.reflected_findings
                            if finding.finding_id in kept_finding_ids
                        ),
                        reflection_call_id,
                    )
                unit_of_work.m3_audit.save_checkpoint(
                    run_id,
                    CheckpointStage.BUNDLE_COMPLETED.value,
                    review.task.input_hash,
                    self._clock.now_utc(),
                    bundle_id=bundle.bundle_id,
                    result_hash=outcome_hash(outcome),
                )

    def finalize_run(
        self,
        run_id: RunId,
        status: RunStatus,
        metrics: ReviewMetrics,
        outcomes: tuple[BundleOutcome, ...],
        finished_at: datetime,
        memory_snapshot: MemorySnapshot | None,
        *,
        is_m3_enabled: bool,
        lease_session: RunLeaseSession,
    ) -> None:
        """リース確認後に実行状態と最終チェックポイントを原子的に確定する."""
        span_attributes = {"run_id": str(run_id), "status": status.value}
        lease_session.assert_owned()
        with (
            self._telemetry.start_span("persistence.finalize", span_attributes),
            self._unit_of_work_factory() as unit_of_work,
        ):
            unit_of_work.runs.finalize(
                run_id,
                status,
                metrics,
                finished_at,
                memory_snapshot.snapshot_id if memory_snapshot is not None else None,
                lease_identity=lease_session.identity,
                lease_checked_at=finished_at,
            )
            if is_m3_enabled:
                unit_of_work.m3_audit.save_checkpoint(
                    run_id,
                    CheckpointStage.FINALIZED.value,
                    result_hash(outcomes),
                    finished_at,
                )
