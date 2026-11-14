from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from self_improving_code_review.application.governance.verify_backup import VerifyBackup
from self_improving_code_review.application.ports.backup import BackupInspection
from self_improving_code_review.domain.common.enums import Permission
from self_improving_code_review.domain.common.errors import AuthorizationError
from self_improving_code_review.domain.common.values import ProjectId


@dataclass
class _Authorizer:
    """テスト用に要求された権限を記録する。"""

    should_reject: bool = False
    permissions: list[Permission] = field(default_factory=list)

    def require(self, actor_id: str, permission: Permission) -> None:
        """権限を記録し、設定時だけ拒否する。"""
        _ = actor_id
        self.permissions.append(permission)
        if self.should_reject:
            raise AuthorizationError("rejected")


@dataclass
class _Inspector:
    """テスト用の固定検査結果を返す。"""

    result: BackupInspection
    calls: list[tuple[Path, ProjectId]] = field(default_factory=list)

    def inspect(self, database_path: Path, project_id: ProjectId) -> BackupInspection:
        """呼出しを記録して固定結果を返す。"""
        self.calls.append((database_path, project_id))
        return self.result


def test_verify_backup_authorizes_and_returns_inspection(tmp_path: Path) -> None:
    inspection = BackupInspection((("integrity", True),), (("review_runs", 2),), ())
    inspector = _Inspector(inspection)
    authorizer = _Authorizer()

    result = VerifyBackup(inspector, authorizer).execute(
        tmp_path / "backup.db",
        ProjectId("project-a"),
        actor_id="tanaka",
    )

    assert result is inspection
    assert result.is_valid is True
    assert inspector.calls == [(tmp_path / "backup.db", ProjectId("project-a"))]
    assert authorizer.permissions == [Permission.VERIFY_BACKUP]


def test_verify_backup_stops_before_inspection_when_unauthorized(tmp_path: Path) -> None:
    inspection = BackupInspection((("integrity", False),), (), ("INVALID",))
    inspector = _Inspector(inspection)
    authorizer = _Authorizer(should_reject=True)

    with pytest.raises(AuthorizationError):
        VerifyBackup(inspector, authorizer).execute(
            tmp_path / "backup.db",
            ProjectId("project-a"),
            actor_id="outsider",
        )

    assert inspection.is_valid is False
    assert inspector.calls == []
