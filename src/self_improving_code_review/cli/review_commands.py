"""M1 レビューコマンドの構成起点."""

from __future__ import annotations

from argparse import Namespace
from functools import partial
from pathlib import Path

from self_improving_code_review.application.config.settings import (
    AppConfig,
    load_config,
    read_api_key,
)
from self_improving_code_review.application.governance import (
    ManagedRunLeaseSessionFactory,
    ProjectContextValidator,
)
from self_improving_code_review.application.memory.retrieve_memories import (
    MemoryRetriever,
    MemorySnapshotService,
)
from self_improving_code_review.application.ports.diff_provider import DiffSource
from self_improving_code_review.application.ports.run_lease import RunLeaseSessionFactory
from self_improving_code_review.application.ports.telemetry import Telemetry
from self_improving_code_review.application.review.context_builder import ReviewContextBuilder
from self_improving_code_review.application.review.models import ReviewRequest, ReviewResult
from self_improving_code_review.application.review.prompt_loader import load_prompt_template
from self_improving_code_review.application.review.reflect_findings import FindingReflector
from self_improving_code_review.application.review.run_review import RunReviewUseCase
from self_improving_code_review.cli.output import write_result
from self_improving_code_review.domain.common.enums import DiffSourceKind, ReviewMode, RunStatus
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.infrastructure.context_tools import (
    RepositoryContextSourceFactory,
    RepositoryContextToolExecutor,
)
from self_improving_code_review.infrastructure.git import (
    GitDiffAdapter,
    GitFileContextVerifier,
    RepositoryIdentity,
    RepositoryIdentityResolver,
)
from self_improving_code_review.infrastructure.llm import AiGatewayLlmClient, DatasetLlmClient
from self_improving_code_review.infrastructure.persistence import (
    SqliteRunLeaseRepository,
    SqliteUnitOfWorkFactory,
)
from self_improving_code_review.infrastructure.persistence.governance_repository import (
    SqliteGovernanceRepository,
)
from self_improving_code_review.infrastructure.persistence.short_transaction import (
    ShortTransactionMemoryRepository,
    ShortTransactionSnapshotRepository,
)
from self_improving_code_review.infrastructure.system import (
    SystemClock,
    ThreadHeartbeatScheduler,
    Uuid4IdGenerator,
)
from self_improving_code_review.infrastructure.telemetry import (
    FailureIsolatedTelemetry,
    NoopTelemetry,
    build_opentelemetry,
)
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.path_policy import PathPolicy
from self_improving_code_review.security.secret_scanner import DetectSecretsScanner

__all__ = ["execute_review_command", "run_configured_review"]


def execute_review_command(arguments: Namespace) -> int:
    """CLI 引数から M1 の依存関係を構成し、レビューを一回実行する.

    Args:
        arguments: argparse が検証したレビューコマンドの引数。

    Returns:
        成功は 0、部分成功または失敗は 2、機密情報による遮断は 4。

    Raises:
        InvalidInputError: 設定、入力元、LLM プロバイダーの指定が不正な場合。
    """
    repository = Path(arguments.repository).resolve()
    config = load_config(Path(arguments.config) if arguments.config else None)
    mode_value = arguments.mode or config.review.mode
    project_value = arguments.project or config.project_id
    config = config.model_copy(
        update={
            "project_id": project_value,
            "review": config.review.model_copy(update={"mode": mode_value}),
        }
    )
    diff_source = _diff_source(arguments)
    result = run_configured_review(
        repository,
        config,
        diff_source,
        ReviewMode(mode_value),
        config_path=Path(arguments.config) if arguments.config else None,
    )
    write_result(result, Path(arguments.output) if arguments.output else None)
    if result.run.status is RunStatus.BLOCKED:
        return 4
    if result.run.status in {RunStatus.FAILED, RunStatus.PARTIAL_SUCCESS}:
        return 2
    return 0


