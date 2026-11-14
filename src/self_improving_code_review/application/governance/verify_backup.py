"""SQLite バックアップ検査の認可と実行順を調停する."""

from __future__ import annotations

from pathlib import Path

from self_improving_code_review.application.ports.authorization import ActorAuthorizer
from self_improving_code_review.application.ports.backup import BackupInspection, BackupInspector
from self_improving_code_review.domain.common.enums import Permission
from self_improving_code_review.domain.common.values import ProjectId

__all__ = ["VerifyBackup"]


class VerifyBackup:
    """認可済み保守担当者だけに読取り専用バックアップ検査を許可する."""

    def __init__(
        self,
        inspector: BackupInspector,
        authorizer: ActorAuthorizer,
    ) -> None:
        """検査アダプターと実行者認可ポートを保持する."""
        self._inspector = inspector
        self._authorizer = authorizer

    def execute(
        self,
        database_path: Path,
        project_id: ProjectId,
        *,
        actor_id: str,
    ) -> BackupInspection:
        """権限を確認して指定プロジェクトを含むバックアップを検査する."""
        self._authorizer.require(actor_id, Permission.VERIFY_BACKUP)
        return self._inspector.inspect(database_path, project_id)
