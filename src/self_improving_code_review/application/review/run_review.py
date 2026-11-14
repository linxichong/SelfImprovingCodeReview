"""M1 から M3 までの一貫したレビューユースケース."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event

from self_improving_code_review import __version__
from self_improving_code_review.application.config.settings import AppConfig
from self_improving_code_review.application.governance.run_lease import (
    NoopRunLeaseSessionFactory,
)
from self_improving_code_review.application.memory.retrieve_memories import (
    MemoryRetriever,
    MemorySnapshotService,
    RetrievedMemories,
)
from self_improving_code_review.application.ports.clock import Clock, IdGenerator
from self_improving_code_review.application.ports.context_source import (
    ContextSourceFactory,
)
from self_improving_code_review.application.ports.diff_provider import DiffProvider
from self_improving_code_review.application.ports.llm_client import (
    LlmClient,
    LlmRequest,
    LlmResult,
)
from self_improving_code_review.application.ports.run_lease import (
    RunLeaseSession,
    RunLeaseSessionFactory,
)
from self_improving_code_review.application.ports.secret_scanner import SecretScanner
from self_improving_code_review.application.ports.telemetry import Telemetry
from self_improving_code_review.application.ports.unit_of_work import (
    UnitOfWorkFactory,
)
from self_improving_code_review.application.review.bundle_review_executor import (
    BundleReviewExecutor,
)
from self_improving_code_review.application.review.bundle_review_models import (
    BundleReview,
    BundleTask,
)
from self_improving_code_review.application.review.bundler import SimpleBundler
from self_improving_code_review.application.review.context_builder import (
    ReviewContextBuilder,
)
from self_improving_code_review.application.review.context_tools import (
    ContextToolExecutor,
)
from self_improving_code_review.application.review.dependency_bundler import (
    DependencyAwareBundler,
)
from self_improving_code_review.application.review.models import (
    BundleOutcome,
    ReviewBundle,
    ReviewPlan,
    ReviewRequest,
    ReviewResult,
)
from self_improving_code_review.application.review.planner import RuleBasedReviewPlanner
from self_improving_code_review.application.review.reflect_findings import FindingReflector
from self_improving_code_review.application.review.resume_review import (
    ResumeReview,
)
from self_improving_code_review.application.review.review_agent import ReviewAgentLoop
from self_improving_code_review.application.review.review_aggregation import (
    discard_blocked_review,
    discard_changed_source,
    find_changed_source_sequences,
    merge_specialist_reviews,
)
from self_improving_code_review.application.review.review_manager import ReviewManager
from self_improving_code_review.application.review.review_metrics import run_status
from self_improving_code_review.application.review.review_preparation import (
    PreparedReviewInput,
    build_idempotency_key,
    build_run_versions,
)
from self_improving_code_review.application.review.review_response_generator import (
    generate_review_response,
)
from self_improving_code_review.application.review.review_result_accumulator import (
    ReviewResultAccumulator,
)
from self_improving_code_review.application.review.review_result_writer import ReviewResultWriter
from self_improving_code_review.application.review.review_run_starter import ReviewRunStarter
from self_improving_code_review.application.review.review_scheduler import schedule_bundle_reviews
from self_improving_code_review.application.review.review_source_mapping import (
    map_secret_findings,
    select_reviewable_raw_diff,
)
from self_improving_code_review.application.review.review_task_preparation import (
    ScannedReviewBundles,
    prepare_review_tasks,
)
from self_improving_code_review.application.review.review_telemetry import record_run_metrics
from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.change.file_filter import (
    FileFilter,
    FileFilterResult,
    FilterPolicy,
)
from self_improving_code_review.domain.change.models import ChangedFile
from self_improving_code_review.domain.common.enums import (
    BundleStatus,
    DiffSourceKind,
    FileDecision,
    FilterReason,
    MemoryStatus,
    ReviewMode,
)
from self_improving_code_review.domain.common.errors import (
    InvalidInputError,
    PolicyViolationError,
)
from self_improving_code_review.domain.common.hashing import hash_lines, hash_text
from self_improving_code_review.domain.common.values import MemoryId, MemoryVersion, RunId
from self_improving_code_review.domain.finding.deduplication import deduplicate
from self_improving_code_review.domain.memory.models import MemoryContent, MemorySnapshot
from self_improving_code_review.domain.rule.loader import RuleLoader
from self_improving_code_review.domain.rule.models import RuleSnapshot
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.models import (
    ScannedPayload,
    ScanResult,
    SecretFinding,
    SourceKind,
    SourcePayload,
)
from self_improving_code_review.security.path_policy import PathPolicy

__all__ = ["RunReviewUseCase"]


class RunReviewUseCase:
    """レビューにおける決定的処理と外部処理の全工程を統括する.

    差分取得、ルール解決、機密情報検査、LLM 呼び出し、候補検証、
    永続化を所定の順序で調停する。各処理の実装は注入されたポートへ委譲し、
    このクラスには処理順と失敗時の状態管理だけを集約する。
    """

    def __init__(
        self,
        *,
        config: AppConfig,
        diff_provider: DiffProvider,
        llm_client: LlmClient,
        secret_scanner: SecretScanner,
        unit_of_work_factory: UnitOfWorkFactory,
        clock: Clock,
        id_generator: IdGenerator,
        context_builder: ReviewContextBuilder,
        telemetry: Telemetry,
        memory_retriever: MemoryRetriever | None = None,
        memory_snapshot_service: MemorySnapshotService | None = None,
        context_source_factory: ContextSourceFactory | None = None,
        context_tool_executor_factory: (
            Callable[[tuple[ChangedFile, ...]], ContextToolExecutor] | None
        ) = None,
        finding_reflector: FindingReflector | None = None,
        file_context_verifier: Callable[[Path, str], bool] | None = None,
        run_lease_session_factory: RunLeaseSessionFactory | None = None,
    ) -> None:
        """設定済みのポートとパイプラインサービスを保持する.

        ``memory_retriever`` と ``memory_snapshot_service`` は M2 のメモリ注入で
        使用する。どちらかが未設定の場合、メモリを注入せず M1 と同じ経路で動作する。
        """
        self._config = config
        self._diff_provider = diff_provider
        self._llm_client = llm_client
        self._secret_scanner = secret_scanner
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._id_generator = id_generator
        self._context_builder = context_builder
        self._telemetry = telemetry
        self._memory_retriever = memory_retriever
        self._run_lease_session_factory = run_lease_session_factory or NoopRunLeaseSessionFactory()
        self._memory_snapshot_service = memory_snapshot_service
        self._context_source_factory = context_source_factory
        self._context_tool_executor_factory = context_tool_executor_factory
        self._finding_reflector = finding_reflector
        self._file_context_verifier = file_context_verifier
        self._resume_review = ResumeReview(context_source_factory)
        self._parser = UnifiedDiffParser()
        self._filter = FileFilter(FilterPolicy(exclude_patterns=config.security.exclude_patterns))
        self._path_policy = PathPolicy(extra_patterns=config.security.blocked_paths)
        self._bundler = SimpleBundler(max_diff_tokens=config.review.bundle_diff_tokens)
        self._dependency_bundler = DependencyAwareBundler(
            max_diff_tokens=config.review.bundle_diff_tokens
        )
        self._rule_loader = RuleLoader()
        self._planner = RuleBasedReviewPlanner(config.review)
        self._manager = ReviewManager()
        self._agent_loop = ReviewAgentLoop(
            llm_client,
            max_findings=config.review.max_findings_per_bundle,
        )

    def execute(self, request: ReviewRequest) -> ReviewResult:
        """遠隔測定障害を分離し、レビュー実行全体を観測する."""
        attributes = {
            "project_id": str(request.project_id),
            "model": self._llm_client.model,
            "provider": self._config.telemetry.provider,
            "prompt_version": self._context_builder.prompt_version,
        }
        with self._run_lease_session_factory() as lease_session:
            try:
                with self._telemetry.start_span("review.run", attributes):
                    result = self._execute(request, lease_session)
            except Exception as exc:
                self._telemetry.add_event(
                    "review.failed",
                    {**attributes, "error_code": type(exc).__name__},
                )
                raise
            record_run_metrics(result, self._telemetry, provider=self._config.telemetry.provider)
            return result

    def _execute(self, request: ReviewRequest, lease_session: RunLeaseSession) -> ReviewResult:
        """レビュー全体を実行し、永続化された結果を返す.

        Args:
            request: リポジトリ、差分入力元、実行モードを含むレビュー要求。
            lease_session: 実行中の所有権を検査するリース会話。

        Returns:
            検証済み指摘、参照指摘、測定値、実行状態を含む完全な結果。

        Raises:
            InvalidDiffError: 差分の形式または行対応が不正な場合。
            PolicyViolationError: リポジトリ外の設定パスなどを検出した場合。
            InfrastructureError: Git、LLM、SQLite の処理を継続できない場合。
        """
        prepared = self._acquire_review_input(request)
        raw_diff = prepared.raw_diff
        file_results = prepared.file_results
        snapshot = prepared.snapshot
        writer = ReviewResultWriter(
            self._unit_of_work_factory,
            self._clock,
            self._telemetry,
            is_specialized_review_enabled=self._config.governance.specialized_reviewers_enabled,
        )
        memory_state_hash = self._active_memory_state_hash(request)
        idempotency_key = build_idempotency_key(
            request,
            raw_diff,
            snapshot,
            config=self._config,
            context_builder=self._context_builder,
            reflector=self._finding_reflector,
            package_version=__version__,
            secret_engine_version=self._secret_scanner.engine_version,
            model=self._llm_client.model,
            memory_state_hash=memory_state_hash,
        )
        with self._unit_of_work_factory() as unit_of_work:
            completed = unit_of_work.results.find_by_idempotency_key(idempotency_key)
            resumable = unit_of_work.results.find_resumable_by_idempotency_key(idempotency_key)
            interrupted = (
                unit_of_work.results.find_interrupted_by_idempotency_key(idempotency_key)
                if self._config.governance.execution_leases_enabled
                else None
            )
        if completed is not None:
            return completed

        versions = build_run_versions(
            snapshot,
            config=self._config,
            context_builder=self._context_builder,
            reflector=self._finding_reflector,
            package_version=__version__,
            secret_engine_version=self._secret_scanner.engine_version,
        )
        is_m3_enabled = (
            self._config.review.context_tools_enabled
            or self._config.review.reflection_enabled
            or self._config.review.max_workers > 1
            or self._config.governance.specialized_reviewers_enabled
        )
        started = ReviewRunStarter(
            config=self._config,
            unit_of_work_factory=self._unit_of_work_factory,
            clock=self._clock,
            id_generator=self._id_generator,
            llm_client=self._llm_client,
            bundler=self._bundler,
            dependency_bundler=self._dependency_bundler,
            planner=self._planner,
            resume_review=self._resume_review,
        ).start(
            request,
            prepared,
            versions,
            idempotency_key,
            resumable or interrupted,
            is_m3_enabled=is_m3_enabled,
            lease_session=lease_session,
        )
        running = started.running
        run_id = running.run_id
        resume_result = started.resume_result
        bundling = started.bundling
        planned_reviews = started.planned_reviews
        filtered = started.filtered_files

        resumed_outcomes = (
            tuple(
                outcome
                for outcome in resume_result.bundle_outcomes
                if outcome.status is BundleStatus.SUCCESS
            )
            if resume_result is not None
            else ()
        )
        resumed_sequences = {outcome.bundle_id.sequence for outcome in resumed_outcomes}
        bundles_to_process = tuple(
            bundle
            for bundle in bundling.bundles
            if bundle.bundle_id.sequence not in resumed_sequences
        )
        collected = ReviewResultAccumulator(resumed_outcomes)
        secret_findings: tuple[SecretFinding, ...] = ()
        scanned_bundles = self._scan_review_bundles(
            bundles_to_process,
            file_results,
            lease_session,
        )
        scanned_payloads = scanned_bundles.payloads
        secret_findings = scanned_bundles.secret_findings
        if scanned_bundles.blocked_outcome is not None:
            collected.outcomes.append(scanned_bundles.blocked_outcome)

        bundles_to_review = () if secret_findings else bundles_to_process
        memory_snapshot: MemorySnapshot | None = None
        resumed_bundle_count = len(resumed_outcomes)
        reexecuted_bundle_count = len(bundles_to_process) if resume_result is not None else 0
        prepared_tasks = prepare_review_tasks(
            request,
            bundles_to_review,
            planned_reviews,
            scanned_payloads,
            config=self._config,
            context_builder=self._context_builder,
            retrieve=self._retrieve_memories,
        )
        memory_truncated_count = prepared_tasks.memory_truncated_count
        has_memory_context = prepared_tasks.has_memory_context
        reviews = self._run_bundle_tasks(
            prepared_tasks.tasks,
            request=request,
            run_id=run_id,
            snapshot=snapshot,
            source_revision=raw_diff.source_revision,
        )
        blocked_review = next((item for item in reviews if item.secret_findings), None)
        if blocked_review is not None:
            secret_findings = blocked_review.secret_findings
        else:
            changed_source_sequences = find_changed_source_sequences(reviews)
            reviews = tuple(
                discard_changed_source(review)
                if review.task.bundle.bundle_id.sequence in changed_source_sequences
                else review
                for review in reviews
            )
        if self._config.governance.specialized_reviewers_enabled:
            reviews = merge_specialist_reviews(reviews)

        for review in reviews:
            built = review.task.built
            hash_text(built.request.system_instruction + "\n" + built.request.user_content)
            outcome = (
                discard_blocked_review(review.outcome, is_source=bool(review.secret_findings))
                if secret_findings
                else review.outcome
            )
            # 保存、再開用ハッシュ、関連監査は同じ採用指摘を参照する。
            # 集計には除外前の候補も渡し、重複件数を維持する。
            validated_findings = outcome.findings
            kept_findings, _ = deduplicate(validated_findings)
            outcome = replace(outcome, findings=kept_findings)
            collected.add_review(
                review,
                outcome,
                validated_findings,
                is_blocked=bool(secret_findings),
            )
            writer.save_review(
                review,
                outcome,
                file_results,
                snapshot,
                run_id,
                is_m3_enabled=is_m3_enabled,
                is_blocked=bool(secret_findings),
                lease_session=lease_session,
            )

        if is_m3_enabled:
            lease_session.assert_owned()
            with self._unit_of_work_factory() as unit_of_work:
                unit_of_work.m3_audit.refresh_context_source_hash(run_id)

        lease_session.assert_owned()
        memory_snapshot = self._persist_memory_snapshot(
            request,
            prepared_tasks.memory_contents,
            collected.finding_memory_links,
            run_id,
            has_memory_context=has_memory_context and not secret_findings,
        )

        aggregation = self._manager.aggregate(() if secret_findings else tuple(collected.findings))
        metrics = collected.to_metrics(
            aggregation,
            memory_truncated_count=memory_truncated_count,
            resumed_bundle_count=resumed_bundle_count,
            reexecuted_bundle_count=reexecuted_bundle_count,
            is_m3_enabled=is_m3_enabled,
        )
        status = run_status(
            outcomes=tuple(collected.outcomes),
            was_blocked=bool(secret_findings),
        )
        finished_at = self._clock.now_utc()
        writer.finalize_run(
            run_id,
            status,
            metrics,
            tuple(collected.outcomes),
            finished_at,
            memory_snapshot,
            is_m3_enabled=is_m3_enabled,
            lease_session=lease_session,
        )
        terminal = replace(
            running,
            status=status,
            finished_at=finished_at,
            versions=replace(
                versions,
                memory_snapshot_id=(
                    memory_snapshot.snapshot_id if memory_snapshot is not None else None
                ),
            ),
        )
        return ReviewResult(
            run=terminal,
            main_findings=aggregation.main_findings,
            reference_findings=aggregation.reference_findings,
            filtered_files=filtered,
            metrics=metrics,
            bundle_outcomes=tuple(collected.outcomes),
            secret_findings=secret_findings,
        )

    def _scan_review_bundles(
        self,
        bundles_to_process: tuple[ReviewBundle, ...],
        file_results: tuple[FileFilterResult, ...],
        lease_session: RunLeaseSession,
    ) -> ScannedReviewBundles:
        """対象単位を順に検査し、最初の秘密検出で遮断記録を保存する."""
        scanned_payloads: dict[str, ScannedPayload] = {}
        secret_findings: tuple[SecretFinding, ...] = ()
        blocked_outcome: BundleOutcome | None = None

        for bundle in bundles_to_process:
            scan_result, scanned = self._scan_and_seal(
                SourcePayload(
                    kind=SourceKind.DIFF,
                    label="bundle-diff",
                    text=bundle.diff_text,
                )
            )
            if scanned is not None:
                scanned_payloads[str(bundle.bundle_id)] = scanned
                continue
            secret_findings = map_secret_findings(bundle, scan_result.findings)
            outcome = BundleOutcome(
                bundle_id=bundle.bundle_id,
                status=BundleStatus.SKIPPED,
                findings=(),
                error_code="SECRET_DETECTED",
                error_message=Messages.SENSITIVE_CANDIDATE_BEFORE_LLM,
            )
            blocked_outcome = outcome
            lease_session.assert_owned()
            with self._unit_of_work_factory() as unit_of_work:
                unit_of_work.bundles.save(bundle, outcome, file_results)
            break

        return ScannedReviewBundles(scanned_payloads, secret_findings, blocked_outcome)

    def _acquire_review_input(self, request: ReviewRequest) -> PreparedReviewInput:
        """差分取得、対象判定、固定ソース照合、ルール読込を所定の順で行う."""
        with self._telemetry.start_span("change.acquire"):
            raw_diff = self._diff_provider.acquire(request.repository_path, request.diff_source)
        changeset = self._parser.parse(raw_diff.text)
        file_results = tuple(self._decide_file(item) for item in changeset.files)
        reviewable_paths = {result.path.value for result in file_results if result.is_reviewable}
        reviewable_files = tuple(
            item for item in changeset.files if item.new_path.value in reviewable_paths
        )
        if (
            self._config.review.context_tools_enabled
            and request.diff_source.kind is DiffSourceKind.FILE
            and reviewable_files
            and (
                self._file_context_verifier is None
                or not self._file_context_verifier(
                    request.repository_path,
                    select_reviewable_raw_diff(raw_diff.text, changeset.files, reviewable_paths),
                )
            )
        ):
            raise InvalidInputError(Messages.FILE_DIFF_WORKTREE_MISMATCH)
        snapshot = self._rule_loader.load(
            self._resolve_rules_root(request.repository_path),
            git_sha=raw_diff.source_revision,
        )
        return PreparedReviewInput(raw_diff, file_results, reviewable_files, snapshot)

    def _run_bundle_tasks(
        self,
        tasks: tuple[BundleTask, ...],
        *,
        request: ReviewRequest,
        run_id: RunId,
        snapshot: RuleSnapshot,
        source_revision: str | None,
    ) -> tuple[BundleReview, ...]:
        """実行依存を固定し、上限付き調停へ担当群の処理を渡す."""

        def execute_group(
            group: tuple[BundleTask, ...],
            stop_event: Event,
        ) -> tuple[BundleReview, ...]:
            """同じ要求と停止信号で主担当・補助担当を順に処理する."""
            return self._review_bundle_group(
                group,
                request=request,
                run_id=run_id,
                snapshot=snapshot,
                source_revision=source_revision,
                stop_event=stop_event,
            )

        return schedule_bundle_reviews(
            tasks,
            max_workers=self._config.review.max_workers,
            execute_group=execute_group,
        )

    def _review_bundle_group(
        self,
        tasks: tuple[BundleTask, ...],
        *,
        request: ReviewRequest,
        run_id: RunId,
        snapshot: RuleSnapshot,
        source_revision: str | None,
        stop_event: Event,
    ) -> tuple[BundleReview, ...]:
        """同一レビュー単位の主担当と補助担当を安定順に逐次実行する."""
        bundle_executor = BundleReviewExecutor(
            llm_client=self._llm_client,
            secret_scanner=self._secret_scanner,
            telemetry=self._telemetry,
            manager=self._manager,
            agent_loop=self._agent_loop,
            path_policy=self._path_policy,
            context_source_factory=self._context_source_factory,
            context_tool_executor_factory=self._context_tool_executor_factory,
            finding_reflector=self._finding_reflector,
            generate=self._generate_with_repair,
            scan=self._scan_and_seal,
        )

        return tuple(
            bundle_executor.execute(
                task,
                request=request,
                run_id=run_id,
                snapshot=snapshot,
                source_revision=source_revision,
                stop_event=stop_event,
            )
            for task in tasks
        )

    def _scan_and_seal(
        self,
        payload: SourcePayload,
    ) -> tuple[ScanResult, ScannedPayload | None]:
        """LLM 送信直前の機密情報検査を独立した観測区間で実行する."""
        with self._telemetry.start_span("secret.scan"):
            return self._secret_scanner.scan_and_seal(payload)

    def _retrieve_memories(
        self,
        request: ReviewRequest,
        bundle: ReviewBundle,
        plan: ReviewPlan,
    ) -> RetrievedMemories:
        """レビュー単位へ注入する有効メモリを検索する.

        注入が無効、または ``MEMORY`` 以外のモードでは検索せず、
        比較群 B と同じ入力でレビューを実行する.
        """
        if (
            self._memory_retriever is None
            or not self._config.memory.is_injection_enabled
            or request.mode is not ReviewMode.MEMORY
        ):
            return RetrievedMemories(contents=(), truncated_count=0, total_tokens=0)
        return self._memory_retriever.retrieve(
            request.project_id,
            bundle.files,
            risk=bundle.risk,
            perspective=plan.perspective,
        )

    def _active_memory_state_hash(self, request: ReviewRequest) -> str:
        """冪等性判定へ使用する有効メモリ集合の安定ハッシュを返す."""
        if (
            self._memory_retriever is None
            or not self._config.memory.is_injection_enabled
            or request.mode is not ReviewMode.MEMORY
        ):
            return hash_lines(("memory-disabled",))
        with self._unit_of_work_factory() as unit_of_work:
            active = unit_of_work.memories.list_by_status(
                request.project_id,
                MemoryStatus.ACTIVE,
            )
        return hash_lines(
            (
                "memory-state",
                *(
                    f"{record.memory_id}:{content.version.value}:{content.content_hash}"
                    for record, content in active
                ),
            )
        )

    def _persist_memory_snapshot(
        self,
        request: ReviewRequest,
        contents: tuple[MemoryContent, ...],
        finding_memory_links: list[tuple[str, MemoryId, MemoryVersion]],
        run_id: RunId,
        *,
        has_memory_context: bool,
    ) -> MemorySnapshot | None:
        """注入したメモリを固定し、指摘へ関連付ける.

        指摘が 0 件でも、実際にプロンプトへ入れたメモリ集合を固定する.
        """
        if self._memory_snapshot_service is None or not has_memory_context:
            return None
        ordered_contents = tuple(
            sorted(contents, key=lambda content: (str(content.memory_id), content.version.value))
        )
        snapshot = self._memory_snapshot_service.create(request.project_id, ordered_contents)
        if finding_memory_links:
            with self._unit_of_work_factory() as unit_of_work:
                unit_of_work.memory_snapshots.link_findings(
                    snapshot,
                    run_id,
                    tuple(finding_memory_links),
                )
        return snapshot

    def _resolve_rules_root(self, repository_root: Path) -> Path:
        """リポジトリルート配下に制限したルールディレクトリを返す."""
        root = repository_root.resolve()
        candidate = (root / self._config.review.rules_directory).resolve()
        if not candidate.is_relative_to(root):
            message = Messages.RULES_DIRECTORY_OUTSIDE_REPOSITORY
            raise PolicyViolationError(message)
        return candidate

    def _decide_file(self, changed_file: ChangedFile) -> FileFilterResult:
        """通常のファイル選別より先にアクセス禁止パス方針を適用する."""
        if self._path_policy.is_blocked(changed_file.new_path):
            return FileFilterResult(
                path=changed_file.new_path,
                decision=FileDecision.FILTER,
                reason=FilterReason.EXCLUDED,
            )
        return self._filter.decide(changed_file)

    def _generate_with_repair(self, request: LlmRequest) -> LlmResult:
        """既存の送信境界から構造化応答生成へ委譲する."""
        return generate_review_response(
            request,
            llm_client=self._llm_client,
            telemetry=self._telemetry,
            max_findings=self._config.review.max_findings_per_bundle,
        )
