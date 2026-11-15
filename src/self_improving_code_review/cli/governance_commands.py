"""M5 プロジェクト統制 CLI の構成起点."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from self_improving_code_review.application.config.settings import AppConfig, load_config
from self_improving_code_review.application.governance import ManageProjects, VerifyBackup
from self_improving_code_review.application.ports.backup import BackupInspection
from self_improving_code_review.cli.output import write_document
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.domain.governance.models import (
    GovernanceResourceType,
    ProjectRecord,
    RepositoryBinding,
)
from self_improving_code_review.infrastructure.persistence.backup_inspector import (
    SqliteBackupInspector,
)
from self_improving_code_review.infrastructure.persistence.governance_repository import (
    SqliteGovernanceRepository,
)
from self_improving_code_review.infrastructure.system import SystemClock
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.authorization import AllowListActorAuthorizer

__all__ = ["execute_governance_command"]


def execute_governance_command(arguments: Namespace) -> int:
    """CLI 引数からプロジェクト統制操作を一回実行する."""
    repository_path = Path(arguments.repository).resolve()
    config = load_config(Path(arguments.config) if arguments.config else None)
    if arguments.governance_command == "backup":
        return _execute_backup_verification(arguments, repository_path, config)

    governance_repository = SqliteGovernanceRepository(
        _database_path(repository_path, config),
        busy_timeout_ms=config.governance.sqlite_busy_timeout_ms,
    )
    use_case = ManageProjects(
        governance_repository,
        AllowListActorAuthorizer(
            maintainers=config.memory.maintainers,
            is_production=config.memory.is_production,
        ),
        SystemClock(),
    )

    operation = arguments.project_command
    if operation == "list":
        _write_project_list(use_case.list_projects())
        return 0
    project_id = ProjectId(arguments.project)
    if operation == "register":
        project = use_case.register(
            project_id,
            name=arguments.name,
            policy_hash=config.pipeline_content_hash,
            actor_id=arguments.actor,
            reason=arguments.reason,
        )
        _write_project(project)
        return 0
    if operation == "bind-repository":
        binding = use_case.bind_repository(
            project_id,
            provider=arguments.provider,
            repository_external_id=arguments.external_repository,
            actor_id=arguments.actor,
            reason=arguments.reason,
        )
        _write_binding(binding)
        return 0
    use_case.adopt_existing(
        project_id,
        resource_type=GovernanceResourceType(arguments.resource_type),
        resource_id=arguments.resource_id,
        actor_id=arguments.actor,
        reason=arguments.reason,
    )
    write_document(
        {
            "schema_version": 2,
            "project_id": str(project_id),
            "resource_type": arguments.resource_type,
            "resource_id_hash": _safe_resource_hash(arguments.resource_id),
            "status": "adopted",
        },
        None,
    )
    return 0


def _write_project(project: ProjectRecord) -> None:
    """登録結果を本文なしの JSON として出力する."""
    write_document({"schema_version": 2, **_project_document(project)}, None)


def _write_project_list(projects: tuple[ProjectRecord, ...]) -> None:
    """登録済みプロジェクトを安定順で出力する."""
    write_document(
        {
            "schema_version": 2,
            "projects": [_project_document(project) for project in projects],
        },
        None,
    )


def _project_document(project: ProjectRecord) -> dict[str, object]:
    """プロジェクトの安全な公開項目だけを返す."""
    return {
        "project_id": str(project.project_id),
        "name": project.name,
        "policy_hash": project.policy_hash,
        "status": project.status.value,
        "lock_version": project.lock_version,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


def _write_binding(binding: RepositoryBinding) -> None:
    """リポジトリ対応の安全な識別情報を出力する."""
    write_document(
        {
            "schema_version": 2,
            "project_id": str(binding.project_id),
            "provider": binding.provider,
            "repository_external_id": binding.repository_external_id,
            "created_at": binding.created_at.isoformat(),
        },
        None,
    )


def _safe_resource_hash(resource_id: str) -> str:
    """既存資源の生識別子を出力しないためのハッシュを返す."""
    from self_improving_code_review.domain.common.hashing import hash_text

    return hash_text(resource_id.strip())


def _execute_backup_verification(
    arguments: Namespace,
    repository: Path,
    config: AppConfig,
) -> int:
    """復元済み DB を読取り専用で検査し、機械可読な報告を保存する."""
    database_path = _backup_database_path(repository, arguments.database)
    inspection = VerifyBackup(
        SqliteBackupInspector(busy_timeout_ms=config.governance.sqlite_busy_timeout_ms),
        AllowListActorAuthorizer(
            maintainers=config.memory.maintainers,
            is_production=config.memory.is_production,
        ),
    ).execute(database_path, ProjectId(arguments.project), actor_id=arguments.actor)
    write_document(
        _backup_document(ProjectId(arguments.project), inspection),
        Path(arguments.output),
    )
    return 0 if inspection.is_valid else 5


def _backup_document(
    project_id: ProjectId,
    inspection: BackupInspection,
) -> dict[str, object]:
    """DB パスや行内容を含まないバックアップ検査文書を返す."""
    return {
        "schema_version": 2,
        "project_id": str(project_id),
        "status": "verified" if inspection.is_valid else "invalid",
        "checks": dict(inspection.checks),
        "counts": dict(inspection.counts),
        "failure_codes": list(inspection.failure_codes),
    }


def _backup_database_path(repository: Path, raw_path: str) -> Path:
    """検査対象をリポジトリ配下の既存通常ファイルへ制限する."""
    configured = Path(raw_path)
    candidate = (
        configured.resolve() if configured.is_absolute() else (repository / configured).resolve()
    )
    if not candidate.is_relative_to(repository):
        raise InvalidInputError(
            Messages.configured_path_outside_repository(Messages.BACKUP_DATABASE_PATH_LABEL)
        )
    if not candidate.is_file():
        raise InvalidInputError(Messages.BACKUP_DATABASE_NOT_FOUND)
    return candidate


def _database_path(repository: Path, config: AppConfig) -> Path:
    """設定された DB パスをリポジトリ内へ制限して返す."""
    configured = config.database.path
    candidate = (
        configured.resolve() if configured.is_absolute() else (repository / configured).resolve()
    )
    if not candidate.is_relative_to(repository):
        raise InvalidInputError(
            Messages.configured_path_outside_repository(Messages.DATABASE_PATH_LABEL)
        )
    return candidate
