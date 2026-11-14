"""M2 フィードバックコマンドの構成起点.

保存済みフィードバックと候補抽出の結果は分けて返す. 候補抽出が失敗しても、
保存済みフィードバックを未保存として表示しない.
"""

from __future__ import annotations

from argparse import Namespace
from dataclasses import replace
from pathlib import Path

from self_improving_code_review.application.config.settings import AppConfig, load_config
from self_improving_code_review.application.feedback.models import (
    FeedbackOutcome,
    FindingFeedbackInput,
    MissedIssueInput,
)
from self_improving_code_review.application.feedback.record_feedback import (
    RecordFindingFeedback,
    RecordMissedIssue,
)
from self_improving_code_review.application.memory.extract_candidate import ExtractMemoryCandidate
from self_improving_code_review.application.memory.extraction_context import (
    ExtractionContextBuilder,
)
from self_improving_code_review.application.memory.models import ExtractionRequest
from self_improving_code_review.application.review.prompt_loader import load_prompt_template
from self_improving_code_review.cli.output import write_document
from self_improving_code_review.cli.review_commands import _build_llm_client
from self_improving_code_review.domain.common.enums import ExtractionStatus, FeedbackLabel, Severity
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import (
    BundleId,
    FindingId,
    NormalizedPath,
    RunId,
)
from self_improving_code_review.infrastructure.git import GitRevisionContentReader
from self_improving_code_review.infrastructure.persistence import SqliteUnitOfWorkFactory
from self_improving_code_review.infrastructure.persistence.short_transaction import (
    ShortTransactionExtractionRepository,
    ShortTransactionFeedbackRepository,
    ShortTransactionMemoryRepository,
)
from self_improving_code_review.infrastructure.system import SystemClock, Uuid4IdGenerator
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.authorization import AllowListActorAuthorizer
from self_improving_code_review.security.secret_scanner import DetectSecretsScanner

__all__ = ["execute_feedback_command"]


def execute_feedback_command(arguments: Namespace) -> int:
    """CLI 引数からフィードバック保存を一回実行する.

    Args:
        arguments: argparse が検証したフィードバックコマンドの引数。

    Returns:
        成功は 0。

    Raises:
        AuthorizationError: 実行者に記録権限がない場合。
        InvalidInputError: 入力、設定、識別子の形式が不正な場合。
    """
    repository = Path(arguments.repository).resolve()
    config = load_config(Path(arguments.config) if arguments.config else None)
    factory = SqliteUnitOfWorkFactory(_database_path(repository, config))
    feedback_repository = ShortTransactionFeedbackRepository(factory)
    authorizer = AllowListActorAuthorizer(
        maintainers=config.memory.maintainers,
        is_production=config.memory.is_production,
    )
    clock = SystemClock()
    id_generator = Uuid4IdGenerator()

    if arguments.feedback_command == "finding":
        outcome = RecordFindingFeedback(
            feedback_repository=feedback_repository,
            authorizer=authorizer,
            clock=clock,
            id_generator=id_generator,
        ).execute(
            FindingFeedbackInput(
                run_id=RunId(arguments.run),
                finding_id=_parse_finding_id(arguments.run, arguments.finding),
                label=FeedbackLabel(arguments.label),
                reason=arguments.reason,
                actor_id=arguments.actor,
            )
        )
    else:
        outcome = RecordMissedIssue(
            feedback_repository=feedback_repository,
            authorizer=authorizer,
            clock=clock,
            id_generator=id_generator,
            revision_reader=GitRevisionContentReader(repository),
        ).execute(
            MissedIssueInput(
                run_id=RunId(arguments.run),
                file=NormalizedPath(arguments.file),
                issue_type=arguments.issue_type,
                severity=Severity(arguments.severity),
                description=arguments.description,
                evidence=arguments.evidence,
                actor_id=arguments.actor,
                start_line=arguments.start_line,
                symbol=arguments.symbol,
            )
        )

    run_context = feedback_repository.find_run_context(RunId(arguments.run))
    if run_context is None:
        raise InvalidInputError(Messages.feedback_run_not_found(arguments.run))
    context_text, context_locator = _extraction_context(
        arguments,
        repository,
        run_context.source_revision,
        feedback_repository,
    )

    llm_client = _build_llm_client(config, repository)
    try:
        extraction = ExtractMemoryCandidate(
            feedback_repository=feedback_repository,
            extraction_repository=ShortTransactionExtractionRepository(factory),
            memory_repository=ShortTransactionMemoryRepository(factory),
            llm_client=llm_client,
            secret_scanner=DetectSecretsScanner(),
            context_builder=ExtractionContextBuilder(
                load_prompt_template(repository / "prompts", config.memory.prompt_name),
                max_output_tokens=config.memory.extraction_max_output_tokens,
                temperature=config.llm.temperature,
            ),
            clock=clock,
            id_generator=id_generator,
            revalidation_default_days=(
                config.governance.revalidation_default_days
                if config.governance.memory_lifecycle_enabled
                else None
            ),
        ).execute(
            ExtractionRequest(
                project_id=run_context.project_id,
                feedback_ids=(outcome.feedback_id,),
                source_revision=run_context.source_revision,
                context_text=context_text,
                context_locator=context_locator,
                is_test_evaluation=run_context.is_test_evaluation,
            )
        )
    finally:
        llm_client.close()

    outcome = replace(
        outcome,
        extraction_id=extraction.extraction_id,
        candidate_status=extraction.status,
        candidate_memory_id=extraction.candidate_memory_id,
        candidate_version=extraction.candidate_version,
        safe_error_code=extraction.safe_error_code,
    )
    _write_outcome(outcome)
    if extraction.status is ExtractionStatus.BLOCKED:
        return 4
    if extraction.status is ExtractionStatus.FAILED:
        return 3 if extraction.safe_error_code == "InvalidLlmOutputError" else 2
    return 0


