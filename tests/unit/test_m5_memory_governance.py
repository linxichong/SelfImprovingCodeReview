"""M5 メモリ統制モデルとユースケースの単体テスト."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from self_improving_code_review.application.governance import (
    ManageMemoryLifecycle,
    RevalidateMemory,
)
from self_improving_code_review.domain.common.enums import (
    ConflictResolution,
    ConflictStatus,
    MemoryCategory,
    MemoryPolarity,
    MemoryStatus,
    RevalidationStatus,
)
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.common.values import (
    MemoryId,
    MemoryVersion,
    ProjectId,
)
from self_improving_code_review.domain.governance.memory_models import (
    MemoryConflict,
    MemoryGovernanceResult,
    MemoryRevalidation,
)
from self_improving_code_review.domain.memory.models import MemoryContent
from self_improving_code_review.domain.memory.scope import MemoryScope
from self_improving_code_review.security.authorization import AllowListActorAuthorizer

_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
_NAIVE = _NOW.replace(tzinfo=None)
_PROJECT = ProjectId("project")
_LEFT = MemoryId("12345678-1234-4234-9234-123456789abc")
_RIGHT = MemoryId("22345678-1234-4234-9234-123456789abc")
_HASH = hash_text("content")


class _Clock:
    """固定 UTC 時刻を返す."""

    def now_utc(self) -> datetime:
        """固定時刻を返す."""
        return _NOW


def _result(
    status: MemoryStatus = MemoryStatus.ACTIVE,
) -> MemoryGovernanceResult:
    """検証用の統制結果を返す."""
    return MemoryGovernanceResult(
        _LEFT,
        MemoryVersion(1),
        status,
        2,
        _HASH,
    )


def _conflict(**changes: object) -> MemoryConflict:
    """検証用の競合を返す."""
    values: dict[str, object] = {
        "conflict_id": _HASH,
        "project_id": _PROJECT,
        "left_memory_id": _LEFT,
        "left_version": MemoryVersion(1),
        "left_content_hash": _HASH,
        "right_memory_id": _RIGHT,
        "right_version": MemoryVersion(1),
        "right_content_hash": _HASH,
        "reason_code": "DIFFERENT_FACT",
        "status": ConflictStatus.OPEN,
        "lock_version": 1,
        "detected_at": _NOW,
    }
    values.update(changes)
    return MemoryConflict(**values)  # type: ignore[arg-type]


def _revalidation(**changes: object) -> MemoryRevalidation:
    """検証用の再検証記録を返す."""
    values: dict[str, object] = {
        "revalidation_id": _HASH,
        "project_id": _PROJECT,
        "memory_id": _LEFT,
        "source_version": MemoryVersion(1),
        "status": RevalidationStatus.DUE,
        "due_at": _NOW,
        "created_at": _NOW,
    }
    values.update(changes)
    return MemoryRevalidation(**values)  # type: ignore[arg-type]


@pytest.mark.case("UT-M5-002")
def test_manage_memory_lifecycle_delegates_all_operations() -> None:
    """統制ユースケースが認可、正規化、ハッシュ化して委譲する."""
    repository = Mock()
    repository.archive.return_value = _result(MemoryStatus.ARCHIVED)
    repository.restore.return_value = _result(MemoryStatus.PENDING)
    repository.supersede.return_value = _result(MemoryStatus.SUPERSEDED)
    repository.list_conflicts.return_value = (_conflict(),)
    repository.resolve_conflict.return_value = (
        _conflict(
            status=ConflictStatus.RESOLVED,
            resolution=ConflictResolution.REJECT_CANDIDATE,
            resolved_at=_NOW,
        ),
        _result(MemoryStatus.REJECTED),
    )
    use_case = ManageMemoryLifecycle(
        repository,
        AllowListActorAuthorizer(("tanaka",), False),
        _Clock(),
    )

    assert (
        use_case.archive(
            _PROJECT,
            _LEFT,
            expected_lock_version=1,
            actor_id=" tanaka ",
            reason=" archived ",
        ).status
        is MemoryStatus.ARCHIVED
    )
    assert (
        use_case.restore(
            _PROJECT,
            _LEFT,
            expected_lock_version=2,
            actor_id="tanaka",
            reason="restored",
        ).status
        is MemoryStatus.PENDING
    )
    assert (
        use_case.supersede(
            _PROJECT,
            _LEFT,
            _RIGHT,
            expected_lock_version=3,
            actor_id="tanaka",
            reason="superseded",
        ).status
        is MemoryStatus.SUPERSEDED
    )
    assert use_case.list_conflicts(_PROJECT, ConflictStatus.OPEN) == (_conflict(),)
    conflict, result = use_case.resolve_conflict(
        _PROJECT,
        _HASH,
        ConflictResolution.REJECT_CANDIDATE,
        expected_lock_version=1,
        actor_id="tanaka",
        reason="resolved",
        evidence="manual evidence",
    )
    assert conflict.status is ConflictStatus.RESOLVED
    assert result.status is MemoryStatus.REJECTED
    assert repository.archive.call_args.kwargs["actor_id"] == "tanaka"
    assert repository.archive.call_args.kwargs["reason_hash"] == hash_text("archived")


def test_revalidate_memory_delegates_confirm_and_invalidate() -> None:
    """再検証ユースケースが期限と監査ハッシュを確定する."""
    repository = Mock()
    repository.list_due.return_value = (_revalidation(),)
    repository.confirm_revalidation.return_value = _result()
    repository.invalidate_revalidation.return_value = _result(MemoryStatus.ARCHIVED)
    use_case = RevalidateMemory(
        repository,
        AllowListActorAuthorizer(("tanaka",), False),
        _Clock(),
        default_days=180,
    )

    assert use_case.list_due(_PROJECT) == (_revalidation(),)
    assert (
        use_case.confirm(
            _PROJECT,
            _LEFT,
            expected_lock_version=1,
            actor_id="tanaka",
            reason="confirmed",
            evidence="evidence",
        ).status
        is MemoryStatus.ACTIVE
    )
    assert (
        use_case.invalidate(
            _PROJECT,
            _LEFT,
            expected_lock_version=2,
            actor_id="tanaka",
            reason="invalid",
            evidence="evidence",
        ).status
        is MemoryStatus.ARCHIVED
    )
    assert repository.confirm_revalidation.call_args.kwargs[
        "next_review_after"
    ] == _NOW + timedelta(days=180)


@pytest.mark.parametrize(
    "changes",
    [
        {"lock_version": 0},
        {"content_hash": "invalid"},
    ],
)
def test_governance_result_rejects_invalid_metadata(changes: dict[str, object]) -> None:
    """統制結果の不正なロック版とハッシュを拒否する."""
    with pytest.raises(InvalidInputError):
        replace(_result(), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"conflict_id": "invalid"},
        {"left_content_hash": "invalid"},
        {"right_content_hash": "invalid"},
        {"left_memory_id": _RIGHT, "right_memory_id": _LEFT},
        {"reason_code": ""},
        {"lock_version": 0},
        {"detected_at": _NAIVE},
        {"status": ConflictStatus.RESOLVED},
        {
            "status": ConflictStatus.RESOLVED,
            "resolution": ConflictResolution.REJECT_CANDIDATE,
            "resolved_at": _NAIVE,
        },
    ],
)
def test_memory_conflict_rejects_invalid_metadata(changes: dict[str, object]) -> None:
    """競合モデルの不正な識別子、順序、状態、時刻を拒否する."""
    with pytest.raises(InvalidInputError):
        _conflict(**changes)


@pytest.mark.case("UT-M5-004")
@pytest.mark.parametrize(
    "changes",
    [
        {"revalidation_id": "invalid"},
        {"due_at": _NAIVE},
        {"created_at": _NAIVE},
        {"next_review_after": _NAIVE},
    ],
)
def test_memory_revalidation_rejects_invalid_metadata(
    changes: dict[str, object],
) -> None:
    """再検証モデルの不正な識別子と時刻を拒否する."""
    with pytest.raises(InvalidInputError):
        _revalidation(**changes)


def _memory_content(**changes: object) -> MemoryContent:
    """検証用のメモリ内容を返す."""
    values: dict[str, object] = {
        "memory_id": _LEFT,
        "version": MemoryVersion(1),
        "category": MemoryCategory.FRAMEWORK,
        "scope": MemoryScope(paths=("src/**",)),
        "fact": "fact",
        "review_instruction": "instruction",
        "polarity": MemoryPolarity.REINFORCE,
        "confidence": 0.8,
        "evidence_summary": "evidence",
        "created_by": "actor",
        "created_at": _NOW,
    }
    values.update(changes)
    return MemoryContent(**values)  # type: ignore[arg-type]


@pytest.mark.case("UT-M5-004")
@pytest.mark.parametrize(
    "changes",
    [
        {"valid_from": _NOW},
        {"review_after": _NOW},
        {
            "valid_from": _NAIVE,
            "review_after": _NAIVE + timedelta(days=1),
        },
        {"valid_from": _NOW, "review_after": _NOW},
        {"framework_version_constraints": ("",)},
        {"framework_version_constraints": ("x" * 201,)},
        {"framework_version_constraints": ("django",)},
        {"framework_version_constraints": ("django[redis]>=5",)},
        {"framework_version_constraints": ('django>=5; python_version > "3"',)},
        {"framework_version_constraints": ("django @ https://example.com/pkg.whl",)},
    ],
)
def test_memory_content_rejects_invalid_lifecycle_fields(
    changes: dict[str, object],
) -> None:
    """ライフサイクル日時と版制約の不正値を拒否する."""
    with pytest.raises(InvalidInputError):
        _memory_content(**changes)


def test_memory_content_hash_includes_framework_constraints() -> None:
    """フレームワーク版制約を正規化文書と内容ハッシュへ含める."""
    unconstrained = _memory_content()
    constrained = _memory_content(framework_version_constraints=("django>=5",))

    assert "framework_version_constraints" not in unconstrained.to_document()
    assert constrained.to_document()["framework_version_constraints"] == ["django>=5"]
    assert constrained.content_hash != unconstrained.content_hash
