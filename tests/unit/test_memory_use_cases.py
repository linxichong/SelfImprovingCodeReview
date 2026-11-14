"""UT-M2-010〜UT-M2-013: メモリ統制、検索、固定化を検証する。"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from self_improving_code_review.application.memory.change_status import ApproveMemory, RejectMemory
from self_improving_code_review.application.memory.retrieve_memories import (
    MemoryRetriever,
    MemorySnapshotService,
)
from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.common.enums import (
    MemoryCategory,
    MemoryPolarity,
    MemoryStatus,
    Permission,
    RiskLevel,
)
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import MemoryId, MemoryVersion, ProjectId
from self_improving_code_review.domain.memory.models import (
    MemoryContent,
    MemoryRecord,
    MemorySnapshot,
    MemoryTransition,
)
from self_improving_code_review.domain.memory.scope import MemoryScope
from self_improving_code_review.messages import Messages

NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
PROJECT_ID = ProjectId("project")
MEMORY_ID = MemoryId("12345678-1234-4234-9234-123456789abc")
_UNEXPECTED_CREATE = "not used"


class FixedClock:
    """一定の UTC 時刻を返す。"""

    def now_utc(self) -> datetime:
        return NOW


class AllowAuthorizer:
    """権限要求を記録して許可する。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Permission]] = []

    def require(self, actor_id: str, permission: Permission) -> None:
        self.calls.append((actor_id, permission))


class FakeMemoryRepository:
    """統制操作を観測できるメモリリポジトリ。"""

    def __init__(
        self,
        found: tuple[MemoryRecord, MemoryContent] | None,
        active: tuple[tuple[MemoryRecord, MemoryContent], ...] = (),
    ) -> None:
        self.found = found
        self.active = active
        self.transitions: list[tuple[MemoryTransition, int]] = []
        self.versioned: list[tuple[MemoryId, MemoryContent, MemoryTransition, int]] = []

    def find(self, memory_id: MemoryId) -> tuple[MemoryRecord, MemoryContent] | None:
        return (
            self.found if self.found is not None and self.found[0].memory_id == memory_id else None
        )

    def list_by_status(
        self, project_id: ProjectId, status: MemoryStatus
    ) -> tuple[tuple[MemoryRecord, MemoryContent], ...]:
        assert project_id == PROJECT_ID
        assert status is MemoryStatus.ACTIVE
        return self.active

    def change_status(self, transition: MemoryTransition, expected_lock_version: int) -> None:
        self.transitions.append((transition, expected_lock_version))

    def add_version_and_change_status(
        self,
        memory_id: MemoryId,
        content: MemoryContent,
        transition: MemoryTransition,
        expected_lock_version: int,
    ) -> None:
        self.versioned.append((memory_id, content, transition, expected_lock_version))

    def create_candidate(self, *_args: object) -> None:
        raise AssertionError(_UNEXPECTED_CREATE)


class FakeSnapshotRepository:
    """保存されたスナップショットを保持する。"""

    def __init__(self) -> None:
        self.saved: list[MemorySnapshot] = []

    def save(self, snapshot: MemorySnapshot) -> None:
        self.saved.append(snapshot)


@pytest.mark.case("UT-M2-010")
def test_approve_memory_activates_candidate_without_new_version() -> None:
    authorizer = AllowAuthorizer()
    repository = FakeMemoryRepository(_pair())

    outcome = ApproveMemory(
        memory_repository=repository, authorizer=authorizer, clock=FixedClock()
    ).execute(MEMORY_ID, actor_id=" reviewer ", reason=" valid ", expected_lock_version=1)

    assert outcome.status is MemoryStatus.ACTIVE
    assert outcome.version == MemoryVersion(1)
    assert authorizer.calls == [(" reviewer ", Permission.APPROVE_MEMORY)]
    transition, lock = repository.transitions[0]
    assert transition.actor_id == "reviewer"
    assert transition.reason == "valid"
    assert lock == 1


@pytest.mark.case("IT-M2-010", "UT-M2-011")
def test_approve_with_changed_scope_creates_an_immutable_new_version() -> None:
    repository = FakeMemoryRepository(_pair())

    outcome = ApproveMemory(
        memory_repository=repository, authorizer=AllowAuthorizer(), clock=FixedClock()
    ).execute(
        MEMORY_ID,
        actor_id="reviewer",
        reason="narrowed",
        expected_lock_version=1,
        narrowed_scope=MemoryScope(paths=("src/app.py",)),
    )

    assert outcome.version == MemoryVersion(2)
    assert repository.transitions == []
    assert repository.versioned[0][1].scope.paths == ("src/app.py",)
    assert repository.versioned[0][1].created_by == "reviewer"


@pytest.mark.case("UT-M2-010")
def test_approve_marks_conflict_without_changing_existing_memory() -> None:
    existing = _pair(
        memory_id="22345678-1234-4234-9234-123456789abc",
        status=MemoryStatus.ACTIVE,
        polarity=MemoryPolarity.SUPPRESS,
    )
    repository = FakeMemoryRepository(_pair(), (existing,))

    outcome = ApproveMemory(
        memory_repository=repository, authorizer=AllowAuthorizer(), clock=FixedClock()
    ).execute(MEMORY_ID, actor_id="reviewer", reason="reviewed", expected_lock_version=1)

    assert outcome.status is MemoryStatus.CONFLICT
    assert outcome.conflict_reason_codes == ("OPPOSITE_POLARITY",)
    assert repository.transitions[0][0].to_status is MemoryStatus.CONFLICT