def run_configured_review(
    repository: Path,
    config: AppConfig,
    diff_source: DiffSource,
    mode: ReviewMode,
    *,
    config_path: Path | None = None,
    execution_nonce: str | None = None,
) -> ReviewResult:
    """構成済み依存関係でレビューを実行し、完全な結果を返す."""
    database_path = _within_repository(
        repository, config.database.path, Messages.DATABASE_PATH_LABEL
    )
    prompts_root = repository / "prompts"
    _validate_project_context(repository, config, database_path)
    specialist_template = (
        load_prompt_template(prompts_root, "review_specialist_v1")
        if config.governance.specialized_reviewers_enabled
        else None
    )
    template = load_prompt_template(prompts_root)
    agent_template = (
        load_prompt_template(prompts_root, "review_agent_v1")
        if config.review.context_tools_enabled
        else None
    )
    reflection_template = (
        load_prompt_template(prompts_root, "reflection_v1")
        if config.review.reflection_enabled
        else None
    )
    telemetry = _build_telemetry(config)
    try:
        llm_client = _build_llm_client(config, repository)
    except Exception:
        telemetry.close()
        raise
    unit_of_work_factory = SqliteUnitOfWorkFactory(
        database_path, busy_timeout_ms=config.governance.sqlite_busy_timeout_ms
    )
    clock = SystemClock()
    id_generator = Uuid4IdGenerator()
    run_lease_session_factory = _build_run_lease_sessions(
        config, database_path, clock, id_generator
    )

    path_policy = PathPolicy(extra_patterns=config.security.blocked_paths)
    context_executor_factory = partial(
        RepositoryContextToolExecutor,
        path_policy=path_policy,
        max_results=config.review.max_tool_results,
    )
    try:
        use_case = RunReviewUseCase(
            config=config,
            diff_provider=GitDiffAdapter(),
            llm_client=llm_client,
            secret_scanner=DetectSecretsScanner(),
            unit_of_work_factory=unit_of_work_factory,
            clock=clock,
            id_generator=id_generator,
            context_builder=ReviewContextBuilder(
                template,
                max_findings=config.review.max_findings_per_bundle,
                temperature=config.llm.temperature,
                specialist_template=specialist_template,
                agent_template=agent_template,
            ),
            telemetry=telemetry,
            run_lease_session_factory=run_lease_session_factory,
            memory_retriever=MemoryRetriever(
                ShortTransactionMemoryRepository(unit_of_work_factory),
                max_memories=config.memory.max_memories,
                memory_token_budget=config.review.memory_tokens,
                per_memory_token_budget=config.memory.per_memory_tokens,
                clock=clock if config.governance.memory_lifecycle_enabled else None,
            ),
            memory_snapshot_service=MemorySnapshotService(
                ShortTransactionSnapshotRepository(unit_of_work_factory),
                clock=clock,
            ),
            context_source_factory=(
                RepositoryContextSourceFactory(max_file_bytes=config.review.max_context_file_bytes)
                if config.review.context_tools_enabled
                else None
            ),
            context_tool_executor_factory=(
                context_executor_factory if config.review.context_tools_enabled else None
            ),
            finding_reflector=(
                FindingReflector(
                    llm_client,
                    reflection_template,
                    max_input_tokens=config.review.reflection_tokens,
                    max_output_tokens=config.review.max_output_tokens,
                    temperature=config.llm.temperature,
                )
                if reflection_template is not None
                else None
            ),
            file_context_verifier=(
                GitFileContextVerifier() if config.review.context_tools_enabled else None
            ),
        )
        result = use_case.execute(
            ReviewRequest(
                repository_path=repository,
                project_id=ProjectId(config.project_id),
                diff_source=diff_source,
                mode=mode,
                config_path=config_path,
                execution_nonce=execution_nonce,
            )
        )
    finally:
        try:
            llm_client.close()
        finally:
            telemetry.close()
    return result


def _validate_project_context(
    repository: Path,
    config: AppConfig,
    database_path: Path,
) -> None:
    """M5 有効時だけ登録済みプロジェクトとリポジトリ所属を検証する."""
    if not config.governance.enabled:
        return
    identity = (
        RepositoryIdentity(config.scm.provider, config.scm.repository_external_id)
        if config.scm.repository_external_id
        else RepositoryIdentityResolver().resolve(repository)
    )
    governance_repository = SqliteGovernanceRepository(
        database_path,
        busy_timeout_ms=config.governance.sqlite_busy_timeout_ms,
    )
    ProjectContextValidator(governance_repository).validate(
        ProjectId(config.project_id),
        provider=identity.provider,
        repository_external_id=identity.external_id,
    )


