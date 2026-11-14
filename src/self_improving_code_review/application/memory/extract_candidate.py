"""メモリ候補抽出ユースケース.

外部 LLM 呼び出し中はトランザクションを保持しない. 抽出意図の作成、
LLM 呼び出し、結果保存をそれぞれ短い作業単位で区切る.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from self_improving_code_review.application.memory.candidate_policy import build_candidate_draft
from self_improving_code_review.application.memory.candidate_schema import (
    validate_memory_candidate_payload,
)
from self_improving_code_review.application.memory.extraction_context import (
    ExtractionContextBuilder,
    render_feedback_payload,
)
from self_improving_code_review.application.memory.extraction_key import (
    build_extraction_key,
    feedback_content_hash,
)
from self_improving_code_review.application.memory.models import (
    ExtractionOutcome,
    ExtractionRequest,
)
from self_improving_code_review.application.ports.clock import Clock, IdGenerator
from self_improving_code_review.application.ports.llm_client import LlmClient
from self_improving_code_review.application.ports.memory_persistence import (
    ExtractionEvidence,
    ExtractionRecord,
    ExtractionRepository,
    FeedbackRepository,
    MemoryEvidenceLink,
    MemoryRepository,
)
from self_improving_code_review.application.ports.secret_scanner import SecretScanner
from self_improving_code_review.domain.common.enums import (
    ExtractionStatus,
    MemoryStatus,
)
from self_improving_code_review.domain.common.errors import (
    InfrastructureError,
    InvalidInputError,
    InvalidLlmOutputError,
    LlmClientError,
    PolicyViolationError,
)
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.common.values import FeedbackId, MemoryId, MemoryVersion
from self_improving_code_review.domain.feedback.models import FeedbackRecord
from self_improving_code_review.domain.memory.models import (
    MemoryContent,
    MemoryRecord,
    MemoryTransition,
)
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.models import SourceKind, SourcePayload

__all__ = ["ExtractMemoryCandidate"]

_EVIDENCE_RELATION = "derived-from"
_INITIAL_VERSION = MemoryVersion(1)


class ExtractMemoryCandidate:
    """検証済みフィードバックから ``PENDING`` のメモリ候補を作成する."""

    def __init__(
        self,
        *,
        feedback_repository: FeedbackRepository,
        extraction_repository: ExtractionRepository,
        memory_repository: MemoryRepository,
        llm_client: LlmClient,
        secret_scanner: SecretScanner,
        context_builder: ExtractionContextBuilder,
        clock: Clock,
        id_generator: IdGenerator,
        revalidation_default_days: int | None = None,
    ) -> None:
        """依存するリポジトリとポートを保持する."""
        self._feedback_repository = feedback_repository
        self._extraction_repository = extraction_repository
        self._memory_repository = memory_repository
        self._llm_client = llm_client
        self._secret_scanner = secret_scanner
        self._context_builder = context_builder
        self._clock = clock
        self._id_generator = id_generator
        self._revalidation_default_days = revalidation_default_days

    def execute(self, request: ExtractionRequest) -> ExtractionOutcome:
        """候補抽出を 1 回実行する.

        Args:
            request: プロジェクト、根拠フィードバック、コンテキストを含む入力.

        Returns:
            抽出実行の状態、候補参照、使用量.

        Raises:
            InvalidInputError: 根拠フィードバックが存在しない場合.
        """
        if request.is_test_evaluation:
            raise InvalidInputError(Messages.MEMORY_EXTRACTION_TEST_DATA_FORBIDDEN)
        feedback = self._load_feedback(request.feedback_ids)
        key = build_extraction_key(
            project_id=request.project_id,
            source_revision=request.source_revision,
            feedback=feedback,
            context_locator=request.context_locator,
            prompt_hash=self._context_builder.prompt_hash,
            model=self._llm_client.model,
        )

        existing = self._extraction_repository.find_by_idempotency_key(key)
        if existing is not None and existing.status.is_terminal:
            return _replayed_outcome(existing)

        record = existing or self._create_extraction(request, key, feedback)
        attempt = record.attempt + 1 if existing is not None else record.attempt
        if not self._extraction_repository.acquire(record.extraction_id, attempt):
            return ExtractionOutcome(
                extraction_id=record.extraction_id,
                status=ExtractionStatus.RUNNING,
                attempt=attempt,
                safe_error_code="EXTRACTION_IN_PROGRESS",
            )

        return self._run(record, attempt, request, feedback)

    def _run(
        self,
        record: ExtractionRecord,
        attempt: int,
        request: ExtractionRequest,
        feedback: tuple[FeedbackRecord, ...],
    ) -> ExtractionOutcome:
        """機密情報検査、LLM 呼び出し、候補保存を順に実行する."""
        feedback_text = render_feedback_payload(feedback)
        payload_text = feedback_text
        if request.context_text:
            payload_text = f"{payload_text}\n{request.context_text}"
        scan_result, scanned = self._secret_scanner.scan_and_seal(
            SourcePayload(kind=SourceKind.DIFF, label="memory-extraction", text=payload_text)
        )
        if scanned is None:
            return self._finalize(
                record,
                attempt,
                status=ExtractionStatus.BLOCKED,
                safe_error_code="SECRET_DETECTED",
                request_hash=scan_result.payload_hash,
            )

        try:
            built = self._context_builder.build(
                scanned,
                project_id=str(request.project_id),
                feedback_text=feedback_text,
                context_text=request.context_text,
            )
        except PolicyViolationError:
            return self._finalize(
                record,
                attempt,
                status=ExtractionStatus.BLOCKED,
                safe_error_code="PROMPT_BOUNDARY_VIOLATION",
                request_hash=scan_result.payload_hash,
            )
        request_hash = hash_text(
            built.request.system_instruction + "\n" + built.request.user_content
        )
        try:
            result = self._llm_client.generate_structured(built.request)
            validate_memory_candidate_payload(result.payload)
            draft = build_candidate_draft(result.payload)
        except (LlmClientError, InvalidLlmOutputError) as exc:
            return self._finalize(
                record,
                attempt,
                status=ExtractionStatus.FAILED,
                safe_error_code=type(exc).__name__,
                request_hash=request_hash,
                input_tokens=exc.input_tokens,
                output_tokens=exc.output_tokens,
                latency_ms=exc.latency_ms,
                cost_usd=exc.cost_usd,
            )
        except PolicyViolationError as exc:
            return self._finalize(
                record,
                attempt,
                status=ExtractionStatus.FAILED,
                safe_error_code=type(exc).__name__,
                request_hash=request_hash,
            )

        created_at = self._clock.now_utc()
        memory_id = self._id_generator.new_memory_id()
        content = MemoryContent(
            memory_id=memory_id,
            version=_INITIAL_VERSION,
            category=draft.category,
            scope=draft.scope,
            fact=draft.fact,
            review_instruction=draft.review_instruction,
            polarity=draft.polarity,
            confidence=draft.confidence,
            evidence_summary=draft.evidence_summary,
            created_by=f"extraction:{record.extraction_id}",
            created_at=created_at,
            valid_from=(created_at if self._revalidation_default_days is not None else None),
            review_after=(
                created_at + timedelta(days=self._revalidation_default_days)
                if self._revalidation_default_days is not None
                else None
            ),
        )
        memory = MemoryRecord(
            memory_id=memory_id,
            project_id=request.project_id,
            current_version=_INITIAL_VERSION,
            current_status=MemoryStatus.PENDING,
            lock_version=1,
            created_at=created_at,
            updated_at=created_at,
        )
        evidence = tuple(
            MemoryEvidenceLink(feedback_id=item.feedback_id, relation=_EVIDENCE_RELATION)
            for item in feedback
        )
        transition = MemoryTransition(
            memory_id=memory_id,
            memory_version=_INITIAL_VERSION,
            from_status=None,
            to_status=MemoryStatus.PENDING,
            actor_id=f"extraction:{record.extraction_id}",
            reason="memory candidate extracted from feedback",
            created_at=created_at,
        )
        finished = replace(
            record,
            status=ExtractionStatus.SUCCESS,
            attempt=attempt,
            request_hash=request_hash,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            latency_ms=result.usage.latency_ms,
            cost_usd=result.usage.cost_usd,
            candidate_memory_id=memory_id,
            candidate_version=_INITIAL_VERSION,
            finished_at=self._clock.now_utc(),
        )
        try:
            self._extraction_repository.finalize_success(
                finished,
                memory,
                content,
                evidence,
                transition,
            )
        except InfrastructureError:
            with suppress(InfrastructureError):
                self._finalize(
                    record,
                    attempt,
                    status=ExtractionStatus.FAILED,
                    safe_error_code="PERSISTENCE_FAILED",
                    request_hash=request_hash,
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                    latency_ms=result.usage.latency_ms,
                    cost_usd=result.usage.cost_usd,
                )
            raise
        return _replayed_outcome(finished, is_replayed=False)

    def _load_feedback(self, feedback_ids: tuple[FeedbackId, ...]) -> tuple[FeedbackRecord, ...]:
        """根拠フィードバックを読み込み、欠落を拒否する.

        Raises:
            InvalidInputError: 根拠が空、または一部が存在しない場合.
        """
        if not feedback_ids:
            message = Messages.MEMORY_EXTRACTION_FEEDBACK_REQUIRED
            raise InvalidInputError(message)
        records = self._feedback_repository.find_by_ids(feedback_ids)
        if len(records) != len(frozenset(item.value for item in feedback_ids)):
            message = Messages.MEMORY_EXTRACTION_FEEDBACK_MISSING
            raise InvalidInputError(message)
        return records

    def _create_extraction(
        self,
        request: ExtractionRequest,
        key: str,
        feedback: tuple[FeedbackRecord, ...],
    ) -> ExtractionRecord:
        """抽出意図を ``PENDING`` として作成し、根拠集合を固定する."""
        record = ExtractionRecord(
            extraction_id=self._id_generator.new_extraction_id(),
            project_id=request.project_id,
            source_revision=request.source_revision,
            idempotency_key=key,
            status=ExtractionStatus.PENDING,
            attempt=1,
            prompt_version=self._context_builder.prompt_version,
            prompt_hash=self._context_builder.prompt_hash,
            model=self._llm_client.model,
            started_at=self._clock.now_utc(),
        )
        self._extraction_repository.create(
            record,
            tuple(
                ExtractionEvidence(
                    feedback_id=item.feedback_id,
                    feedback_content_hash=feedback_content_hash(item),
                )
                for item in feedback
            ),
        )
        return record

    def _finalize(
        self,
        record: ExtractionRecord,
        attempt: int,
        *,
        status: ExtractionStatus,
        request_hash: str,
        safe_error_code: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
        cost_usd: Decimal = Decimal("0"),
        candidate_memory_id: MemoryId | None = None,
    ) -> ExtractionOutcome:
        """抽出実行の最終状態と使用量を保存する."""
        finished = replace(
            record,
            status=status,
            attempt=attempt,
            request_hash=request_hash,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            safe_error_code=safe_error_code,
            candidate_memory_id=candidate_memory_id,
            candidate_version=_INITIAL_VERSION if candidate_memory_id is not None else None,
            finished_at=self._clock.now_utc(),
        )
        self._extraction_repository.finalize(finished)
        return ExtractionOutcome(
            extraction_id=finished.extraction_id,
            status=status,
            attempt=attempt,
            candidate_memory_id=candidate_memory_id,
            candidate_version=finished.candidate_version,
            safe_error_code=safe_error_code,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
        )


def _replayed_outcome(
    record: ExtractionRecord,
    *,
    is_replayed: bool = True,
) -> ExtractionOutcome:
    """完了済み抽出実行の結果をそのまま返す."""
    return ExtractionOutcome(
        extraction_id=record.extraction_id,
        status=record.status,
        attempt=record.attempt,
        candidate_memory_id=record.candidate_memory_id,
        candidate_version=record.candidate_version,
        safe_error_code=record.safe_error_code,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        latency_ms=record.latency_ms,
        cost_usd=record.cost_usd,
        is_replayed=is_replayed,
    )