def test_approve_rejects_missing_memory_and_empty_reason() -> None:
    use_case = ApproveMemory(
        memory_repository=FakeMemoryRepository(None),
        authorizer=AllowAuthorizer(),
        clock=FixedClock(),
    )
    with pytest.raises(InvalidInputError, match=re.escape(Messages.memory_not_found(""))):
        use_case.execute(MEMORY_ID, actor_id="reviewer", reason="valid", expected_lock_version=1)
    with pytest.raises(InvalidInputError):
        use_case.execute(MEMORY_ID, actor_id="reviewer", reason=" ", expected_lock_version=1)


@pytest.mark.case("UT-M2-010")
def test_reject_memory_records_transition() -> None:
    repository = FakeMemoryRepository(_pair())

    outcome = RejectMemory(
        memory_repository=repository, authorizer=AllowAuthorizer(), clock=FixedClock()
    ).execute(MEMORY_ID, actor_id=" reviewer ", reason=" invalid ", expected_lock_version=1)

    assert outcome.status is MemoryStatus.REJECTED
    assert repository.transitions[0][0].to_status is MemoryStatus.REJECTED
    assert repository.transitions[0][0].actor_id == "reviewer"


@pytest.mark.case("IT-M2-013")
def test_retriever_selects_only_matching_active_memory_with_budget() -> None:
    matching = _pair(status=MemoryStatus.ACTIVE)
    not_matching = _pair(
        memory_id="22345678-1234-4234-9234-123456789abc",
        status=MemoryStatus.ACTIVE,
        scope=MemoryScope(paths=("tests/**",)),
    )
    repository = FakeMemoryRepository(None, (matching, not_matching))
    files = (
        UnifiedDiffParser()
        .parse(
            "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"
            "@@ -1 +1,2 @@\n value = 1\n+value = 2\n"
        )
        .files
    )

    result = MemoryRetriever(repository).retrieve(
        PROJECT_ID, files, risk=RiskLevel.LOW, perspective="correctness"
    )

    assert result.contents == (matching[1],)
    assert result.total_tokens > 0
    assert result.truncated_count == 0


@pytest.mark.case("UT-M5-004")
def test_retriever_excludes_expired_and_unknown_framework_versions() -> None:
    """M5 有効時は期限切れと実行版不明の制約付きメモリを除外する."""
    valid = _pair(status=MemoryStatus.ACTIVE)
    expired_pair = _pair(
        memory_id="22345678-1234-4234-9234-123456789abc",
        status=MemoryStatus.ACTIVE,
    )
    constrained_pair = _pair(
        memory_id="32345678-1234-4234-9234-123456789abc",
        status=MemoryStatus.ACTIVE,
    )
    expired = (
        expired_pair[0],
        replace(
            expired_pair[1],
            valid_from=NOW - timedelta(days=2),
            review_after=NOW,
        ),
    )
    constrained = (
        constrained_pair[0],
        replace(constrained_pair[1], framework_version_constraints=("django>=5",)),
    )
    files = (
        UnifiedDiffParser()
        .parse(
            "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"
            "@@ -1 +1,2 @@\n value = 1\n+value = 2\n"
        )
        .files
    )

    result = MemoryRetriever(
        FakeMemoryRepository(None, (valid, expired, constrained)),
        clock=FixedClock(),
    ).retrieve(PROJECT_ID, files, risk=RiskLevel.LOW, perspective="correctness")

    assert result.contents == (valid[1],)


@pytest.mark.case("UT-M2-015")
def test_snapshot_service_saves_fixed_content_references() -> None:
    repository = FakeSnapshotRepository()
    service = MemorySnapshotService(repository, clock=FixedClock())

    snapshot = service.create(PROJECT_ID, (_pair()[1],))

    assert repository.saved == [snapshot]
    assert snapshot.snapshot_id == snapshot.snapshot_hash[:32]
    assert snapshot.items[0].content_hash == _pair()[1].content_hash


def _pair(
    *,
    memory_id: str = "12345678-1234-4234-9234-123456789abc",
    status: MemoryStatus = MemoryStatus.PENDING,
    polarity: MemoryPolarity = MemoryPolarity.REINFORCE,
    scope: MemoryScope | None = None,
) -> tuple[MemoryRecord, MemoryContent]:
    identifier = MemoryId(memory_id)
    content = MemoryContent(
        identifier,
        MemoryVersion(1),
        MemoryCategory.FRAMEWORK,
        scope or MemoryScope(paths=("src/**",)),
        "The framework validates inputs.",
        "Require evidence before reporting.",
        polarity,
        0.8,
        "Confirmed by feedback.",
        "extractor",
        NOW,
    )
    return (
        MemoryRecord(identifier, PROJECT_ID, MemoryVersion(1), status, 1, NOW, NOW, NOW),
        content,
    )
