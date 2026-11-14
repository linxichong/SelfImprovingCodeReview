"""M5 メモリ生命周期、競合解決、再検証の SQLite 契約テスト."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from self_improving_code_review.domain.common.enums import (
    ConflictResolution,
    ConflictStatus,
    MemoryCategory,
    MemoryPolarity,
    MemoryStatus,
    RevalidationStatus,
)
from self_improving_code_review.domain.common.errors import (
    ConcurrencyConflictError,
    DatabaseError,
    InvalidInputError,
)
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.common.values import (
    MemoryId,
    MemoryVersion,
    ProjectId,
)
from self_improving_code_review.domain.memory.models import (
    MemoryContent,
    MemoryRecord,
    MemoryTransition,
)
from self_improving_code_review.domain.memory.scope import MemoryScope
from self_improving_code_review.infrastructure.persistence.memory_governance_repository import (
    SqliteMemoryGovernanceRepository,
)
from self_improving_code_review.infrastructure.persistence.short_transaction import (
    ShortTransactionMemoryRepository,
)
from self_improving_code_review.infrastructure.persistence.sqlite import SqliteUnitOfWorkFactory

_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
_PROJECT = ProjectId("project")
_LEFT = MemoryId("12345678-1234-4234-9234-123456789abc")
_RIGHT = MemoryId("22345678-1234-4234-9234-123456789abc")
_REASON_HASH = hash_text("reason")
_EVIDENCE_HASH = hash_text("evidence")


def _content(
    memory_id: MemoryId,
    *,
    fact: str,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    review_after: datetime | None = None,
    path: str = "src/**",
) -> tuple[MemoryRecord, MemoryContent, MemoryTransition]:
    """指定状態の検証用メモリ一式を返す."""
    valid_from = _NOW - timedelta(days=30) if review_after is not None else None
    content = MemoryContent(
        memory_id=memory_id,
        version=MemoryVersion(1),
        category=MemoryCategory.FRAMEWORK,
        scope=MemoryScope(paths=(path,)),
        fact=fact,
        review_instruction=f"Review {fact}",
        polarity=MemoryPolarity.REINFORCE,
        confidence=0.8,
        evidence_summary="Confirmed evidence.",
        created_by="extractor",
        created_at=_NOW - timedelta(days=60),
        valid_from=valid_from,
        review_after=review_after,
    )
    record = MemoryRecord(
        memory_id,
        _PROJECT,
        content.version,
        status,
        1,
        content.created_at,
        content.created_at,
    )
    transition = MemoryTransition(
        memory_id,
        content.version,
        None,
        status,
        "extractor",
        "created",
        content.created_at,
    )
    return record, content, transition


def _repositories(
    tmp_path: Path,
) -> tuple[ShortTransactionMemoryRepository, SqliteMemoryGovernanceRepository]:
    """同じ DB を使う通常メモリと M5 統制リポジトリを返す."""
    database = tmp_path / "review.db"
    return (
        ShortTransactionMemoryRepository(SqliteUnitOfWorkFactory(database)),
        SqliteMemoryGovernanceRepository(database),
    )


def _create(
    repository: ShortTransactionMemoryRepository,
    memory_id: MemoryId,
    *,
    fact: str,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    review_after: datetime | None = None,
    path: str = "src/**",
) -> None:
    """検証用メモリを保存する."""
    repository.create(
        *_content(
            memory_id,
            fact=fact,
            status=status,
            review_after=review_after,
            path=path,
        )[:2],
        (),
        _content(
            memory_id,
            fact=fact,
            status=status,
            review_after=review_after,
            path=path,
        )[2],
    )


@pytest.mark.case("UT-M5-002")
def test_archive_restore_and_supersede_preserve_versions(tmp_path: Path) -> None:
    """保管、復元、置換が履歴と楽観ロックを維持する."""
    memories, governance = _repositories(tmp_path)
    _create(memories, _LEFT, fact="left")
    _create(memories, _RIGHT, fact="right")

    archived = governance.archive(
        _PROJECT,
        _LEFT,
        expected_lock_version=1,
        actor_id="tanaka",
        reason_hash=_REASON_HASH,
        occurred_at=_NOW,
    )
    restored = governance.restore(
        _PROJECT,
        _LEFT,
        expected_lock_version=2,
        actor_id="tanaka",
        reason_hash=_REASON_HASH,
        occurred_at=_NOW + timedelta(minutes=1),
    )

    assert archived.status is MemoryStatus.ARCHIVED
    assert restored.status is MemoryStatus.PENDING
    assert restored.version == MemoryVersion(2)
    assert restored.lock_version == 4

    pending = memories.find(_LEFT)
    assert pending is not None
    memories.change_status(
        MemoryTransition(
            _LEFT,
            MemoryVersion(2),
            MemoryStatus.PENDING,
            MemoryStatus.ACTIVE,
            "tanaka",
            "approved",
            _NOW + timedelta(minutes=2),
        ),
        4,
    )
    superseded = governance.supersede(
        _PROJECT,
        _LEFT,
        _RIGHT,
        expected_lock_version=5,
        actor_id="tanaka",
        reason_hash=_REASON_HASH,
        occurred_at=_NOW + timedelta(minutes=3),
    )
    assert superseded.status is MemoryStatus.SUPERSEDED


@pytest.mark.case("UT-M5-003")
def test_detect_and_reject_conflicting_candidate(tmp_path: Path) -> None:
    """競合スナップショットを固定し、候補却下を原子的に保存する."""
    memories, governance = _repositories(tmp_path)
    _create(memories, _LEFT, fact="use strict validation")
    _create(memories, _RIGHT, fact="skip validation", status=MemoryStatus.CONFLICT)

    detected = governance.record_detected_conflicts(
        _PROJECT,
        _RIGHT,
        detected_at=_NOW,
    )
    repeated = governance.record_detected_conflicts(
        _PROJECT,
        _RIGHT,
        detected_at=_NOW + timedelta(minutes=1),
    )

    assert detected == repeated
    assert governance.list_conflicts(_PROJECT, ConflictStatus.OPEN) == detected
    conflict = detected[0]
    resolved, result = governance.resolve_conflict(
        _PROJECT,
        conflict.conflict_id,
        ConflictResolution.REJECT_CANDIDATE,
        expected_lock_version=1,
        actor_id="tanaka",
        reason_hash=_REASON_HASH,
        evidence_hash=_EVIDENCE_HASH,
        occurred_at=_NOW + timedelta(minutes=2),
        narrowed_scope=None,
    )
    assert resolved.status is ConflictStatus.RESOLVED
    assert result.status is MemoryStatus.REJECTED
    assert governance.list_conflicts(_PROJECT, ConflictStatus.OPEN) == ()
    assert governance.list_conflicts(_PROJECT, ConflictStatus.RESOLVED) == (resolved,)


@pytest.mark.case("UT-M5-004")
def test_revalidation_confirm_and_invalidate(tmp_path: Path) -> None:
    """期限到来を固定し、再確認と無効化の両経路を保存する."""
    memories, governance = _repositories(tmp_path)
    due_at = _NOW - timedelta(days=1)
    _create(memories, _LEFT, fact="left", review_after=due_at)
    _create(memories, _RIGHT, fact="right", review_after=due_at)

    due = governance.list_due(_PROJECT, _NOW)
    assert len(due) == 2
    assert all(item.status is RevalidationStatus.DUE for item in due)
    assert governance.list_due(_PROJECT, _NOW) == due

    confirmed = governance.confirm_revalidation(
        _PROJECT,
        _LEFT,
        expected_lock_version=1,
        actor_id="tanaka",
        reason_hash=_REASON_HASH,
        evidence_hash=_EVIDENCE_HASH,
        occurred_at=_NOW,
        next_review_after=_NOW + timedelta(days=180),
    )
    invalidated = governance.invalidate_revalidation(
        _PROJECT,
        _RIGHT,
        expected_lock_version=1,
        actor_id="tanaka",
        reason_hash=_REASON_HASH,
        evidence_hash=_EVIDENCE_HASH,
        occurred_at=_NOW,
    )
    assert confirmed.version == MemoryVersion(2)
    assert confirmed.status is MemoryStatus.ACTIVE
    assert invalidated.status is MemoryStatus.ARCHIVED


@pytest.mark.case("UT-M5-002")
def test_memory_governance_rejects_invalid_state_and_stale_lock(tmp_path: Path) -> None:
    """不正状態と古い楽観ロック版を拒否する."""
    memories, governance = _repositories(tmp_path)
    _create(memories, _LEFT, fact="left")

    with pytest.raises(ConcurrencyConflictError):
        governance.archive(
            _PROJECT,
            _LEFT,
            expected_lock_version=9,
            actor_id="tanaka",
            reason_hash=_REASON_HASH,
            occurred_at=_NOW,
        )
    with pytest.raises(InvalidInputError):
        governance.record_detected_conflicts(_PROJECT, _LEFT, detected_at=_NOW)
    with pytest.raises(InvalidInputError):
        governance.confirm_revalidation(
            _PROJECT,
            _LEFT,
            expected_lock_version=1,
            actor_id="tanaka",
            reason_hash=_REASON_HASH,
            evidence_hash=_EVIDENCE_HASH,
            occurred_at=_NOW,
            next_review_after=_NOW,
        )

    with pytest.raises(ConcurrencyConflictError):
        governance.confirm_revalidation(
            _PROJECT,
            _LEFT,
            expected_lock_version=9,
            actor_id="tanaka",
            reason_hash=_REASON_HASH,
            evidence_hash=_EVIDENCE_HASH,
            occurred_at=_NOW,
            next_review_after=_NOW + timedelta(days=1),
        )
    with pytest.raises(ConcurrencyConflictError):
        governance.invalidate_revalidation(
            _PROJECT,
            _LEFT,
            expected_lock_version=9,
            actor_id="tanaka",
            reason_hash=_REASON_HASH,
            evidence_hash=_EVIDENCE_HASH,
            occurred_at=_NOW,
        )
    left_record, left_content, _transition = _content(_LEFT, fact="left")
    right_record, right_content, _transition = _content(_RIGHT, fact="right")
    with pytest.raises(InvalidInputError):
        governance._candidate_pair(
            left_record,
            left_content,
            right_record,
            right_content,
        )


def _detect(
    memories: ShortTransactionMemoryRepository,
    governance: SqliteMemoryGovernanceRepository,
    *,
    left_status: MemoryStatus = MemoryStatus.ACTIVE,
    right_status: MemoryStatus = MemoryStatus.CONFLICT,
) -> str:
    """左右メモリを作成して競合識別子を返す."""
    _create(memories, _LEFT, fact="left", status=left_status)
    _create(memories, _RIGHT, fact="right", status=right_status)
    candidate = _RIGHT if right_status is MemoryStatus.CONFLICT else _LEFT
    return governance.record_detected_conflicts(_PROJECT, candidate, detected_at=_NOW)[
        0
    ].conflict_id


@pytest.mark.case("UT-M5-003")
@pytest.mark.parametrize(
    ("resolution", "left_status", "right_status", "winner"),
    [
        (
            ConflictResolution.SUPERSEDE_LEFT,
            MemoryStatus.ACTIVE,
            MemoryStatus.CONFLICT,
            _RIGHT,
        ),
        (
            ConflictResolution.SUPERSEDE_RIGHT,
            MemoryStatus.CONFLICT,
            MemoryStatus.ACTIVE,
            _LEFT,
        ),
    ],
)
def test_conflict_supersede_activates_winner_atomically(
    resolution: ConflictResolution,
    left_status: MemoryStatus,
    right_status: MemoryStatus,
    winner: MemoryId,
    tmp_path: Path,
) -> None:
    """選択側を置換済みにし、反対側の候補を同時に有効化する."""
    memories, governance = _repositories(tmp_path)
    conflict_id = _detect(
        memories,
        governance,
        left_status=left_status,
        right_status=right_status,
    )

    _resolved, result = governance.resolve_conflict(
        _PROJECT,
        conflict_id,
        resolution,
        expected_lock_version=1,
        actor_id="tanaka",
        reason_hash=_REASON_HASH,
        evidence_hash=_EVIDENCE_HASH,
        occurred_at=_NOW,
        narrowed_scope=None,
    )

    assert result.memory_id == winner
    assert result.status is MemoryStatus.ACTIVE
    found = memories.find(winner)
    assert found is not None
    assert found[0].current_status is MemoryStatus.ACTIVE


@pytest.mark.case("UT-M5-003")
def test_narrow_scope_resolves_or_reopens_conflict(tmp_path: Path) -> None:
    """範囲縮小後に残存競合を再検査し、新版へ状態を反映する."""
    memories, governance = _repositories(tmp_path)
    _create(memories, _LEFT, fact="left", path="src/app.py")
    _create(memories, _RIGHT, fact="right", status=MemoryStatus.CONFLICT, path="src/app.py")
    conflict_id = governance.record_detected_conflicts(_PROJECT, _RIGHT, detected_at=_NOW)[
        0
    ].conflict_id

    _resolved, result = governance.resolve_conflict(
        _PROJECT,
        conflict_id,
        ConflictResolution.NARROW_SCOPE,
        expected_lock_version=1,
        actor_id="tanaka",
        reason_hash=_REASON_HASH,
        evidence_hash=_EVIDENCE_HASH,
        occurred_at=_NOW,
        narrowed_scope=MemoryScope(paths=("tests/test_app.py",)),
    )
    assert result.status is MemoryStatus.ACTIVE
    assert result.version == MemoryVersion(2)

    other_path = tmp_path / "remaining"
    other_path.mkdir()
    remaining_memories, remaining_governance = _repositories(other_path)
    remaining_id = _detect(remaining_memories, remaining_governance)
    _resolved, remaining_result = remaining_governance.resolve_conflict(
        _PROJECT,
        remaining_id,
        ConflictResolution.NARROW_SCOPE,
        expected_lock_version=1,
        actor_id="tanaka",
        reason_hash=_REASON_HASH,
        evidence_hash=_EVIDENCE_HASH,
        occurred_at=_NOW,
        narrowed_scope=MemoryScope(paths=("src/**",)),
    )
    assert remaining_result.status is MemoryStatus.CONFLICT
    assert len(remaining_governance.list_conflicts(_PROJECT, ConflictStatus.OPEN)) == 1


@pytest.mark.case("UT-M5-003")
def test_conflict_resolution_rejects_invalid_choices(tmp_path: Path) -> None:
    """不足入力、古いロック、状態と左右選択の矛盾を拒否する."""
    memories, governance = _repositories(tmp_path)
    conflict_id = _detect(memories, governance)

    common = {
        "expected_lock_version": 1,
        "actor_id": "tanaka",
        "reason_hash": _REASON_HASH,
        "evidence_hash": _EVIDENCE_HASH,
        "occurred_at": _NOW,
    }
    with pytest.raises(InvalidInputError):
        governance.resolve_conflict(
            _PROJECT,
            conflict_id,
            ConflictResolution.NARROW_SCOPE,
            narrowed_scope=None,
            **common,
        )
    with pytest.raises(InvalidInputError):
        governance.resolve_conflict(
            _PROJECT,
            conflict_id,
            ConflictResolution.REJECT_CANDIDATE,
            narrowed_scope=MemoryScope(paths=("src/**",)),
            **common,
        )
    with pytest.raises(ConcurrencyConflictError):
        governance.resolve_conflict(
            _PROJECT,
            conflict_id,
            ConflictResolution.REJECT_CANDIDATE,
            narrowed_scope=None,
            **{**common, "expected_lock_version": 9},
        )
    with pytest.raises(InvalidInputError):
        governance.resolve_conflict(
            _PROJECT,
            "0" * 64,
            ConflictResolution.REJECT_CANDIDATE,
            narrowed_scope=None,
            **common,
        )
    with pytest.raises(InvalidInputError):
        governance.resolve_conflict(
            _PROJECT,
            conflict_id,
            ConflictResolution.SUPERSEDE_RIGHT,
            narrowed_scope=None,
            **common,
        )


@pytest.mark.case("UT-M5-002")
def test_repository_rejects_invalid_targets_and_states(tmp_path: Path) -> None:
    """同一置換先、別プロジェクト、非有効置換先を拒否する."""
    memories, governance = _repositories(tmp_path)
    _create(memories, _LEFT, fact="left")
    _create(memories, _RIGHT, fact="right", status=MemoryStatus.PENDING)

    with pytest.raises(InvalidInputError):
        governance.supersede(
            _PROJECT,
            _LEFT,
            _LEFT,
            expected_lock_version=1,
            actor_id="tanaka",
            reason_hash=_REASON_HASH,
            occurred_at=_NOW,
        )
    with pytest.raises(InvalidInputError):
        governance.supersede(
            _PROJECT,
            _LEFT,
            _RIGHT,
            expected_lock_version=1,
            actor_id="tanaka",
            reason_hash=_REASON_HASH,
            occurred_at=_NOW,
        )
    with pytest.raises(InvalidInputError):
        governance.archive(
            ProjectId("other"),
            _LEFT,
            expected_lock_version=1,
            actor_id="tanaka",
            reason_hash=_REASON_HASH,
            occurred_at=_NOW,
        )
    with pytest.raises(InvalidInputError):
        governance.restore(
            _PROJECT,
            _RIGHT,
            expected_lock_version=1,
            actor_id="tanaka",
            reason_hash=_REASON_HASH,
            occurred_at=_NOW,
        )


def test_sqlite_failures_are_mapped_to_database_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """各公開操作の SQLite 例外をプロジェクト共通例外へ変換する."""
    _memories, governance = _repositories(tmp_path)

    def fail_connection() -> sqlite3.Connection:
        """模擬 SQLite 障害を送出する."""
        raise sqlite3.OperationalError("simulated")

    monkeypatch.setattr(governance, "_connection", fail_connection)
    operations = (
        lambda: governance.record_detected_conflicts(_PROJECT, _LEFT, detected_at=_NOW),
        lambda: governance.restore(
            _PROJECT,
            _LEFT,
            expected_lock_version=1,
            actor_id="tanaka",
            reason_hash=_REASON_HASH,
            occurred_at=_NOW,
        ),
        lambda: governance.supersede(
            _PROJECT,
            _LEFT,
            _RIGHT,
            expected_lock_version=1,
            actor_id="tanaka",
            reason_hash=_REASON_HASH,
            occurred_at=_NOW,
        ),
        lambda: governance.list_conflicts(_PROJECT, ConflictStatus.OPEN),
        lambda: governance.resolve_conflict(
            _PROJECT,
            _REASON_HASH,
            ConflictResolution.REJECT_CANDIDATE,
            expected_lock_version=1,
            actor_id="tanaka",
            reason_hash=_REASON_HASH,
            evidence_hash=_EVIDENCE_HASH,
            occurred_at=_NOW,
            narrowed_scope=None,
        ),
        lambda: governance.list_due(_PROJECT, _NOW),
        lambda: governance.confirm_revalidation(
            _PROJECT,
            _LEFT,
            expected_lock_version=1,
            actor_id="tanaka",
            reason_hash=_REASON_HASH,
            evidence_hash=_EVIDENCE_HASH,
            occurred_at=_NOW,
            next_review_after=_NOW + timedelta(days=1),
        ),
        lambda: governance.invalidate_revalidation(
            _PROJECT,
            _LEFT,
            expected_lock_version=1,
            actor_id="tanaka",
            reason_hash=_REASON_HASH,
            evidence_hash=_EVIDENCE_HASH,
            occurred_at=_NOW,
        ),
        lambda: governance.archive(
            _PROJECT,
            _LEFT,
            expected_lock_version=1,
            actor_id="tanaka",
            reason_hash=_REASON_HASH,
            occurred_at=_NOW,
        ),
    )
    for operation in operations:
        with pytest.raises(DatabaseError):
            operation()
