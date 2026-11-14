"""M2 SQLite 永続化契約を検証する。"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from self_improving_code_review.application.ports.memory_persistence import (
    ExtractionEvidence,
    ExtractionRecord,
    MemoryEvidenceLink,
)
from self_improving_code_review.application.review.models import RunRecord, RunVersions
from self_improving_code_review.domain.common.enums import (
    ExtractionStatus,
    FeedbackLabel,
    MemoryCategory,
    MemoryPolarity,
    MemoryStatus,
    ReviewMode,
    RunStatus,
    Severity,
)
from self_improving_code_review.domain.common.errors import (
    ConcurrencyConflictError,
    DatabaseError,
)
from self_improving_code_review.domain.common.values import (
    ExtractionId,
    FeedbackId,
    MemoryId,
    MemoryVersion,
    NormalizedPath,
    ProjectId,
    RunId,
)
from self_improving_code_review.domain.feedback.models import FeedbackRecord, IssueLocation
from self_improving_code_review.domain.memory.models import (
    MemoryContent,
    MemoryRecord,
    MemorySnapshot,
    MemorySnapshotItem,
    MemoryTransition,
)
from self_improving_code_review.domain.memory.scope import MemoryScope
from self_improving_code_review.domain.rule.loader import RuleLoader
from self_improving_code_review.infrastructure.persistence.feedback_repository import (
    SqliteFeedbackRepository,
    _parse_finding_id,
    _row_to_feedback,
)
from self_improving_code_review.infrastructure.persistence.short_transaction import (
    ShortTransactionExtractionRepository,
    ShortTransactionFeedbackRepository,
    ShortTransactionMemoryRepository,
    ShortTransactionSnapshotRepository,
)
from self_improving_code_review.infrastructure.persistence.sqlite import SqliteUnitOfWorkFactory
from self_improving_code_review.messages import Messages

NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")
FEEDBACK_ID = FeedbackId("22345678-1234-4234-9234-123456789abc")
EXTRACTION_ID = ExtractionId("32345678-1234-4234-9234-123456789abc")
MEMORY_ID = MemoryId("42345678-1234-4234-9234-123456789abc")
PROJECT_ID = ProjectId("project")


def test_short_transaction_feedback_round_trip_and_run_context(tmp_path: Path) -> None:
    factory = _factory_with_run(tmp_path)
    repository = ShortTransactionFeedbackRepository(factory)
    record = _feedback()

    repository.save(record)
    symbol_record = replace(
        record,
        feedback_id=FeedbackId("a2345678-1234-4234-9234-123456789abc"),
        idempotency_key="symbol-feedback-key",
        location=IssueLocation(NormalizedPath("src/app.py"), symbol="load_user"),
    )
    repository.save(symbol_record)

    assert repository.find_by_idempotency_key("feedback-key") == record
    assert repository.find_by_idempotency_key("missing") is None
    assert repository.find_by_ids(()) == ()
    assert repository.find_by_ids((FEEDBACK_ID,)) == (record,)
    assert repository.find_by_idempotency_key("symbol-feedback-key") == symbol_record
    assert not repository.exists_finding(RUN_ID, "B001-F001")
    assert repository.find_finding_context(RUN_ID, "B001-F001") is None
    context = repository.find_run_context(RUN_ID)
    assert context is not None
    assert context.project_id == PROJECT_ID
    assert context.source_revision == "a" * 40
    assert not context.is_test_evaluation
    with closing(sqlite3.connect(tmp_path / "review.db")) as connection:
        connection.execute(
            "INSERT INTO ground_truth_sets VALUES(?,?,?,?,?,?,?)",
            ("test-v1", "test", 1, "hash", "published", "reviewer", NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO evaluation_runs VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "52345678-1234-4234-9234-123456789abc",
                "test-v1",
                "B",
                1,
                "config",
                None,
                None,
                "success",
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO evaluation_review_runs(evaluation_id,run_id) VALUES(?,?)",
            ("52345678-1234-4234-9234-123456789abc", str(RUN_ID)),
        )
        connection.commit()
    test_context = repository.find_run_context(RUN_ID)
    assert test_context is not None
    assert test_context.is_test_evaluation
    assert repository.find_run_context(RunId("92345678-1234-4234-9234-123456789abc")) is None


@pytest.mark.case("IT-M2-006")
def test_short_transaction_extraction_lifecycle(tmp_path: Path) -> None:
    factory = _factory_with_feedback(tmp_path)
    repository = ShortTransactionExtractionRepository(factory)
    pending = _extraction()
    evidence = (ExtractionEvidence(FEEDBACK_ID, "feedback-hash"),)

    repository.create(pending, evidence)
    assert repository.find_by_idempotency_key("extraction-key") == pending
    assert repository.find_by_idempotency_key("missing") is None
    assert repository.acquire(EXTRACTION_ID, 1)
    assert not repository.acquire(EXTRACTION_ID, 2)

    failed = replace(
        pending,
        status=ExtractionStatus.FAILED,
        request_hash="request-hash",
        safe_error_code="LlmClientError",
        input_tokens=10,
        output_tokens=2,
        latency_ms=3,
        cost_usd=Decimal("0.1"),
        finished_at=NOW,
    )
    repository.finalize(failed)
    restored = repository.find_by_idempotency_key("extraction-key")
    assert restored == failed
    assert repository.acquire(EXTRACTION_ID, 2)


@pytest.mark.case("IT-M2-008")
def test_finalize_success_creates_memory_and_success_atomically(tmp_path: Path) -> None:
    factory = _factory_with_feedback(tmp_path)
    extractions = ShortTransactionExtractionRepository(factory)
    memories = ShortTransactionMemoryRepository(factory)
    pending = _extraction()
    extractions.create(pending, (ExtractionEvidence(FEEDBACK_ID, "hash"),))
    extractions.acquire(EXTRACTION_ID, 1)
    record, content, transition = _memory()
    success = replace(
        pending,
        status=ExtractionStatus.SUCCESS,
        candidate_memory_id=MEMORY_ID,
        candidate_version=MemoryVersion(1),
        finished_at=NOW,
    )

    extractions.finalize_success(
        success,
        record,
        content,
        (MemoryEvidenceLink(FEEDBACK_ID, "derived-from"),),
        transition,
    )

    assert extractions.find_by_idempotency_key("extraction-key") == success
    assert memories.find(MEMORY_ID) == (record, content)
    assert memories.list_by_status(PROJECT_ID, MemoryStatus.PENDING) == ((record, content),)


@pytest.mark.case("IT-M2-011")
def test_memory_version_status_lock_and_hash_validation(tmp_path: Path) -> None:
    factory = _factory_with_feedback(tmp_path)
    repository = ShortTransactionMemoryRepository(factory)
    assert repository.find(MEMORY_ID) is None
    record, content, transition = _memory()
    repository.create(
        record,
        content,
        (MemoryEvidenceLink(FEEDBACK_ID, "derived-from"),),
        transition,
    )

    version_two = replace(
        content, version=MemoryVersion(2), scope=MemoryScope(paths=("src/app.py",))
    )
    repository.add_version(MEMORY_ID, version_two, 1)
    with pytest.raises(ConcurrencyConflictError):
        repository.add_version(MEMORY_ID, replace(content, version=MemoryVersion(3)), 1)

    active = MemoryTransition(
        MEMORY_ID,
        MemoryVersion(2),
        MemoryStatus.PENDING,
        MemoryStatus.ACTIVE,
        "reviewer",
        "approved",
        NOW,
    )
    repository.change_status(active, 2)
    with pytest.raises(ConcurrencyConflictError):
        repository.change_status(active, 2)
    restored = repository.find(MEMORY_ID)
    assert restored is not None
    assert restored[0].current_status is MemoryStatus.ACTIVE
    assert restored[0].confirmed_at == NOW

    database = tmp_path / "review.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE memory_versions SET content_hash = 'tampered' "
            "WHERE memory_id = ? AND version = 2",
            (str(MEMORY_ID),),
        )
        connection.commit()
    with pytest.raises(DatabaseError, match=re.escape(Messages.MEMORY_CONTENT_HASH_MISMATCH)):
        repository.find(MEMORY_ID)


@pytest.mark.case("IT-M2-010")
def test_add_version_and_status_is_one_repository_operation(tmp_path: Path) -> None:
    factory = _factory_with_feedback(tmp_path)
    repository = ShortTransactionMemoryRepository(factory)
    record, content, transition = _memory()
    repository.create(record, content, (), transition)
    version_two = replace(
        content, version=MemoryVersion(2), scope=MemoryScope(paths=("src/app.py",))
    )
    active = MemoryTransition(
        MEMORY_ID,
        MemoryVersion(2),
        MemoryStatus.PENDING,
        MemoryStatus.ACTIVE,
        "reviewer",
        "narrowed",
        NOW,
    )

    repository.add_version_and_change_status(MEMORY_ID, version_two, active, 1)

    restored = repository.find(MEMORY_ID)
    assert restored is not None
    assert restored[0].current_version == MemoryVersion(2)
    assert restored[0].current_status is MemoryStatus.ACTIVE


@pytest.mark.case("IT-M2-015")
def test_snapshot_save_is_idempotent_and_links_only_fixed_items(tmp_path: Path) -> None:
    factory = _factory_with_feedback(tmp_path)
    memories = ShortTransactionMemoryRepository(factory)
    snapshots = ShortTransactionSnapshotRepository(factory)
    record, content, transition = _memory()
    memories.create(record, content, (), transition)
    snapshot = MemorySnapshot(
        "snapshot",
        PROJECT_ID,
        "snapshot-hash",
        NOW,
        (MemorySnapshotItem(MEMORY_ID, MemoryVersion(1), content.content_hash),),
    )

    snapshots.save(snapshot)
    snapshots.save(snapshot)
    snapshots.link_findings(snapshot, RUN_ID, ())

    with closing(sqlite3.connect(tmp_path / "review.db")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memory_snapshots").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM memory_snapshot_items").fetchone()[0] == 1


def test_missing_memory_version_is_reported_as_database_error(tmp_path: Path) -> None:
    factory = _factory_with_feedback(tmp_path)
    repository = ShortTransactionMemoryRepository(factory)
    record, content, transition = _memory()
    repository.create(record, content, (), transition)
    with closing(sqlite3.connect(tmp_path / "review.db")) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "UPDATE memories SET current_version = 9 WHERE memory_id = ?", (str(MEMORY_ID),)
        )
        connection.commit()

    with pytest.raises(DatabaseError, match=re.escape(Messages.MEMORY_VERSION_NOT_FOUND)):
        repository.find(MEMORY_ID)


def test_feedback_finding_context_and_identifier_reconstruction() -> None:
    row = (
        "src/app.py",
        10,
        11,
        "correctness",
        "null-dereference",
        "high",
        "Title",
        "Description",
        "value.name",
        "No guard",
    )

    class Cursor:
        def fetchone(self) -> tuple[object, ...]:
            return row

    class Connection:
        def execute(self, *_args: object) -> Cursor:
            return Cursor()

    context = SqliteFeedbackRepository(Connection()).find_finding_context(RUN_ID, "B001-F001")
    finding_id = _parse_finding_id(RUN_ID.value, "B002-F003")

    assert context is not None
    assert "file: src/app.py" in context
    assert "evidence_reason: No guard" in context
    assert finding_id is not None
    assert str(finding_id) == "B002-F003"

    finding_feedback = _row_to_feedback(
        (
            "b2345678-1234-4234-9234-123456789abc",
            RUN_ID.value,
            "B001-F001",
            "false-positive",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "Framework handles it.",
            "reviewer",
            None,
            "finding-feedback-key",
            NOW.isoformat(),
        )
    )
    assert finding_feedback.location is None
    assert finding_feedback.finding_id is not None


def _factory_with_run(tmp_path: Path) -> SqliteUnitOfWorkFactory:
    factory = SqliteUnitOfWorkFactory(tmp_path / "review.db")
    snapshot = RuleLoader().load(tmp_path / "missing", git_sha="a" * 40)
    with factory() as unit:
        unit.rule_snapshots.save(snapshot)
        unit.runs.create(
            RunRecord(
                RUN_ID,
                PROJECT_ID,
                ReviewMode.RULES,
                RunStatus.SUCCESS,
                "a" * 40,
                "dataset/test",
                RunVersions(
                    "0.1.0",
                    "config",
                    "prompt@1",
                    "prompt-hash",
                    snapshot.snapshot_id,
                    snapshot.rule_set_hash,
                    True,
                    "test",
                ),
                "run-key",
                NOW,
                NOW,
            )
        )
    return factory


def _factory_with_feedback(tmp_path: Path) -> SqliteUnitOfWorkFactory:
    factory = _factory_with_run(tmp_path)
    ShortTransactionFeedbackRepository(factory).save(_feedback())
    return factory


def _feedback() -> FeedbackRecord:
    return FeedbackRecord(
        FEEDBACK_ID,
        RUN_ID,
        FeedbackLabel.MISSED_ISSUE,
        "Null check is missing.",
        "reviewer",
        NOW,
        "feedback-key",
        location=IssueLocation(NormalizedPath("src/app.py"), 10),
        issue_type="null-dereference",
        severity=Severity.HIGH,
        description="Null check is missing.",
        evidence="value.name has no guard.",
    )


def _extraction() -> ExtractionRecord:
    return ExtractionRecord(
        EXTRACTION_ID,
        PROJECT_ID,
        "a" * 40,
        "extraction-key",
        ExtractionStatus.PENDING,
        1,
        "memory_extraction@1",
        "prompt-hash",
        "dataset/test",
        NOW,
    )


def _memory() -> tuple[MemoryRecord, MemoryContent, MemoryTransition]:
    content = MemoryContent(
        MEMORY_ID,
        MemoryVersion(1),
        MemoryCategory.FRAMEWORK,
        MemoryScope(paths=("src/**",)),
        "The framework validates inputs.",
        "Require evidence before reporting.",
        MemoryPolarity.REINFORCE,
        0.8,
        "Confirmed by feedback.",
        "extractor",
        NOW,
    )
    record = MemoryRecord(
        MEMORY_ID, PROJECT_ID, MemoryVersion(1), MemoryStatus.PENDING, 1, NOW, NOW
    )
    transition = MemoryTransition(
        MEMORY_ID,
        MemoryVersion(1),
        None,
        MemoryStatus.PENDING,
        "extractor",
        "created",
        NOW,
    )
    return record, content, transition
