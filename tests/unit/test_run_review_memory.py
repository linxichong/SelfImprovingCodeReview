"""M2 メモリ注入に関するレビュー実行の分岐を検証する。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from self_improving_code_review.application.memory.retrieve_memories import RetrievedMemories
from self_improving_code_review.application.review.run_review import RunReviewUseCase
from self_improving_code_review.domain.common.enums import (
    MemoryCategory,
    MemoryPolarity,
    MemoryStatus,
    ReviewMode,
    RiskLevel,
)
from self_improving_code_review.domain.common.values import (
    MemoryId,
    MemoryVersion,
    ProjectId,
    RunId,
)
from self_improving_code_review.domain.memory.models import MemoryContent, MemoryRecord
from self_improving_code_review.domain.memory.scope import MemoryScope

NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
MEMORY_ID = MemoryId("12345678-1234-4234-9234-123456789abc")
RUN_ID = RunId("22345678-1234-4234-9234-123456789abc")
PROJECT_ID = ProjectId("project")


class Retriever:
    """検索呼び出しを記録して固定結果を返す。"""

    def __init__(self, content: MemoryContent) -> None:
        self.content = content
        self.calls: list[tuple[object, ...]] = []

    def retrieve(self, *args: object, **kwargs: object) -> RetrievedMemories:
        self.calls.append((*args, kwargs))
        return RetrievedMemories((self.content,), 0, 10)


class SnapshotService:
    """固定対象を記録して簡易スナップショットを返す。"""

    def __init__(self) -> None:
        self.calls: list[tuple[ProjectId, tuple[MemoryContent, ...]]] = []

    def create(self, project_id: ProjectId, contents: tuple[MemoryContent, ...]) -> object:
        self.calls.append((project_id, contents))
        return SimpleNamespace(snapshot_id="snapshot")


class UnitOfWork:
    """有効メモリ取得と指摘リンクを模擬する。"""

    def __init__(self, active: tuple[tuple[MemoryRecord, MemoryContent], ...]) -> None:
        self.memories = SimpleNamespace(list_by_status=lambda _project, _status: active)
        self.links: list[tuple[object, ...]] = []
        self.memory_snapshots = SimpleNamespace(link_findings=lambda *args: self.links.append(args))

    def __enter__(self) -> UnitOfWork:
        return self

    def __exit__(self, *args: object) -> None:
        pass


@pytest.mark.case("IT-M1-004", "IT-M3-009", "UT-M2-016")
def test_memory_retrieval_is_disabled_unless_all_conditions_are_met() -> None:
    use_case = object.__new__(RunReviewUseCase)
    use_case._memory_retriever = None
    use_case._config = SimpleNamespace(memory=SimpleNamespace(is_injection_enabled=True))
    request = SimpleNamespace(project_id=PROJECT_ID, mode=ReviewMode.MEMORY)
    bundle = SimpleNamespace(files=(), risk=RiskLevel.LOW)
    plan = SimpleNamespace(perspective="correctness")

    empty = use_case._retrieve_memories(request, bundle, plan)
    assert empty.contents == ()

    retriever = Retriever(_content())
    use_case._memory_retriever = retriever
    use_case._config = SimpleNamespace(memory=SimpleNamespace(is_injection_enabled=False))
    assert use_case._retrieve_memories(request, bundle, plan).contents == ()
    use_case._config = SimpleNamespace(memory=SimpleNamespace(is_injection_enabled=True))
    request.mode = ReviewMode.RULES
    assert use_case._retrieve_memories(request, bundle, plan).contents == ()

    request.mode = ReviewMode.MEMORY
    result = use_case._retrieve_memories(request, bundle, plan)
    assert result.contents == (_content(),)
    assert retriever.calls


@pytest.mark.case("IT-M2-015", "IT-M3-009", "UT-M2-016")
def test_snapshot_uses_exact_rendered_contents_and_links_findings() -> None:
    content = _content()
    active = ((_record(), content),)
    unit = UnitOfWork(active)
    service = SnapshotService()
    use_case = object.__new__(RunReviewUseCase)
    use_case._memory_snapshot_service = service
    use_case._unit_of_work_factory = lambda: unit
    request = SimpleNamespace(project_id=PROJECT_ID)
    links = [("B001-F001", MEMORY_ID, MemoryVersion(1))]

    snapshot = use_case._persist_memory_snapshot(
        request,
        (content,),
        links,
        RUN_ID,
        has_memory_context=True,
    )

    assert snapshot is not None
    assert service.calls == [(PROJECT_ID, (content,))]
    assert unit.links[0][1] == RUN_ID


@pytest.mark.case("UT-M2-016")
def test_snapshot_is_created_for_empty_rendered_set_without_findings() -> None:
    use_case = object.__new__(RunReviewUseCase)
    use_case._memory_snapshot_service = None
    request = SimpleNamespace(project_id=PROJECT_ID)
    links = [("B001-F001", MEMORY_ID, MemoryVersion(1))]
    assert (
        use_case._persist_memory_snapshot(
            request,
            (_content(),),
            links,
            RUN_ID,
            has_memory_context=True,
        )
        is None
    )

    service = SnapshotService()
    use_case._memory_snapshot_service = service
    use_case._unit_of_work_factory = lambda: UnitOfWork(())
    assert (
        use_case._persist_memory_snapshot(
            request,
            (),
            [],
            RUN_ID,
            has_memory_context=True,
        )
        is not None
    )
    assert service.calls == [(PROJECT_ID, ())]
    assert (
        use_case._persist_memory_snapshot(
            request,
            (_content(),),
            links,
            RUN_ID,
            has_memory_context=False,
        )
        is None
    )


@pytest.mark.case("IT-M1-004", "UT-M2-016")
def test_active_memory_state_participates_only_in_memory_mode() -> None:
    content = _content()
    use_case = object.__new__(RunReviewUseCase)
    use_case._memory_retriever = Retriever(content)
    use_case._config = SimpleNamespace(memory=SimpleNamespace(is_injection_enabled=True))
    use_case._unit_of_work_factory = lambda: UnitOfWork(((_record(), content),))
    request = SimpleNamespace(project_id=PROJECT_ID, mode=ReviewMode.MEMORY)

    active_hash = use_case._active_memory_state_hash(request)
    request.mode = ReviewMode.RULES
    disabled_hash = use_case._active_memory_state_hash(request)

    assert active_hash != disabled_hash


def _content() -> MemoryContent:
    return MemoryContent(
        MEMORY_ID,
        MemoryVersion(1),
        MemoryCategory.FRAMEWORK,
        MemoryScope(paths=("src/**",)),
        "Fact",
        "Instruction",
        MemoryPolarity.REINFORCE,
        0.8,
        "Evidence",
        "actor",
        NOW,
    )


def _record() -> MemoryRecord:
    return MemoryRecord(
        MEMORY_ID,
        PROJECT_ID,
        MemoryVersion(1),
        MemoryStatus.ACTIVE,
        1,
        NOW,
        NOW,
    )
