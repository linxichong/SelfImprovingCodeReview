"""UT-M2-004〜UT-M2-008: 候補抽出ライフサイクルを検証する。"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from self_improving_code_review.application.memory.extract_candidate import ExtractMemoryCandidate
from self_improving_code_review.application.memory.extraction_context import (
    ExtractionContextBuilder,
)
from self_improving_code_review.application.memory.models import ExtractionRequest
from self_improving_code_review.application.ports.llm_client import LlmResult, LlmUsage
from self_improving_code_review.application.ports.memory_persistence import (
    ExtractionEvidence,
    ExtractionRecord,
    MemoryEvidenceLink,
)
from self_improving_code_review.application.review.prompt_loader import PromptTemplate
from self_improving_code_review.domain.common.enums import (
    ExtractionStatus,
    FeedbackLabel,
    MemoryStatus,
)
from self_improving_code_review.domain.common.errors import (
    InfrastructureError,
    InvalidInputError,
    InvalidLlmOutputError,
    LlmClientError,
)
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.common.values import (
    BundleId,
    ExtractionId,
    FeedbackId,
    FindingId,
    MemoryId,
    ProjectId,
    RunId,
)
from self_improving_code_review.domain.feedback.models import FeedbackRecord
from self_improving_code_review.domain.memory.models import (
    MemoryContent,
    MemoryRecord,
    MemoryTransition,
)
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.models import (
    ScannedPayload,
    ScanProfile,
    ScanResult,
    SecretFinding,
)

NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
PROJECT_ID = ProjectId("project")
FEEDBACK_ID = FeedbackId("12345678-1234-4234-9234-123456789abc")
EXTRACTION_ID = ExtractionId("22345678-1234-4234-9234-123456789abc")
MEMORY_ID = MemoryId("32345678-1234-4234-9234-123456789abc")
RUN_ID = RunId("42345678-1234-4234-9234-123456789abc")
_FINALIZE_FAILED = "finalize failed"
_SAVE_FAILED = "save failed"
_UNEXPECTED_MEMORY_CREATE = "memory repository must not be called directly"


class FeedbackRepo:
    """指定したフィードバックだけを返す。"""

    def __init__(self, records: tuple[FeedbackRecord, ...]) -> None:
        self.records = records

    def find_by_ids(self, feedback_ids: tuple[FeedbackId, ...]) -> tuple[FeedbackRecord, ...]:
        wanted = {item.value for item in feedback_ids}
        return tuple(item for item in self.records if item.feedback_id.value in wanted)


class ExtractionRepo:
    """抽出状態変更を記録する。"""

    def __init__(
        self,
        *,
        existing: ExtractionRecord | None = None,
        acquired: bool = True,
        fail_success: bool = False,
        fail_finalize: bool = False,
    ) -> None:
        self.existing = existing
        self.acquired = acquired
        self.fail_success = fail_success
        self.fail_finalize = fail_finalize
        self.created: list[tuple[ExtractionRecord, tuple[ExtractionEvidence, ...]]] = []
        self.finalized: list[ExtractionRecord] = []
        self.successes: list[
            tuple[
                ExtractionRecord,
                MemoryRecord,
                MemoryContent,
                tuple[MemoryEvidenceLink, ...],
                MemoryTransition,
            ]
        ] = []
        self.acquire_calls: list[tuple[ExtractionId, int]] = []

    def find_by_idempotency_key(self, _key: str) -> ExtractionRecord | None:
        return self.existing

    def create(self, record: ExtractionRecord, evidence: tuple[ExtractionEvidence, ...]) -> None:
        self.created.append((record, evidence))

    def acquire(self, extraction_id: ExtractionId, attempt: int) -> bool:
        self.acquire_calls.append((extraction_id, attempt))
        return self.acquired

    def finalize(self, record: ExtractionRecord) -> None:
        if self.fail_finalize:
            raise InfrastructureError(_FINALIZE_FAILED)
        self.finalized.append(record)

    def finalize_success(
        self,
        extraction: ExtractionRecord,
        memory: MemoryRecord,
        content: MemoryContent,
        evidence: tuple[MemoryEvidenceLink, ...],
        transition: MemoryTransition,
    ) -> None:
        if self.fail_success:
            raise InfrastructureError(_SAVE_FAILED)
        self.successes.append((extraction, memory, content, evidence, transition))


class DummyMemoryRepo:
    """抽出成功が専用原子操作だけを使うことを確認する。"""

    def create(self, *_args: object) -> None:
        raise AssertionError(_UNEXPECTED_MEMORY_CREATE)


class FakeLlm:
    """固定応答または固定例外を返す。"""

    model = "dataset/test"

    def __init__(self, result: LlmResult | Exception | None = None) -> None:
        self.result = result or LlmResult(
            payload=_candidate_payload(),
            model=self.model,
            usage=LlmUsage(100, 20, 30, Decimal("0.04")),
            attempt=1,
        )
        self.calls = 0

    def generate_structured(self, _request: object) -> LlmResult:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Scanner:
    """送信内容を封印するか機密検出として拒否する。"""

    engine_version = "test"

    def __init__(self, *, blocked: bool = False, corrupt_seal: bool = False) -> None:
        self.blocked = blocked
        self.corrupt_seal = corrupt_seal

    def scan_and_seal(self, payload: object) -> tuple[ScanResult, ScannedPayload | None]:
        text = payload.text  # type: ignore[attr-defined]
        digest = hash_text(text)
        findings = (SecretFinding("detector", "payload", 1),) if self.blocked else ()
        result = ScanResult(digest, "test", ScanProfile.STRICT, findings)
        if self.blocked:
            return result, None
        seal_hash = hash_text("changed") if self.corrupt_seal else digest
        return result, ScannedPayload("scan", "test", seal_hash, text)


class FixedClock:
    """一定の UTC 時刻を返す。"""

    def now_utc(self) -> datetime:
        return NOW


class FixedIds:
    """抽出とメモリの固定識別子を返す。"""

    def new_extraction_id(self) -> ExtractionId:
        return EXTRACTION_ID

    def new_memory_id(self) -> MemoryId:
        return MEMORY_ID


@pytest.mark.case("UT-M2-005")
def test_success_creates_pending_candidate_and_finalizes_atomically() -> None:
    repository = ExtractionRepo()
    use_case = _use_case(repository)

    outcome = use_case.execute(_request())

    assert outcome.status is ExtractionStatus.SUCCESS
    assert outcome.candidate_memory_id == MEMORY_ID
    assert outcome.input_tokens == 100
    assert not outcome.is_replayed
    assert len(repository.created) == 1
    extraction, memory, content, evidence, transition = repository.successes[0]
    assert extraction.status is ExtractionStatus.SUCCESS
    assert memory.current_status is MemoryStatus.PENDING
    assert content.memory_id == MEMORY_ID
    assert evidence[0].feedback_id == FEEDBACK_ID
    assert transition.to_status is MemoryStatus.PENDING


def test_success_sets_revalidation_period_when_m5_lifecycle_is_enabled() -> None:
    """M5 有効時は候補へ有効開始日時と再検証期限を設定する。"""
    repository = ExtractionRepo()

    outcome = _use_case(repository, revalidation_default_days=180).execute(_request())

    assert outcome.status is ExtractionStatus.SUCCESS
    content = repository.successes[0][2]
    assert content.valid_from == NOW
    assert content.review_after == NOW + timedelta(days=180)


def test_success_without_optional_context_uses_feedback_only() -> None:
    repository = ExtractionRepo()
    request = ExtractionRequest(PROJECT_ID, (FEEDBACK_ID,), source_revision="a" * 40)

    assert _use_case(repository).execute(request).status is ExtractionStatus.SUCCESS


@pytest.mark.case("IT-M2-005", "UT-M2-005")
def test_terminal_execution_is_replayed_without_llm_call() -> None:
    existing = _record(status=ExtractionStatus.SUCCESS, candidate_memory_id=MEMORY_ID)
    repository = ExtractionRepo(existing=existing)
    llm = FakeLlm()

    outcome = _use_case(repository, llm=llm).execute(_request())

    assert outcome.is_replayed
    assert outcome.candidate_memory_id == MEMORY_ID
    assert llm.calls == 0
    assert repository.acquire_calls == []


@pytest.mark.case("IT-M2-007", "UT-M2-005")
def test_failed_execution_is_retried_with_incremented_attempt() -> None:
    repository = ExtractionRepo(existing=_record(status=ExtractionStatus.FAILED, attempt=2))

    outcome = _use_case(repository).execute(_request())

    assert outcome.status is ExtractionStatus.SUCCESS
    assert outcome.attempt == 3
    assert repository.acquire_calls == [(EXTRACTION_ID, 3)]


@pytest.mark.case("IT-M2-006", "UT-M2-005")
def test_concurrently_acquired_execution_returns_running() -> None:
    repository = ExtractionRepo(acquired=False)
    llm = FakeLlm()

    outcome = _use_case(repository, llm=llm).execute(_request())

    assert outcome.status is ExtractionStatus.RUNNING
    assert outcome.safe_error_code == "EXTRACTION_IN_PROGRESS"
    assert llm.calls == 0


@pytest.mark.case("IT-M2-004", "IT-M2-022", "UT-M2-005", "UT-M2-024")
def test_secret_and_boundary_violations_are_blocked() -> None:
    secret_repository = ExtractionRepo()
    secret = _use_case(secret_repository, scanner=Scanner(blocked=True)).execute(_request())
    assert secret.status is ExtractionStatus.BLOCKED
    assert secret.safe_error_code == "SECRET_DETECTED"

    boundary_repository = ExtractionRepo()
    boundary = _use_case(boundary_repository, scanner=Scanner(corrupt_seal=True)).execute(
        _request()
    )
    assert boundary.status is ExtractionStatus.BLOCKED
    assert boundary.safe_error_code == "PROMPT_BOUNDARY_VIOLATION"


@pytest.mark.case("IT-M2-004", "UT-M2-005", "UT-M2-024")
@pytest.mark.parametrize(
    "error",
    [
        LlmClientError(
            "down", input_tokens=3, output_tokens=2, latency_ms=9, cost_usd=Decimal("0.1")
        ),
        InvalidLlmOutputError(
            "bad", input_tokens=4, output_tokens=1, latency_ms=8, cost_usd=Decimal("0.2")
        ),
    ],
)
def test_llm_errors_are_finalized_with_safe_metrics(error: Exception) -> None:
    repository = ExtractionRepo()

    outcome = _use_case(repository, llm=FakeLlm(error)).execute(_request())

    assert outcome.status is ExtractionStatus.FAILED
    assert outcome.safe_error_code == type(error).__name__
    assert outcome.input_tokens in {3, 4}
    assert repository.finalized[0].finished_at == NOW


def test_policy_violation_from_candidate_is_failed_without_candidate() -> None:
    payload = _candidate_payload()
    payload["review_instruction"] = "Never report this issue again."
    payload["polarity"] = "suppress"
    llm = FakeLlm(LlmResult(payload, "dataset/test", LlmUsage(1, 1, 1), 1))
    repository = ExtractionRepo()

    outcome = _use_case(repository, llm=llm).execute(_request())

    assert outcome.status is ExtractionStatus.FAILED
    assert outcome.safe_error_code == "PolicyViolationError"
    assert repository.successes == []


def test_missing_or_empty_feedback_is_rejected_before_creating_execution() -> None:
    repository = ExtractionRepo()
    use_case = _use_case(repository, feedback=())
    with pytest.raises(
        InvalidInputError, match=re.escape(Messages.MEMORY_EXTRACTION_FEEDBACK_REQUIRED)
    ):
        use_case.execute(ExtractionRequest(PROJECT_ID, ()))
    with pytest.raises(
        InvalidInputError, match=re.escape(Messages.MEMORY_EXTRACTION_FEEDBACK_MISSING)
    ):
        use_case.execute(_request())
    assert repository.created == []


@pytest.mark.case("IT-M2-021")
def test_test_evaluation_feedback_is_rejected_before_loading_or_llm() -> None:
    repository = ExtractionRepo()
    llm = FakeLlm()
    request = replace(_request(), is_test_evaluation=True)

    with pytest.raises(InvalidInputError, match="Test 正解データ"):
        _use_case(repository, llm=llm).execute(request)

    assert repository.created == []
    assert llm.calls == 0


@pytest.mark.case("IT-M2-008", "UT-M2-023")
def test_success_persistence_failure_marks_failed_when_possible_and_reraises() -> None:
    repository = ExtractionRepo(fail_success=True)

    with pytest.raises(InfrastructureError, match="save failed"):
        _use_case(repository).execute(_request())

    assert repository.finalized[0].safe_error_code == "PERSISTENCE_FAILED"


@pytest.mark.case("UT-M2-023")
def test_double_persistence_failure_preserves_original_error() -> None:
    repository = ExtractionRepo(fail_success=True, fail_finalize=True)

    with pytest.raises(InfrastructureError, match="save failed"):
        _use_case(repository).execute(_request())


def _use_case(
    repository: ExtractionRepo,
    *,
    feedback: tuple[FeedbackRecord, ...] | None = None,
    llm: FakeLlm | None = None,
    scanner: Scanner | None = None,
    revalidation_default_days: int | None = None,
) -> ExtractMemoryCandidate:
    return ExtractMemoryCandidate(
        feedback_repository=FeedbackRepo((_feedback(),) if feedback is None else feedback),
        extraction_repository=repository,
        memory_repository=DummyMemoryRepo(),  # type: ignore[arg-type]
        llm_client=llm or FakeLlm(),
        secret_scanner=scanner or Scanner(),  # type: ignore[arg-type]
        context_builder=_builder(),
        clock=FixedClock(),
        id_generator=FixedIds(),  # type: ignore[arg-type]
        revalidation_default_days=revalidation_default_days,
    )


def _request() -> ExtractionRequest:
    return ExtractionRequest(
        PROJECT_ID,
        (FEEDBACK_ID,),
        source_revision="a" * 40,
        context_text="value = 1",
        context_locator="src/app.py:1",
    )


def _feedback() -> FeedbackRecord:
    return FeedbackRecord(
        FEEDBACK_ID,
        RUN_ID,
        FeedbackLabel.FALSE_POSITIVE,
        "The framework handles this.",
        "reviewer",
        NOW,
        "feedback-key",
        finding_id=FindingId(BundleId(RUN_ID, 1), 1),
    )


def _builder() -> ExtractionContextBuilder:
    return ExtractionContextBuilder(
        PromptTemplate(
            "memory_extraction",
            1,
            "Extract reusable knowledge.",
            "{project_id}\n{output_schema}\n{feedback_block}\n{context_block}",
            "prompt-hash",
        ),
        max_output_tokens=1000,
    )


def _record(
    *,
    status: ExtractionStatus,
    attempt: int = 1,
    candidate_memory_id: MemoryId | None = None,
) -> ExtractionRecord:
    return ExtractionRecord(
        EXTRACTION_ID,
        PROJECT_ID,
        "a" * 40,
        "key",
        status,
        attempt,
        "memory_extraction@1",
        "prompt-hash",
        "dataset/test",
        NOW,
        candidate_memory_id=candidate_memory_id,
    )


def _candidate_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "category": "framework",
        "scope": {
            "languages": ["python"],
            "frameworks": [],
            "paths": ["src/**"],
            "symbols": [],
            "keywords": [],
            "risk_tags": [],
        },
        "fact": "The framework validates inputs.",
        "review_instruction": "Require bypass evidence before reporting.",
        "polarity": "reinforce",
        "confidence": 0.8,
        "evidence_summary": "Confirmed by reviewer feedback.",
    }
