"""プロジェクト登録と既存資源所属を統制するユースケース."""

from __future__ import annotations

from self_improving_code_review.application.ports.authorization import ActorAuthorizer
from self_improving_code_review.application.ports.clock import Clock
from self_improving_code_review.application.ports.governance import GovernanceRepository
from self_improving_code_review.domain.common.enums import Permission
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.domain.governance.models import (
    GovernanceResourceType,
    ProjectRecord,
    ProjectStatus,
    RepositoryBinding,
    validate_governance_reason,
)
from self_improving_code_review.messages import Messages

__all__ = ["ManageProjects"]


class ManageProjects:
    """認可、監査および永続化をまとめてプロジェクト所属を管理する."""

    def __init__(
        self,
        repository: GovernanceRepository,
        authorizer: ActorAuthorizer,
        clock: Clock,
    ) -> None:
        """必要なポートを保持する."""
        self._repository = repository
        self._authorizer = authorizer
        self._clock = clock

    def register(
        self,
        project_id: ProjectId,
        *,
        name: str,
        policy_hash: str,
        actor_id: str,
        reason: str,
    ) -> ProjectRecord:
        """有効なプロジェクトを冪等に登録する."""
        normalized_reason = self._authorize(actor_id, reason)
        now = self._clock.now_utc()
        record = ProjectRecord(
            project_id=project_id,
            name=name,
            policy_hash=policy_hash,
            status=ProjectStatus.ACTIVE,
            lock_version=1,
            created_at=now,
            updated_at=now,
        )
        return self._repository.register_project(
            record,
            actor_id=actor_id.strip(),
            reason_hash=hash_text(normalized_reason),
        )

    def list_projects(self) -> tuple[ProjectRecord, ...]:
        """登録済みプロジェクトを識別子順で返す."""
        return self._repository.list_projects()

    def bind_repository(
        self,
        project_id: ProjectId,
        *,
        provider: str,
        repository_external_id: str,
        actor_id: str,
        reason: str,
    ) -> RepositoryBinding:
        """外部リポジトリを有効なプロジェクトへ一意に関連付ける."""
        project = self._require_active_project(project_id)
        normalized_reason = self._authorize(actor_id, reason)
        binding = RepositoryBinding(
            project_id=project.project_id,
            provider=provider,
            repository_external_id=repository_external_id,
            created_at=self._clock.now_utc(),
        )
        return self._repository.bind_repository(
            binding,
            actor_id=actor_id.strip(),
            reason_hash=hash_text(normalized_reason),
        )

    def adopt_existing(
        self,
        project_id: ProjectId,
        *,
        resource_type: GovernanceResourceType,
        resource_id: str,
        actor_id: str,
        reason: str,
    ) -> None:
        """所属のない既存資源一件を有効なプロジェクトへ割り当てる."""
        self._require_active_project(project_id)
        normalized_reason = self._authorize(actor_id, reason)
        normalized_resource_id = resource_id.strip()
        if not 1 <= len(normalized_resource_id) <= 512 or any(
            character.isspace() for character in normalized_resource_id
        ):
            raise InvalidInputError(Messages.GOVERNANCE_RESOURCE_ID_INVALID)
        self._repository.adopt_existing(
            project_id,
            resource_type,
            normalized_resource_id,
            actor_id=actor_id.strip(),
            reason_hash=hash_text(normalized_reason),
            occurred_at=self._clock.now_utc(),
        )

    def _authorize(self, actor_id: str, reason: str) -> str:
        """統制権限を確認し、監査理由を正規化する."""
        self._authorizer.require(actor_id, Permission.MANAGE_PROJECT)
        return validate_governance_reason(reason)

    def _require_active_project(self, project_id: ProjectId) -> ProjectRecord:
        """有効な登録済みプロジェクトを返す."""
        project = self._repository.find_project(project_id)
        if project is None:
            raise InvalidInputError(Messages.PROJECT_NOT_REGISTERED)
        if project.status is not ProjectStatus.ACTIVE:
            raise InvalidInputError(Messages.PROJECT_ARCHIVED)
        return project