def _write_outcome(outcome: FeedbackOutcome) -> None:
    """保存結果を安定した形式で標準出力へ書き込む."""
    write_document(
        {
            "schema_version": 1,
            "feedback_id": str(outcome.feedback_id),
            "feedback_status": outcome.feedback_status,
            "is_duplicate": outcome.is_duplicate,
            "extraction_id": (
                str(outcome.extraction_id) if outcome.extraction_id is not None else None
            ),
            "candidate_status": (
                outcome.candidate_status.value if outcome.candidate_status is not None else None
            ),
            "candidate_memory_id": (
                str(outcome.candidate_memory_id)
                if outcome.candidate_memory_id is not None
                else None
            ),
            "candidate_version": (
                outcome.candidate_version.value if outcome.candidate_version is not None else None
            ),
            "safe_error_code": outcome.safe_error_code,
        },
        None,
    )


def _database_path(repository: Path, config: AppConfig) -> Path:
    """設定されたデータベースパスを解決し、リポジトリ内に制限する.

    Raises:
        InvalidInputError: パスがリポジトリ外を指す場合。
    """
    configured = config.database.path
    candidate = (
        configured.resolve() if configured.is_absolute() else (repository / configured).resolve()
    )
    if not candidate.is_relative_to(repository):
        raise InvalidInputError(Messages.configured_path_outside_repository("データベースのパス"))
    return candidate


def _parse_finding_id(raw_run_id: str, raw_finding_id: str) -> FindingId:
    """``B001-F002`` 形式の引数を指摘識別子へ変換する.

    Raises:
        InvalidInputError: 形式が規約に一致しない場合。
    """
    bundle_part, separator, finding_part = raw_finding_id.partition("-F")
    if not separator or not bundle_part.startswith("B"):
        message = Messages.invalid_finding_id_format(raw_finding_id)
        raise InvalidInputError(message)
    try:
        bundle_sequence = int(bundle_part.removeprefix("B"))
        finding_sequence = int(finding_part)
    except ValueError as exc:
        message = Messages.finding_id_sequence_not_numeric(raw_finding_id)
        raise InvalidInputError(message) from exc
    return FindingId(
        bundle_id=BundleId(run_id=RunId(raw_run_id), sequence=bundle_sequence),
        sequence=finding_sequence,
    )


def _extraction_context(
    arguments: Namespace,
    repository: Path,
    source_revision: str | None,
    feedback_repository: ShortTransactionFeedbackRepository,
) -> tuple[str, str]:
    """検証済み指摘または対象改訂から必要最小限の抽出コンテキストを返す."""
    run_id = RunId(arguments.run)
    if arguments.feedback_command == "finding":
        context = feedback_repository.find_finding_context(run_id, arguments.finding)
        if context is None:
            raise InvalidInputError(Messages.FEEDBACK_RUN_NOT_FOUND)
        return context, f"finding:{arguments.finding}"
    if source_revision is None:
        raise InvalidInputError(Messages.FEEDBACK_REVISION_REQUIRED)
    path = NormalizedPath(arguments.file)
    source = GitRevisionContentReader(repository).read_text(source_revision, path)
    if source is None:
        raise InvalidInputError(Messages.MISSED_ISSUE_FILE_NOT_FOUND)
    lines = source.splitlines()
    target_index = (arguments.start_line - 1) if arguments.start_line is not None else 0
    if arguments.start_line is None and arguments.symbol:
        target_index = next(
            (index for index, line in enumerate(lines) if arguments.symbol in line),
            0,
        )
    start = max(target_index - 10, 0)
    end = min(target_index + 11, len(lines))
    numbered = "\n".join(f"{index + 1}: {lines[index]}" for index in range(start, end))
    return numbered, f"{path.value}:{arguments.start_line or arguments.symbol}"
