"""M4 SCM レビューコマンドの構成起点."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from self_improving_code_review.application.config.settings import (
    AppConfig,
    load_config,
    read_scm_token,
)
from self_improving_code_review.application.ports.diff_provider import DiffSource
from self_improving_code_review.application.review.models import ReviewResult
from self_improving_code_review.application.scm.models import ScmReviewOutcome
from self_improving_code_review.application.scm.review_change import ScmReviewCoordinator
from self_improving_code_review.application.scm.sync_feedback import SyncScmFeedback
from self_improving_code_review.cli.output import result_to_document, write_document
from self_improving_code_review.cli.review_commands import (
    _validate_project_context,
    run_configured_review,
)
from self_improving_code_review.cli.scm_feedback_processor import ScmM2FeedbackProcessor
from self_improving_code_review.domain.common.enums import ReviewMode, RunStatus
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.domain.scm.models import (
    ChangeRef,
    PublicationStatus,
    ScmProvider,
)
from self_improving_code_review.infrastructure.git import GitDiffAdapter
from self_improving_code_review.infrastructure.persistence.scm_feedback_repository import (
    SqliteScmFeedbackRepository,
)
from self_improving_code_review.infrastructure.persistence.scm_publisher import (
    SqliteScmPublicationPublisher,
)
from self_improving_code_review.infrastructure.persistence.sqlite import initialize_database
from self_improving_code_review.infrastructure.scm import GitHubScmClient
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.secret_scanner import DetectSecretsScanner

__all__ = ["execute_scm_command"]


class _ConfiguredReviewExecutor:
    """既存の CLI 構成済みレビュー関数をアプリケーションポートへ適合させる."""

    def execute(
        self,
        repository: Path,
        config: AppConfig,
        diff_source: DiffSource,
        mode: ReviewMode,
        *,
        config_path: Path | None,
        execution_nonce: str | None,
    ) -> ReviewResult:
        """既存 M3 レビュー処理へ引数をそのまま委譲する."""
        return run_configured_review(
            repository,
            config,
            diff_source,
            mode,
            config_path=config_path,
            execution_nonce=execution_nonce,
        )


def execute_scm_command(arguments: Namespace) -> int:
    """SCM 変更要求をレビューし、任意でコメントを公開する."""
    repository = Path(arguments.repository).resolve()
    config_path = Path(arguments.config) if arguments.config else None
    config = load_config(config_path)
    token = read_scm_token()
    if token is None:
        raise InvalidInputError(Messages.SCM_TOKEN_REQUIRED)
    client = GitHubScmClient(config.scm, token)
    database_path = _within_repository(repository, config.database.path)
    initialize_database(database_path)
    _validate_project_context(repository, config, database_path)
    project_id = ProjectId(config.project_id) if config.governance.enabled else None
    reference = ChangeRef(
        provider=ScmProvider.GITHUB,
        repository_external_id=config.scm.repository_external_id,
        change_external_id=arguments.change,
    )
    if arguments.scm_command == "sync-feedback":
        return _execute_feedback_sync(
            arguments,
            repository,
            config,
            client,
            database_path,
            reference,
            project_id,
        )
    publisher = SqliteScmPublicationPublisher(database_path, client, project_id=project_id)
    coordinator = ScmReviewCoordinator(
        config=config,
        scm_client=client,
        diff_provider=GitDiffAdapter(),
        review_executor=_ConfiguredReviewExecutor(),
        secret_scanner=DetectSecretsScanner(),
        publisher=publisher,
    )
    outcome = coordinator.execute(
        repository,
        reference,
        mode=ReviewMode(arguments.mode or config.review.mode),
        config_path=config_path,
        should_publish=arguments.publish,
    )
    is_dry_run = not (arguments.publish and config.scm.publish_enabled)
    write_document(
        _outcome_document(outcome, is_dry_run=is_dry_run),
        Path(arguments.output) if arguments.output else None,
    )
    if outcome.review.run.status is RunStatus.BLOCKED:
        return 4
    if outcome.review.run.status in {RunStatus.FAILED, RunStatus.PARTIAL_SUCCESS}:
        return 2
    if any(
        publication.status is not PublicationStatus.PUBLISHED
        for publication in outcome.publications
    ):
        return 2
    return 0


def _execute_feedback_sync(
    arguments: Namespace,
    repository: Path,
    config: AppConfig,
    client: GitHubScmClient,
    database_path: Path,
    reference: ChangeRef,
    project_id: ProjectId | None,
) -> int:
    """SCM コメントを M2 へ同期し、集計結果を出力する."""
    processor = ScmM2FeedbackProcessor(repository, config, database_path)
    try:
        outcome = SyncScmFeedback(
            config=config,
            scm_client=client,
            repository=SqliteScmFeedbackRepository(database_path, project_id),
            processor=processor,
        ).execute(reference)
    finally:
        processor.close()
    api_metrics = client.get_metrics()
    write_document(
        {
            "schema_version": 1,
            "scm_feedback_sync": {
                "provider": reference.provider.value,
                "repository": reference.repository_external_id,
                "change": reference.change_external_id,
                "fetched_count": outcome.fetched_count,
                "processed_count": outcome.processed_count,
                "ignored_count": outcome.ignored_count,
                "failed_count": outcome.failed_count,
                "duplicate_count": outcome.duplicate_count,
                "next_cursor": outcome.next_cursor,
                "api_metrics": {
                    "call_count": api_metrics.call_count,
                    "retry_count": api_metrics.retry_count,
                    "retry_wait_ms": api_metrics.retry_wait_ms,
                },
            },
        },
        Path(arguments.output) if arguments.output else None,
    )
    return 2 if outcome.failed_count else 0


def _outcome_document(outcome: ScmReviewOutcome, *, is_dry_run: bool) -> dict[str, object]:
    """SCM 固有情報と既存レビュー結果を結合した公開 JSON を返す."""
    document: dict[str, object] = result_to_document(outcome.review)
    document["scm"] = {
        "provider": outcome.change.ref.provider.value,
        "repository": outcome.change.ref.repository_external_id,
        "change": outcome.change.ref.change_external_id,
        "base_sha": outcome.change.base_sha,
        "head_sha": outcome.change.head_sha,
        "dry_run": is_dry_run,
        "metrics": {
            "api_call_count": outcome.metrics.api_call_count,
            "api_retry_count": outcome.metrics.api_retry_count,
            "api_retry_wait_ms": outcome.metrics.api_retry_wait_ms,
            "planned_inline_count": outcome.metrics.planned_inline_count,
            "planned_summary_count": outcome.metrics.planned_summary_count,
            "created_count": outcome.metrics.created_count,
            "updated_count": outcome.metrics.updated_count,
            "reused_count": outcome.metrics.reused_count,
            "recovered_count": outcome.metrics.recovered_count,
            "failed_count": outcome.metrics.failed_count,
            "stale_count": outcome.metrics.stale_count,
            "blocked_count": outcome.metrics.blocked_count,
            "head_change_abort_count": outcome.metrics.head_change_abort_count,
        },
        "plans": [
            {
                "kind": plan.kind.value,
                "finding_id": plan.finding_id,
                "request_hash": plan.request_hash,
                "file": (
                    plan.request.position.path.value if plan.request.position is not None else None
                ),
                "line": (plan.request.position.line if plan.request.position is not None else None),
            }
            for plan in outcome.plans
        ],
        "publications": [
            {
                "fingerprint_hash": item.fingerprint_hash,
                "status": item.status.value,
                "operation": item.operation.value,
                "external_comment_id": item.external_comment_id,
                "external_comment_url": item.external_comment_url,
                "safe_error_code": item.safe_error_code,
            }
            for item in outcome.publications
        ],
    }
    return document


def _within_repository(repository: Path, configured_path: Path) -> Path:
    """DB の設定パスをリポジトリ内へ制限する."""
    candidate = configured_path if configured_path.is_absolute() else repository / configured_path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(repository)
    except ValueError as exc:
        raise InvalidInputError(
            Messages.configured_path_outside_repository(Messages.DATABASE_PATH_LABEL)
        ) from exc
    return resolved
