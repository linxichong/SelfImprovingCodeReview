"""M5 プロジェクト統制の永続化ポート."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.domain.governance.models import (
    GovernanceResourceType,
    ProjectRecord,
    RepositoryBinding,
)

__all__ = ["GovernanceRepository"]


class GovernanceRepository(Protocol):
    """プロジェクト、リポジトリ対応および既存資源の所属を保存する."""

    def register_project(
        self, project: ProjectRecord, *, actor_id: str, reason_hash: str
    ) -> ProjectRecord:
        """プロジェクトを冪等に登録する."""
        ...

    def list_projects(self) -> tuple[ProjectRecord, ...]:
        """全プロジェクトを識別子順で返す."""
        ...

    def find_project(self, project_id: ProjectId) -> ProjectRecord | None:
        """対象プロジェクトを返し、存在しない場合は None を返す."""
        ...

    def find_repository_project(
        self,
        provider: str,
        repository_external_id: str,
    ) -> ProjectId | None:
        """リポジトリの所属プロジェクトを返す."""
        ...

    def has_unassigned_resources(self) -> bool:
        """プロジェクト所属のない既存資源が存在する場合は True を返す."""
        ...

    def bind_repository(
        self,
        binding: RepositoryBinding,
        *,
        actor_id: str,
        reason_hash: str,
    ) -> RepositoryBinding:
        """リポジトリを一つのプロジェクトへ冪等に関連付ける."""
        ...

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
        """既存資源一件へプロジェクト所属を割り当てる."""
        ...
