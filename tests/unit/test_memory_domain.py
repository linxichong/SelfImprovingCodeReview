"""UT-M2-009〜UT-M2-013: メモリ領域の方針と検索を検証する。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.common.enums import (
    MemoryCategory,
    MemoryPolarity,
    MemoryStatus,
    RiskLevel,
    Severity,
)
from self_improving_code_review.domain.common.errors import (
    ConcurrencyConflictError,
    InvalidInputError,
    InvalidStateTransitionError,
)
from self_improving_code_review.domain.common.values import (
    MemoryId,
    MemoryVersion,
    NormalizedPath,
    ProjectId,
)
from self_improving_code_review.domain.memory.conflicts import (
    find_conflicts,
    is_forbidden_suppression,
    normalize_instruction_text,
)
from self_improving_code_review.domain.memory.models import (
    MemoryContent,
    MemoryRecord,
    MemorySnapshot,
    MemorySnapshotItem,
    MemoryTransition,
    compute_memory_snapshot_hash,
)
from self_improving_code_review.domain.memory.retrieval import (
    ScoredMemory,
    estimate_memory_tokens,
    render_memory_block,
    score_memory,
    select_memories,
)
from self_improving_code_review.domain.memory.scope import MemoryScope
from self_improving_code_review.domain.memory.transitions import (
    require_allowed_transition,
    require_lock_version,
)

NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
MEMORY_ID = MemoryId("12345678-1234-4234-9234-123456789abc")


@pytest.mark.case("UT-M2-013")
def test_scope_validation_and_matching() -> None:
    scope = MemoryScope(paths=("src/**/*.py",), languages=("python",))

    assert scope.matches_path(NormalizedPath("src/app/main.py"))
    assert not scope.matches_path(NormalizedPath("tests/test_app.py"))
    assert not scope.is_unbounded_path_scope
    assert MemoryScope(paths=("**/*",)).is_unbounded_path_scope
    assert MemoryScope(languages=("python",)).matches_path(NormalizedPath("anything.txt"))


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"languages": ("",)},
        {"languages": tuple(str(item) for item in range(21))},
    ],
)
def test_scope_rejects_invalid_groups(kwargs: dict[str, tuple[str, ...]]) -> None:
    with pytest.raises(InvalidInputError):
        MemoryScope(**kwargs)


@pytest.mark.case("UT-M2-012")
def test_scope_intersection_is_conservative_for_globs() -> None:
    assert MemoryScope(paths=("src/**/*.py",)).intersects(MemoryScope(paths=("src/app.py",)))
    assert not MemoryScope(languages=("python",)).intersects(MemoryScope(languages=("java",)))
    assert MemoryScope(keywords=("auth",)).intersects(MemoryScope(paths=("src/**",)))


@pytest.mark.case("UT-M2-009")
def test_memory_content_hash_is_stable_and_documents_all_fields() -> None:
    first = _content()
    second = _content()

    assert first.content_hash == second.content_hash
    assert first.to_document()["category"] == "framework"
    assert first.to_document()["scope"] == {
        "languages": ["python"],
        "frameworks": ["fastapi"],
        "paths": ["src/**"],
        "symbols": ["load_user"],
        "keywords": ["Depends"],
        "risk_tags": ["security"],
    }
    from self_improving_code_review.application.review import context_builder

    assert "memory_id:" in context_builder._render_memories((first,))


@pytest.mark.case("UT-M2-009")
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fact", ""),
        ("review_instruction", "x" * 501),
        ("evidence_summary", " "),
        ("created_by", ""),
        ("confidence", 1.1),
        ("created_at", datetime(2026, 1, 2)),  # noqa: DTZ001 - naive time rejection
    ],
)
def test_memory_content_rejects_invalid_values(field: str, value: object) -> None:
    values = _content_values()
    values[field] = value
    with pytest.raises(InvalidInputError):
        MemoryContent(**values)  # type: ignore[arg-type]


@pytest.mark.case("UT-M2-009")
def test_memory_record_transition_and_snapshot_models() -> None:
    record = MemoryRecord(
        memory_id=MEMORY_ID,
        project_id=ProjectId("project"),
        current_version=MemoryVersion(1),
        current_status=MemoryStatus.ACTIVE,
        lock_version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    item = MemorySnapshotItem(MEMORY_ID, MemoryVersion(1), _content().content_hash)
    snapshot = MemorySnapshot("snapshot", ProjectId("project"), "hash", NOW, (item,))

    assert record.is_retrievable
    assert not snapshot.is_empty
    assert MemorySnapshot("empty", ProjectId("project"), "hash", NOW, ()).is_empty
    assert compute_memory_snapshot_hash((item,)) == compute_memory_snapshot_hash((item,))

    with pytest.raises(InvalidInputError):
        MemoryRecord(
            MEMORY_ID, ProjectId("project"), MemoryVersion(1), MemoryStatus.PENDING, 0, NOW, NOW
        )
    with pytest.raises(InvalidInputError):
        MemoryRecord(
            MEMORY_ID,
            ProjectId("project"),
            MemoryVersion(1),
            MemoryStatus.PENDING,
            1,
            datetime(2026, 1, 2),  # noqa: DTZ001 - naive time rejection
            NOW,
        )


@pytest.mark.case("UT-M2-010")
@pytest.mark.parametrize(("actor", "reason"), [("", "reason"), ("actor", "")])
def test_transition_requires_audit_fields(actor: str, reason: str) -> None:
    with pytest.raises(InvalidInputError):
        MemoryTransition(
            MEMORY_ID, MemoryVersion(1), None, MemoryStatus.PENDING, actor, reason, NOW
        )


@pytest.mark.case("UT-M2-009", "UT-M2-010")
def test_transition_requires_utc_and_allowed_state_and_lock() -> None:
    with pytest.raises(InvalidInputError):
        MemoryTransition(
            MEMORY_ID,
            MemoryVersion(1),
            None,
            MemoryStatus.PENDING,
            "actor",
            "reason",
            datetime(2026, 1, 2),  # noqa: DTZ001 - naive time rejection
        )
    require_allowed_transition(MemoryStatus.PENDING, MemoryStatus.ACTIVE)
    require_lock_version(2, 2)
    with pytest.raises(InvalidStateTransitionError):
        require_allowed_transition(MemoryStatus.REJECTED, MemoryStatus.ACTIVE)
    with pytest.raises(ConcurrencyConflictError):
        require_lock_version(1, 2)


@pytest.mark.case("UT-M2-012")
def test_conflicts_classify_opposite_fact_and_instruction() -> None:
    candidate = _content()
    opposite = _content(
        memory_id="22345678-1234-4234-9234-123456789abc", polarity=MemoryPolarity.SUPPRESS
    )
    fact = _content(memory_id="32345678-1234-4234-9234-123456789abc", fact="Different fact")
    instruction = _content(
        memory_id="42345678-1234-4234-9234-123456789abc",
        review_instruction="Different instruction",
    )
    unrelated = _content(
        memory_id="52345678-1234-4234-9234-123456789abc",
        category=MemoryCategory.TESTING,
    )

    conflicts = find_conflicts(candidate, (candidate, opposite, fact, instruction, unrelated))

    assert [item.reason_code for item in conflicts] == [
        "OPPOSITE_POLARITY",
        "DIFFERENT_FACT",
        "DIFFERENT_INSTRUCTION",
    ]
    assert normalize_instruction_text(" A  FACT ") == normalize_instruction_text("a fact")

    disjoint = _content(
        memory_id="62345678-1234-4234-9234-123456789abc",
        scope=MemoryScope(languages=("java",)),
    )
    identical = _content(memory_id="72345678-1234-4234-9234-123456789abc")
    assert find_conflicts(candidate, (disjoint, identical)) == ()


@pytest.mark.case("UT-M2-016")
def test_forbidden_suppression_policy() -> None:
    assert not is_forbidden_suppression(
        polarity=MemoryPolarity.REINFORCE,
        category=MemoryCategory.SECURITY,
        severity=Severity.CRITICAL,
    )
    assert is_forbidden_suppression(
        polarity=MemoryPolarity.SUPPRESS,
        category=MemoryCategory.SECURITY,
        severity=None,
    )
    assert is_forbidden_suppression(
        polarity=MemoryPolarity.SUPPRESS,
        category=MemoryCategory.FRAMEWORK,
        severity=Severity.HIGH,
    )
    assert not is_forbidden_suppression(
        polarity=MemoryPolarity.SUPPRESS,
        category=MemoryCategory.FRAMEWORK,
        severity=Severity.LOW,
    )


@pytest.mark.case("UT-M2-013")
def test_retrieval_scores_every_scope_signal_and_ignores_context_lines() -> None:
    diff = (
        "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"
        "@@ -1 +1,2 @@\n old\n+FastAPI Depends(load_user) security\n"
    )
    files = UnifiedDiffParser().parse(diff).files

    assert score_memory(_content(), files, risk=RiskLevel.HIGH, perspective="security") == 320
    context_only = _content(scope=MemoryScope(keywords=("old",)))
    assert score_memory(context_only, files, risk=RiskLevel.LOW, perspective="style") == 0
    assert "memory_id:" in render_memory_block(_content())
    assert estimate_memory_tokens(_content()) > 0


@pytest.mark.case("UT-M2-013")
@pytest.mark.parametrize(
    "scope",
    [
        MemoryScope(paths=("tests/**",), languages=("python",)),
        MemoryScope(paths=("src/**",), languages=("java",)),
        MemoryScope(paths=("src/**",), frameworks=("django",)),
        MemoryScope(paths=("src/**",), symbols=("other_symbol",)),
        MemoryScope(paths=("src/**",), keywords=("old",)),
        MemoryScope(paths=("src/**",), risk_tags=("low",)),
    ],
)
def test_retrieval_rejects_a_mismatching_scope_group(scope: MemoryScope) -> None:
    """一部条件の一致だけで範囲外のメモリが注入されないことを確認する。"""
    diff = (
        "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"
        "@@ -1 +1,2 @@\n old\n+FastAPI Depends(load_user) security\n"
    )
    files = UnifiedDiffParser().parse(diff).files
    assert (
        score_memory(_content(scope=scope), files, risk=RiskLevel.HIGH, perspective="security") == 0
    )


@pytest.mark.case("UT-M2-013")
def test_retrieval_requires_all_groups_to_match_the_same_file() -> None:
    """別ファイルの一致条件を合成せず、一致ファイルの最大スコアだけを使う。"""
    diff = (
        "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"
        "@@ -0,0 +1 @@\n+value = 1\n"
        "diff --git a/tests/test_app.py b/tests/test_app.py\n"
        "--- a/tests/test_app.py\n+++ b/tests/test_app.py\n@@ -0,0 +1 @@\n+needle = 1\n"
    )
    files = UnifiedDiffParser().parse(diff).files
    restricted = _content(scope=MemoryScope(paths=("src/**",), keywords=("needle",)))
    assert score_memory(restricted, files, risk=RiskLevel.LOW, perspective="general") == 0
    matching = _content(
        scope=MemoryScope(paths=("src/**", "tests/**"), keywords=("needle", "absent"))
    )
    assert score_memory(matching, files, risk=RiskLevel.LOW, perspective="general") == 110
    assert score_memory(matching, (), risk=RiskLevel.LOW, perspective="general") == 0


@pytest.mark.case("IT-M2-013", "UT-M2-014")
def test_selection_filters_zero_scores_and_applies_all_budgets() -> None:
    high = ScoredMemory(_content(), 100, 10, NOW)
    oversized = ScoredMemory(_content(memory_id="22345678-1234-4234-9234-123456789abc"), 90, 51)
    total_over = ScoredMemory(_content(memory_id="32345678-1234-4234-9234-123456789abc"), 80, 45)
    max_over = ScoredMemory(_content(memory_id="42345678-1234-4234-9234-123456789abc"), 70, 10)
    zero = ScoredMemory(_content(memory_id="52345678-1234-4234-9234-123456789abc"), 0, 1)

    selection = select_memories(
        (zero, total_over, oversized, max_over, high),
        max_memories=1,
        memory_token_budget=40,
        per_memory_token_budget=50,
    )

    assert selection.selected == (high,)
    assert selection.truncated_count == 3
    assert selection.total_tokens == 10


@pytest.mark.case("IT-M2-013", "UT-M2-014")
def test_selection_distinguishes_per_item_total_and_count_limits() -> None:
    first = ScoredMemory(_content(), 100, 30)
    second = ScoredMemory(_content(memory_id="22345678-1234-4234-9234-123456789abc"), 90, 25)
    third = ScoredMemory(_content(memory_id="32345678-1234-4234-9234-123456789abc"), 80, 60)
    total = select_memories(
        (first, second), max_memories=5, memory_token_budget=50, per_memory_token_budget=100
    )
    per_item = select_memories(
        (third,), max_memories=5, memory_token_budget=100, per_memory_token_budget=50
    )
    count = select_memories(
        (first, second), max_memories=1, memory_token_budget=100, per_memory_token_budget=100
    )
    assert total.truncated_count == 1
    assert per_item.truncated_count == 1
    assert count.truncated_count == 1


@pytest.mark.case("UT-M2-012")
def test_scope_exact_paths_can_intersect_or_be_disjoint() -> None:
    assert MemoryScope(paths=("src/app.py",)).intersects(MemoryScope(paths=("src/app.py",)))
    assert not MemoryScope(paths=("src/app.py",)).intersects(MemoryScope(paths=("tests/app.py",)))
    assert MemoryScope(paths=("src/*.py",)).intersects(MemoryScope(paths=("src/app.py",)))


def _content_values() -> dict[str, object]:
    return {
        "memory_id": MEMORY_ID,
        "version": MemoryVersion(1),
        "category": MemoryCategory.FRAMEWORK,
        "scope": MemoryScope(
            languages=("python",),
            frameworks=("fastapi",),
            paths=("src/**",),
            symbols=("load_user",),
            keywords=("Depends",),
            risk_tags=("security",),
        ),
        "fact": "A fact",
        "review_instruction": "An instruction",
        "polarity": MemoryPolarity.REINFORCE,
        "confidence": 0.8,
        "evidence_summary": "Evidence",
        "created_by": "actor",
        "created_at": NOW,
    }


def _content(**overrides: object) -> MemoryContent:
    values = _content_values()
    if "memory_id" in overrides and isinstance(overrides["memory_id"], str):
        overrides["memory_id"] = MemoryId(overrides["memory_id"])
    values.update(overrides)
    return MemoryContent(**values)  # type: ignore[arg-type]