def _build_run_lease_sessions(
    config: AppConfig,
    database_path: Path,
    clock: SystemClock,
    id_generator: Uuid4IdGenerator,
) -> RunLeaseSessionFactory | None:
    """有効時だけ独立接続とバックグラウンド心拍を持つ会話を構築する."""
    if not config.governance.execution_leases_enabled:
        return None
    return ManagedRunLeaseSessionFactory(
        repository=SqliteRunLeaseRepository(
            database_path,
            busy_timeout_ms=config.governance.sqlite_busy_timeout_ms,
        ),
        clock=clock,
        scheduler=ThreadHeartbeatScheduler(),
        owner_factory=id_generator.new_lease_owner_id,
        lease_seconds=config.governance.lease_seconds,
        heartbeat_seconds=config.governance.heartbeat_seconds,
    )


def _build_telemetry(config: AppConfig) -> Telemetry:
    """設定に応じて Noop または OpenTelemetry の安全境界を構築する."""
    delegate: Telemetry = (
        build_opentelemetry(config.telemetry) if config.telemetry.enabled else NoopTelemetry()
    )
    return FailureIsolatedTelemetry(delegate)


def _build_llm_client(
    config: AppConfig,
    repository: Path,
) -> AiGatewayLlmClient | DatasetLlmClient:
    """設定に応じて外部ゲートウェイまたはローカルデータセットを選択する.

    dataset モードはネットワークと認証情報を使用しない。ai_gateway モードは
    外部送信許可、固定モデル、API キーがすべて揃った場合だけ生成する。

    Args:
        config: 検証済みのアプリケーション設定。
        repository: データセットパスを制限するリポジトリルート。

    Returns:
        アプリケーションの LLM ポートを満たすクライアント。

    Raises:
        InvalidInputError: 必須設定がない、またはパスがリポジトリ外の場合。
    """
    if config.llm.provider == "dataset":
        if config.llm.dataset_path is None:
            raise InvalidInputError(Messages.DATASET_PATH_REQUIRED)
        dataset_path = _within_repository(
            repository,
            config.llm.dataset_path,
            Messages.LLM_DATASET_PATH_LABEL,
        )
        return DatasetLlmClient(dataset_path)

    if not config.llm.is_resolved:
        raise InvalidInputError(Messages.MODEL_REQUIRED)
    if not config.security.allow_external_source:
        raise InvalidInputError(Messages.EXTERNAL_SOURCE_PERMISSION_REQUIRED)
    api_key = read_api_key()
    if api_key is None:
        raise InvalidInputError(Messages.API_KEY_REQUIRED)
    return AiGatewayLlmClient(
        base_url=config.llm.base_url,
        model=config.llm.model,
        api_key=api_key,
        timeout_seconds=config.llm.timeout_seconds,
        max_retries=config.llm.max_retries,
    )


def _diff_source(arguments: Namespace) -> DiffSource:
    """Argparse で選択した排他的な差分入力元を構築する."""
    if arguments.git_diff:
        return DiffSource(kind=DiffSourceKind.WORKSPACE)
    if arguments.range_revisions:
        base, head = arguments.range_revisions
        return DiffSource(
            kind=DiffSourceKind.RANGE,
            base_revision=base,
            head_revision=head,
        )
    if arguments.commit:
        return DiffSource(kind=DiffSourceKind.COMMIT, commit=arguments.commit)
    if not arguments.diff_file:
        raise InvalidInputError(Messages.DIFF_SOURCE_REQUIRED)
    return DiffSource(kind=DiffSourceKind.FILE, patch_path=Path(arguments.diff_file))


def _within_repository(repository: Path, configured: Path, label: str) -> Path:
    """設定されたパスを解決し、リポジトリ内に制限する."""
    candidate = (
        configured.resolve() if configured.is_absolute() else (repository / configured).resolve()
    )
    if not candidate.is_relative_to(repository):
        raise InvalidInputError(Messages.configured_path_outside_repository(label))
    return candidate
