"""M5 メモリ統制 CLI の単体テスト."""

from __future__ import annotations

from argparse import Namespace
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from self_improving_code_review.cli import memory_commands
from self_improving_code_review.domain.common.enums import (
    ConflictResolution,
    ConflictStatus,
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

_NOW = datetime(2026, 1, 2, tzinfo=UTC)
_PROJECT = ProjectId("project")
_LEFT = MemoryId("12345678-1234-4234-9234-123456789abc")
_RIGHT = MemoryId("22345678-1234-4234-9234-123456789abc")
_HASH = hash_text("content")


def _config(*, enabled: bool = True) -> SimpleNamespace:
    """M5 CLI 用の最小設定を返す."""
    return SimpleNamespace(
        project_id="project",
        memory=SimpleNamespace(maintainers=("tanaka",), is_production=False),
        governance=SimpleNamespace(
            enabled=enabled,
            memory_lifecycle_enabled=enabled,
            sqlite_busy_timeout_ms=5000,
            revalidation_default_days=180,
        ),
    )


def _result(status: MemoryStatus = MemoryStatus.ACTIVE) -> MemoryGovernanceResult:
    """検証用統制結果を返す."""
    return MemoryGovernanceResult(_LEFT, MemoryVersion(1), status, 2, _HASH)


def _conflict() -> MemoryConflict:
    """検証用競合を返す."""
    return MemoryConflict(
        _HASH,
        _PROJECT,
        _LEFT,
        MemoryVersion(1),
        _HASH,
        _RIGHT,
        MemoryVersion(1),
        _HASH,
        "DIFFERENT_FACT",
        ConflictStatus.OPEN,
        1,
        _NOW,
    )


def _revalidation() -> MemoryRevalidation:
    """検証用再検証記録を返す."""
    return MemoryRevalidation(
        _HASH,
        _PROJECT,
        _LEFT,
        MemoryVersion(1),
        RevalidationStatus.DUE,
        _NOW,
        _NOW,
    )


def _base_arguments(command: str) -> Namespace:
    """M5 メモリコマンドの共通引数を返す."""
    return Namespace(
        memory_command=command,
        memory_id=str(_LEFT),
        replacement=str(_RIGHT),
        project=None,
        actor="tanaka",
        reason="reason",
        evidence="evidence",
        lock_version=1,
        memory_action="list",
        status=ConflictStatus.OPEN.value,
        conflict_id=_HASH,
        resolution=ConflictResolution.REJECT_CANDIDATE.value,
        scope_file=None,
    )


@pytest.mark.parametrize(
    ("command", "method", "status"),
    [
        ("archive", "archive", MemoryStatus.ARCHIVED),
        ("restore", "restore", MemoryStatus.PENDING),
        ("supersede", "supersede", MemoryStatus.SUPERSEDED),
    ],
)
def test_execute_m5_lifecycle_commands(
    command: str,
    method: str,
    status: MemoryStatus,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保管、復元、置換コマンドを対応ユースケースへ委譲する."""
    lifecycle = Mock()
    getattr(lifecycle, method).return_value = _result(status)
    monkeypatch.setattr(memory_commands, "SqliteMemoryGovernanceRepository", Mock())
    monkeypatch.setattr(memory_commands, "ManageMemoryLifecycle", lambda *_args: lifecycle)
    monkeypatch.setattr(memory_commands, "RevalidateMemory", Mock())
    write = Mock()
    monkeypatch.setattr(memory_commands, "write_document", write)

    assert (
        memory_commands._execute_m5_memory_command(
            tmp_path / "review.db",
            _config(),
            _base_arguments(command),
        )
        == 0
    )
    assert getattr(lifecycle, method).called
    assert write.call_args.args[0]["status"] == status.value


def test_execute_m5_conflict_list_and_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """競合一覧と解決結果を版付き安全文書で出力する."""
    open_conflict = _conflict()
    resolved = replace(
        open_conflict,
        status=ConflictStatus.RESOLVED,
        resolution=ConflictResolution.REJECT_CANDIDATE,
        resolved_at=_NOW,
        lock_version=2,
    )
    lifecycle = Mock()
    lifecycle.list_conflicts.return_value = (open_conflict,)
    lifecycle.resolve_conflict.return_value = (
        resolved,
        _result(MemoryStatus.REJECTED),
    )
    monkeypatch.setattr(memory_commands, "SqliteMemoryGovernanceRepository", Mock())
    monkeypatch.setattr(memory_commands, "ManageMemoryLifecycle", lambda *_args: lifecycle)
    monkeypatch.setattr(memory_commands, "RevalidateMemory", Mock())
    write = Mock()
    monkeypatch.setattr(memory_commands, "write_document", write)

    arguments = _base_arguments("conflict")
    assert (
        memory_commands._execute_m5_memory_command(tmp_path / "review.db", _config(), arguments)
        == 0
    )
    assert write.call_args.args[0]["conflicts"][0]["resolution"] is None

    arguments.memory_action = "resolve"
    assert (
        memory_commands._execute_m5_memory_command(tmp_path / "review.db", _config(), arguments)
        == 0
    )
    document = write.call_args.args[0]
    assert document["conflict"]["resolution"] == "reject-candidate"
    assert document["conflict"]["resolved_at"] == _NOW.isoformat()


@pytest.mark.parametrize("action", ["confirm", "invalidate"])
def test_execute_m5_revalidation_commands(
    action: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """期限一覧、再確認、無効化を対応ユースケースへ委譲する."""
    revalidation = Mock()
    revalidation.list_due.return_value = (_revalidation(),)
    revalidation.confirm.return_value = _result()
    revalidation.invalidate.return_value = _result(MemoryStatus.ARCHIVED)
    monkeypatch.setattr(memory_commands, "SqliteMemoryGovernanceRepository", Mock())
    monkeypatch.setattr(memory_commands, "ManageMemoryLifecycle", Mock())
    monkeypatch.setattr(memory_commands, "RevalidateMemory", lambda *_args, **_kwargs: revalidation)
    write = Mock()
    monkeypatch.setattr(memory_commands, "write_document", write)

    arguments = _base_arguments("revalidation")
    assert (
        memory_commands._execute_m5_memory_command(tmp_path / "review.db", _config(), arguments)
        == 0
    )
    assert write.call_args.args[0]["revalidations"][0]["status"] == "due"

    arguments.memory_action = action
    assert (
        memory_commands._execute_m5_memory_command(tmp_path / "review.db", _config(), arguments)
        == 0
    )
    assert getattr(revalidation, action).called


def test_m5_memory_command_requires_feature_gate(tmp_path: Path) -> None:
    """M5 メモリ機能が無効な場合は操作を拒否する."""
    with pytest.raises(InvalidInputError, match="governance"):
        memory_commands._execute_m5_memory_command(
            tmp_path / "review.db",
            _config(enabled=False),
            _base_arguments("archive"),
        )


def test_execute_memory_command_routes_m5_and_records_detected_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M5 操作の振分けと承認時の競合記録を確認する."""
    config = SimpleNamespace(
        database=SimpleNamespace(path=Path("review.db")),
        project_id="project",
        memory=SimpleNamespace(maintainers=("tanaka",), is_production=False),
        governance=SimpleNamespace(
            enabled=True,
            memory_lifecycle_enabled=True,
            sqlite_busy_timeout_ms=5000,
        ),
    )
    monkeypatch.setattr(memory_commands, "load_config", lambda _path: config)
    routed = Mock(return_value=0)
    monkeypatch.setattr(memory_commands, "_execute_m5_memory_command", routed)
    arguments = _base_arguments("archive")
    arguments.repository = str(tmp_path)
    arguments.config = None
    assert memory_commands.execute_memory_command(arguments) == 0
    assert routed.called

    status_change = SimpleNamespace(
        memory_id=_LEFT,
        status=MemoryStatus.CONFLICT,
        version=MemoryVersion(1),
        conflict_reason_codes=("DIFFERENT_FACT",),
    )
    memory_repository = Mock()
    memory_repository.find.return_value = (SimpleNamespace(project_id=_PROJECT), Mock())
    monkeypatch.setattr(memory_commands, "SqliteUnitOfWorkFactory", Mock())
    monkeypatch.setattr(
        memory_commands,
        "ShortTransactionMemoryRepository",
        lambda _factory: memory_repository,
    )
    use_case = Mock()
    use_case.execute.return_value = status_change
    monkeypatch.setattr(memory_commands, "ApproveMemory", lambda **_kwargs: use_case)
    governance_repository = Mock()
    monkeypatch.setattr(
        memory_commands,
        "SqliteMemoryGovernanceRepository",
        lambda *_args, **_kwargs: governance_repository,
    )
    monkeypatch.setattr(memory_commands, "_write_change", Mock())
    arguments.memory_command = "approve"
    arguments.scope_file = None
    assert memory_commands.execute_memory_command(arguments) == 2
    assert governance_repository.record_detected_conflicts.called

    memory_repository.find.return_value = None
    with pytest.raises(InvalidInputError):
        memory_commands.execute_memory_command(arguments)
