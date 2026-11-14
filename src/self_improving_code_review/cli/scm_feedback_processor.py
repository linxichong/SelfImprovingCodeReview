"""SCM 入力を既存 M2 フィードバック保存と候補抽出へ接続する."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from self_improving_code_review.application.config.settings import AppConfig
from self_improving_code_review.application.feedback.models import (
    FeedbackOutcome,
    FindingFeedbackInput,
)
from self_improving_code_review.application.feedback.record_feedback import (
    RecordFindingFeedback,
)
from self_improving_code_review.application.memory.extract_candidate import (
    ExtractMemoryCandidate,
)
from self_improving_code_review.application.memory.extraction_context import (
    ExtractionContextBuilder,
)
from self_improving_code_review.application.memory.models import ExtractionRequest
from self_improving_code_review.application.ports.llm_client import LlmClient
from self_improving_code_review.application.review.prompt_loader import load_prompt_template
from self_improving_code_review.application.scm.models import (
    ParsedFeedbackCommand,
    ScmFeedbackTarget,
)
from self_improving_code_review.cli.review_commands import _build_llm_client
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import BundleId, FindingId, RunId
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

__all__ = ["ScmM2FeedbackProcessor"]


class ScmM2FeedbackProcessor:
    """一つの SCM 同期内で M2 保存と LLM 候補抽出を再利用する."""

    def __init__(self, repository: Path, config: AppConfig, database_path: Path) -> None:
        """M2 の短期リポジトリ、LLM、プロンプトおよび安全境界を構成する."""
        factory = SqliteUnitOfWorkFactory(database_path)
        self._source_repository = repository
        self._config = config
        self._factory = factory
        self._repository = ShortTransactionFeedbackRepository(factory)
        self._clock = SystemClock()
        self._id_generator = Uuid4IdGenerator()
        self._recorder = RecordFindingFeedback(
            feedback_repository=self._repository,
            authorizer=AllowListActorAuthorizer(
                maintainers=config.memory.maintainers,
                is_production=config.memory.is_production,
            ),
            clock=self._clock,
            id_generator=self._id_generator,
        )
        self._llm_client: LlmClient | None = None
        self._extractor: ExtractMemoryCandidate | None = None

    def _build_extractor(self) -> ExtractMemoryCandidate:
        """最初の有効イベント時だけ LLM と候補抽出処理を構成する."""
        llm_client = _build_llm_client(self._config, self._source_repository)
        self._llm_client = llm_client
        return ExtractMemoryCandidate(
            feedback_repository=self._repository,
            extraction_repository=ShortTransactionExtractionRepository(self._factory),
            memory_repository=ShortTransactionMemoryRepository(self._factory),
            llm_client=llm_client,
            secret_scanner=DetectSecretsScanner(),
            context_builder=ExtractionContextBuilder(
                load_prompt_template(
                    self._source_repository / "prompts",
                    self._config.memory.prompt_name,
                ),
                max_output_tokens=self._config.memory.extraction_max_output_tokens,
                temperature=self._config.llm.temperature,
            ),
            clock=self._clock,
            id_generator=self._id_generator,
            revalidation_default_days=(
                self._config.governance.revalidation_default_days
                if self._config.governance.memory_lifecycle_enabled
                else None
            ),
        )

    def process(
        self,
        target: ScmFeedbackTarget,
        command: ParsedFeedbackCommand,
        actor_id: str,
    ) -> FeedbackOutcome:
        """SCM の一評価を保存し、同じフィードバックから候補を抽出する."""
        run_id = RunId(target.run_id)
        finding_id = _parse_finding_id(run_id, target.finding_id)
        outcome = self._recorder.execute(
            FindingFeedbackInput(
                run_id=run_id,
                finding_id=finding_id,
                label=command.label,
                reason=command.reason,
                actor_id=actor_id,
            )
        )
        run_context = self._repository.find_run_context(run_id)
        context = self._repository.find_finding_context(run_id, target.finding_id)
        if run_context is None or context is None:
            raise InvalidInputError(Messages.FEEDBACK_RUN_NOT_FOUND)
        if self._extractor is None:
            self._extractor = self._build_extractor()
        extraction = self._extractor.execute(
            ExtractionRequest(
                project_id=run_context.project_id,
                feedback_ids=(outcome.feedback_id,),
                source_revision=run_context.source_revision,
                context_text=context,
                context_locator=f"finding:{target.finding_id}",
                is_test_evaluation=run_context.is_test_evaluation,
            )
        )
        return replace(
            outcome,
            extraction_id=extraction.extraction_id,
            candidate_status=extraction.status,
            candidate_memory_id=extraction.candidate_memory_id,
            candidate_version=extraction.candidate_version,
            safe_error_code=extraction.safe_error_code,
        )

    def close(self) -> None:
        """同期で共有した LLM クライアントを終了する."""
        if self._llm_client is not None:
            self._llm_client.close()


def _parse_finding_id(run_id: RunId, raw_finding_id: str) -> FindingId:
    """保存済み Bnnn-Fnnn 表現を値オブジェクトへ変換する."""
    bundle_part, separator, finding_part = raw_finding_id.partition("-F")
    if not separator or not bundle_part.startswith("B"):
        raise InvalidInputError(Messages.SCM_FEEDBACK_TARGET_INVALID)
    try:
        bundle_sequence = int(bundle_part.removeprefix("B"))
        finding_sequence = int(finding_part)
    except ValueError as exc:
        raise InvalidInputError(Messages.SCM_FEEDBACK_TARGET_INVALID) from exc
    return FindingId(
        bundle_id=BundleId(run_id=run_id, sequence=bundle_sequence),
        sequence=finding_sequence,
    )
