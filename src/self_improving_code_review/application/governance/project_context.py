"""M5 レビュー開始前のプロジェクト分離検証."""

from __future__ import annotations

from self_improving_code_review.application.ports.governance import GovernanceRepository
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.domain.governance.models import ProjectStatus
from self_improving_code_review.messages import Messages

__all__ = ["ProjectContextValidator"]


class ProjectContextValidator:
    """登録状態、リポジトリ所属および既存資源所属を決定的に検証する."""

    def __init__(self, repository: GovernanceRepository) -> None:
        """プロジェクト統制の読取りポートを保持する."""
        self._repository = repository

    def validate(
        self,
        project_id: ProjectId,
        *,
        provider: str,
        repository_external_id: str,
    ) -> None:
        """指定リポジトリを対象プロジェクトとして利用できることを確認する."""
        project = self._repository.find_project(project_id)
        bound_project = self._repository.find_repository_project(
            provider,
            repository_external_id,
        )
        if (
            project is None
            or project.status is not ProjectStatus.ACTIVE
            or bound_project != project_id
        ):
            raise InvalidInputError(Messages.PROJECT_CONTEXT_NOT_FOUND)
        if self._repository.has_unassigned_resources():
            raise InvalidInputError(Messages.GOVERNANCE_RESOURCES_UNASSIGNED)
