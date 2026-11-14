"""再開照合、新規実行の開始と初期チェックポイントを所定順に処理する."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from self_improving_code_review.application.config.settings import AppConfig
from self_improving_code_review.application.ports.clock import Clock, IdGenerator
from self_improving_code_review.application.ports.llm_client import (
    LlmClient,
)
from self_improving_code_review.application.ports.run_lease import (
    RunLeaseSession,
)
from self_improving_code_review.application.ports.unit_of_work import (
    UnitOfWorkFactory,
)
from self_improving_code_review.application.review.bundler import BundlingResult, SimpleBundler
from self_improving_code_review.application.review.dependency_bundler import (
    DependencyAwareBundler,
    DependencyBundlingResult,
)
from self_improving_code_review.application.review.models import (
    ReviewPlan,
    ReviewRequest,
    ReviewResult,
    RunRecord,
    RunVersions,
)
from self_improving_code_review.application.review.planner import RuleBasedReviewPlanner
from self_improving_code_review.application.review.resume_review import (
    BundleResumeIdentity,
    ResumeIdentity,
    ResumeReview,
)
from self_improving_code_review.application.review.review_identity import (
    bundle_input_hash,
    compute_plan_hash,
    is_resume_candidate,
)
from self_improving_code_review.application.review.review_preparation import (
    PreparedReviewInput,
    prepare_pipeline,
)
from self_improving_code_review.domain.change.file_filter import (
    FileFilterResult,
)
from self_improving_code_review.domain.common.enums import (
    CheckpointStage,
    FileDecision,
    FilterReason,
    ReviewMode,
    RunStatus,
)
from self_improving_code_review.domain.common.errors import (
    PolicyViolationError,
)
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.rule.models import ReviewRule
from self_improving_code_review.messages import Messages


@dataclass(frozen=True, slots=True)
class StartedReviewRun:
    """開始処理を終えた実行と固定計画を後続処理へ渡す."""

    running: RunRecord
    resume_result: ReviewResult | None
    bundling: BundlingResult | DependencyBundlingResult
    planned_reviews: Mapping[int, tuple[ReviewPlan, tuple[ReviewRule, ...]]]
    filtered_files: tuple[FileFilterResult, ...]


class ReviewRunStarter:
    """計画照合とリース取得を調停し、開始時の保存境界を維持する."""

    def __init__(
        self,
        *,
        config: AppConfig,
        unit_of_work_factory: UnitOfWorkFactory,
        clock: Clock,
        id_generator: IdGenerator,
        llm_client: LlmClient,
        bundler: SimpleBundler,
        dependency_bundler: DependencyAwareBundler,
        planner: RuleBasedReviewPlanner,
        resume_review: ResumeReview,
    ) -> None:
        """既存の開始処理に使う構成済み依存を保持する."""
        self._config = config
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._id_generator = id_generator
        self._llm_client = llm_client
        self._bundler = bundler
        self._dependency_bundler = dependency_bundler
        self._planner = planner
        self._resume_review = resume_review

    def start(
        self,
        request: ReviewRequest,
        prepared: PreparedReviewInput,
        versions: RunVersions,
        idempotency_key: str,
        candidate: ReviewResult | None,
        *,
        is_m3_enabled: bool,
        lease_session: RunLeaseSession,
    ) -> StartedReviewRun:
        """再開可能性を検証し、新規または再開実行と初期監査を保存する."""
        raw_diff = prepared.raw_diff
        file_results = prepared.file_results
        reviewable_files = prepared.reviewable_files
        snapshot = prepared.snapshot
        resume_result = candidate if is_resume_candidate(candidate) else None
        run_id = (
            resume_result.run.run_id
            if resume_result is not None
            else self._id_generator.new_run_id()
        )
        bundling, planned_reviews = prepare_pipeline(
            run_id,
            reviewable_files,
            snapshot,
            request.mode,
            is_m3_enabled=is_m3_enabled,
            config=self._config,
            bundler=self._bundler,
            dependency_bundler=self._dependency_bundler,
            planner=self._planner,
        )
        plan_hash = compute_plan_hash(bundling.bundles, planned_reviews)
        if resume_result is not None and is_m3_enabled:
            with self._unit_of_work_factory() as unit_of_work:
                stored_identity = unit_of_work.m3_audit.load_resume_identity(run_id)
            expected_identity = ResumeIdentity(
                pipeline_config_hash=self._config.pipeline_content_hash,
                diff_hash=hash_text(raw_diff.text),
                plan_hash=plan_hash,
                bundles=tuple(
                    BundleResumeIdentity(
                        sequence=bundle.bundle_id.sequence,
                        input_hash=bundle_input_hash(
                            bundle,
                            planned_reviews[bundle.bundle_id.sequence][0],
                        ),
                        reviewer_type=planned_reviews[bundle.bundle_id.sequence][
                            0
                        ].reviewer_type.value,
                    )
                    for bundle in bundling.bundles
                ),
                requires_memory_snapshot=(
                    request.mode is ReviewMode.MEMORY and self._config.memory.is_injection_enabled
                ),
            )
            if not self._resume_review.can_resume(
                resume_result,
                stored_identity,
                expected_identity,
                repository=request.repository_path,
                source_revision=raw_diff.source_revision,
                source_kind=request.diff_source.kind,
            ):
                resume_result = None
                run_id = self._id_generator.new_run_id()
                bundling, planned_reviews = prepare_pipeline(
                    run_id,
                    reviewable_files,
                    snapshot,
                    request.mode,
                    is_m3_enabled=is_m3_enabled,
                    config=self._config,
                    bundler=self._bundler,
                    dependency_bundler=self._dependency_bundler,
                    planner=self._planner,
                )
                plan_hash = compute_plan_hash(bundling.bundles, planned_reviews)
        if resume_result is not None:
            lease_session.acquire(run_id)
            lease_checked_at = self._clock.now_utc()
            with self._unit_of_work_factory() as unit_of_work:
                if not unit_of_work.runs.acquire_resume(
                    run_id,
                    lease_identity=lease_session.identity,
                    lease_checked_at=lease_checked_at,
                ):
                    raise PolicyViolationError(Messages.REVIEW_RUN_RESUME_CONFLICT)
            running = replace(
                resume_result.run,
                status=RunStatus.RUNNING,
                finished_at=None,
                resume_count=resume_result.run.resume_count + 1,
            )
        else:
            running = RunRecord(
                run_id=run_id,
                project_id=request.project_id,
                mode=request.mode,
                status=RunStatus.RUNNING,
                source_revision=raw_diff.source_revision,
                model=self._llm_client.model,
                versions=versions,
                idempotency_key=idempotency_key,
                started_at=self._clock.now_utc(),
            )
            with self._unit_of_work_factory() as unit_of_work:
                unit_of_work.rule_snapshots.save(snapshot)
                unit_of_work.runs.create(running)
            lease_session.acquire(run_id)

        oversized_results = tuple(
            FileFilterResult(
                path=item.path,
                decision=FileDecision.FILTER,
                reason=FilterReason.BUDGET_EXCEEDED,
            )
            for item in bundling.oversized_files
        )
        filtered = (
            tuple(result for result in file_results if not result.is_reviewable) + oversized_results
        )
        lease_session.assert_owned()
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.bundles.save_filtered_files(run_id, filtered)
            if is_m3_enabled:
                edges = bundling.edges if isinstance(bundling, DependencyBundlingResult) else ()
                unit_of_work.m3_audit.save_edges(run_id, edges)
                unit_of_work.m3_audit.save_run_identity(run_id, plan_hash)
                unit_of_work.m3_audit.save_checkpoint(
                    run_id,
                    CheckpointStage.DIFF_PARSED.value,
                    hash_text(raw_diff.text),
                    self._clock.now_utc(),
                )
                unit_of_work.m3_audit.save_checkpoint(
                    run_id,
                    CheckpointStage.PLAN_CREATED.value,
                    plan_hash,
                    self._clock.now_utc(),
                )

        return StartedReviewRun(
            running=running,
            resume_result=resume_result,
            bundling=bundling,
            planned_reviews=planned_reviews,
            filtered_files=filtered,
        )
