"""M5 プロジェクト統制の SQLite 永続化アダプター."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from self_improving_code_review.application.ports.governance import GovernanceRepository
from self_improving_code_review.domain.common.errors import DatabaseError, InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.domain.governance.models import (
    GovernanceResourceType,
    ProjectRecord,
    ProjectStatus,
    RepositoryBinding,
)
from self_improving_code_review.infrastructure.persistence.sqlite import initialize_database
from self_improving_code_review.messages import Messages

__all__ = ["SqliteGovernanceRepository"]


def _raise_project_already_registered() -> None:
    """競合するプロジェクト再登録を失敗させる."""
    raise InvalidInputError(Messages.PROJECT_ALREADY_REGISTERED)


def _raise_repository_already_bound() -> None:
    """別プロジェクトへ登録済みのリポジトリ対応を失敗させる."""
    raise InvalidInputError(Messages.REPOSITORY_ALREADY_BOUND)


class SqliteGovernanceRepository(GovernanceRepository):
    """短い SQLite トランザクションでプロジェクト所属を保存する."""

    def __init__(self, database_path: Path, *, busy_timeout_ms: int = 5000) -> None:
        """保存先を初期化し、接続ごとのビジー待機時間を保持する."""
        self._database_path = database_path
        self._busy_timeout_ms = busy_timeout_ms
        initialize_database(database_path)

    def register_project(
        self,
        project: ProjectRecord,
        *,
        actor_id: str,
        reason_hash: str,
    ) -> ProjectRecord:
        """同一内容を許容し、異なる再登録を拒否する."""
        try:
            with closing(self._connection()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = self._find_project(connection, project.project_id)
                if existing is not None:
                    if (
                        existing.name == project.name
                        and existing.policy_hash == project.policy_hash
                        and existing.status is project.status
                    ):
                        return existing
                    _raise_project_already_registered()
                connection.execute(
                    "INSERT INTO projects("
                    "project_id, name, policy_hash, status, lock_version, created_at, updated_at"
                    ") VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(project.project_id),
                        project.name,
                        project.policy_hash,
                        project.status.value,
                        project.lock_version,
                        project.created_at.isoformat(),
                        project.updated_at.isoformat(),
                    ),
                )
                self._audit(
                    connection,
                    project.project_id,
                    operation="register-project",
                    target_type="project",
                    target_id=str(project.project_id),
                    actor_id=actor_id,
                    reason_hash=reason_hash,
                    occurred_at=project.created_at,
                )
                return project
        except InvalidInputError:
            raise
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.GOVERNANCE_PROJECT_REGISTER_FAILED) from exc

    def list_projects(self) -> tuple[ProjectRecord, ...]:
        """全プロジェクトを識別子順で返す."""
        try:
            with closing(self._connection()) as connection, connection:
                rows = connection.execute(
                    "SELECT project_id, name, policy_hash, status, lock_version, "
                    "created_at, updated_at FROM projects ORDER BY project_id"
                ).fetchall()
            return tuple(self._project_from_row(row) for row in rows)
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.GOVERNANCE_PROJECT_LIST_FAILED) from exc

    def find_project(self, project_id: ProjectId) -> ProjectRecord | None:
        """対象プロジェクトを返し、存在しない場合は None を返す."""
        try:
            with closing(self._connection()) as connection, connection:
                return self._find_project(connection, project_id)
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.GOVERNANCE_PROJECT_READ_FAILED) from exc

    def find_repository_project(
        self,
        provider: str,
        repository_external_id: str,
    ) -> ProjectId | None:
        """リポジトリの所属プロジェクトを返す."""
        try:
            with closing(self._connection()) as connection, connection:
                row = connection.execute(
                    "SELECT project_id FROM project_repositories "
                    "WHERE provider = ? AND repository_external_id = ?",
                    (provider, repository_external_id),
                ).fetchone()
            return None if row is None else ProjectId(str(row[0]))
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.GOVERNANCE_CONTEXT_VALIDATION_FAILED) from exc

    def has_unassigned_resources(self) -> bool:
        """所属対応表に存在しない正解集合または SCM 変更要求を検出する."""
        try:
            with closing(self._connection()) as connection, connection:
                row = connection.execute(
                    "SELECT "
                    "EXISTS(SELECT 1 FROM ground_truth_sets AS source "
                    "WHERE NOT EXISTS("
                    "SELECT 1 FROM project_ground_truth_sets AS assigned "
                    "WHERE assigned.set_id = source.set_id"
                    ")) OR "
                    "EXISTS(SELECT 1 FROM scm_changes AS source "
                    "WHERE NOT EXISTS("
                    "SELECT 1 FROM project_scm_changes AS assigned "
                    "WHERE assigned.provider = source.provider "
                    "AND assigned.repository_external_id = source.repository_external_id "
                    "AND assigned.change_external_id = source.change_external_id"
                    "))"
                ).fetchone()
            return bool(row is not None and int(str(row[0])))
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.GOVERNANCE_CONTEXT_VALIDATION_FAILED) from exc

    def bind_repository(
        self,
        binding: RepositoryBinding,
        *,
        actor_id: str,
        reason_hash: str,
    ) -> RepositoryBinding:
        """リポジトリの一意なプロジェクト所属を保存する."""
        try:
            with closing(self._connection()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT project_id, created_at FROM project_repositories "
                    "WHERE provider = ? AND repository_external_id = ?",
                    (binding.provider, binding.repository_external_id),
                ).fetchone()
                if row is not None:
                    if str(row[0]) != str(binding.project_id):
                        _raise_repository_already_bound()
                    return RepositoryBinding(
                        project_id=binding.project_id,
                        provider=binding.provider,
                        repository_external_id=binding.repository_external_id,
                        created_at=datetime.fromisoformat(str(row[1])),
                    )
                connection.execute(
                    "INSERT INTO project_repositories("
                    "project_id, provider, repository_external_id, created_at"
                    ") VALUES(?, ?, ?, ?)",
                    (
                        str(binding.project_id),
                        binding.provider,
                        binding.repository_external_id,
                        binding.created_at.isoformat(),
                    ),
                )
                self._audit(
                    connection,
                    binding.project_id,
                    operation="bind-repository",
                    target_type="repository",
                    target_id=f"{binding.provider}|{binding.repository_external_id}",
                    actor_id=actor_id,
                    reason_hash=reason_hash,
                    occurred_at=binding.created_at,
                )
                return binding
        except InvalidInputError:
            raise
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.GOVERNANCE_REPOSITORY_BIND_FAILED) from exc

    def adopt_existing(
        self,
        project_id: ProjectId,
        resource_type: GovernanceResourceType,
        resource_id: str,
        *,
        actor_id: str,
        reason_hash: str,
        occurred_at: datetime,
    ) -> None:
        """既存の正解集合または SCM 変更要求へ所属を設定する."""
        try:
            with closing(self._connection()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                is_created = (
                    self._adopt_ground_truth(connection, project_id, resource_id, occurred_at)
                    if resource_type is GovernanceResourceType.GROUND_TRUTH_SET
                    else self._adopt_scm_change(connection, project_id, resource_id, occurred_at)
                )
                if not is_created:
                    return
                self._audit(
                    connection,
                    project_id,
                    operation="adopt-existing",
                    target_type=resource_type.value,
                    target_id=resource_id,
                    actor_id=actor_id,
                    reason_hash=reason_hash,
                    occurred_at=occurred_at,
                )
        except InvalidInputError:
            raise
        except sqlite3.Error as exc:
            raise DatabaseError(Messages.GOVERNANCE_RESOURCE_ADOPT_FAILED) from exc

    def _connection(self) -> sqlite3.Connection:
        """外部キーとビジー待機を設定した短命接続を返す."""
        connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        return connection

    @staticmethod
    def _find_project(
        connection: sqlite3.Connection,
        project_id: ProjectId,
    ) -> ProjectRecord | None:
        """接続内でプロジェクト一件を検索する."""
        row = connection.execute(
            "SELECT project_id, name, policy_hash, status, lock_version, created_at, updated_at "
            "FROM projects WHERE project_id = ?",
            (str(project_id),),
        ).fetchone()
        return None if row is None else SqliteGovernanceRepository._project_from_row(row)

    @staticmethod
    def _project_from_row(row: sqlite3.Row | tuple[object, ...]) -> ProjectRecord:
        """SQLite 行を検証済みプロジェクトへ変換する."""
        return ProjectRecord(
            project_id=ProjectId(str(row[0])),
            name=str(row[1]),
            policy_hash=str(row[2]),
            status=ProjectStatus(str(row[3])),
            lock_version=int(str(row[4])),
            created_at=datetime.fromisoformat(str(row[5])),
            updated_at=datetime.fromisoformat(str(row[6])),
        )

    @staticmethod
    def _adopt_ground_truth(
        connection: sqlite3.Connection,
        project_id: ProjectId,
        resource_id: str,
        occurred_at: datetime,
    ) -> bool:
        """正解集合一件へ所属を設定し、新規作成時は True を返す."""
        exists = connection.execute(
            "SELECT 1 FROM ground_truth_sets WHERE set_id = ?",
            (resource_id,),
        ).fetchone()
        if exists is None:
            raise InvalidInputError(Messages.GOVERNANCE_RESOURCE_NOT_FOUND)
        row = connection.execute(
            "SELECT project_id FROM project_ground_truth_sets WHERE set_id = ?",
            (resource_id,),
        ).fetchone()
        if row is not None:
            if str(row[0]) != str(project_id):
                raise InvalidInputError(Messages.GOVERNANCE_RESOURCE_OTHER_PROJECT)
            return False
        connection.execute(
            "INSERT INTO project_ground_truth_sets(project_id, set_id, adopted_at) VALUES(?, ?, ?)",
            (str(project_id), resource_id, occurred_at.isoformat()),
        )
        return True

    @staticmethod
    def _adopt_scm_change(
        connection: sqlite3.Connection,
        project_id: ProjectId,
        resource_id: str,
        occurred_at: datetime,
    ) -> bool:
        """SCM 複合 ID の変更要求へ所属を設定し、新規作成時は True を返す."""
        parts = resource_id.split("|")
        if len(parts) != 3 or not all(parts):
            raise InvalidInputError(Messages.SCM_RESOURCE_ID_INVALID)
        provider, repository, change = parts
        exists = connection.execute(
            "SELECT 1 FROM scm_changes WHERE provider = ? "
            "AND repository_external_id = ? AND change_external_id = ?",
            (provider, repository, change),
        ).fetchone()
        if exists is None:
            raise InvalidInputError(Messages.GOVERNANCE_RESOURCE_NOT_FOUND)
        row = connection.execute(
            "SELECT project_id FROM project_scm_changes WHERE provider = ? "
            "AND repository_external_id = ? AND change_external_id = ?",
            (provider, repository, change),
        ).fetchone()
        if row is not None:
            if str(row[0]) != str(project_id):
                raise InvalidInputError(Messages.GOVERNANCE_RESOURCE_OTHER_PROJECT)
            return False
        connection.execute(
            "INSERT INTO project_scm_changes("
            "project_id, provider, repository_external_id, change_external_id, adopted_at"
            ") VALUES(?, ?, ?, ?, ?)",
            (str(project_id), provider, repository, change, occurred_at.isoformat()),
        )
        return True

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        project_id: ProjectId,
        *,
        operation: str,
        target_type: str,
        target_id: str,
        actor_id: str,
        reason_hash: str,
        occurred_at: datetime,
    ) -> None:
        """本文を保存せず統制操作の監査行を追加する."""
        connection.execute(
            "INSERT INTO governance_audit("
            "project_id, operation, target_type, target_id_hash, actor_id, reason_hash, occurred_at"
            ") VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                str(project_id),
                operation,
                target_type,
                hash_text(target_id),
                actor_id,
                reason_hash,
                occurred_at.isoformat(),
            ),
        )
