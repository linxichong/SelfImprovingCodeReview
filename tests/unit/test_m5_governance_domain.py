from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from self_improving_code_review.application.governance import (
    ManageProjects,
    ProjectContextValidator,
)
from self_improving_code_review.domain.common.enums import Permission
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.domain.governance.models import (
    GovernanceResourceType,
    ProjectRecord,
    ProjectStatus,
    RepositoryBinding,
    validate_governance_reason,
)
from self_improving_code_review.messages import Messages

_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
_HASH = "a" * 64


class _Clock:
    def now_utc(self) -> datetime:
        return _NOW


class _Authorizer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Permission]] = []

    def require(self, actor_id: str, permission: Permission) -> None:
        self.calls.append((actor_id, permission))


class _Repository:
    def __init__(self) -> None:
        self.projects: dict[str, ProjectRecord] = {}
        self.bindings: list[RepositoryBinding] = []
        self.adoptions: list[tuple[ProjectId, GovernanceResourceType, str]] = []

        self.audits: list[tuple[str, str, datetime | None]] = []
        self.has_unassigned = False

    def register_project(
        self, project: ProjectRecord, *, actor_id: str, reason_hash: str
    ) -> ProjectRecord:
        self.projects[str(project.project_id)] = project
        self.audits.append((actor_id, reason_hash, None))
        return project

    def list_projects(self) -> tuple[ProjectRecord, ...]:
        return tuple(self.projects[key] for key in sorted(self.projects))

    def find_project(self, project_id: ProjectId) -> ProjectRecord | None:
        return self.projects.get(str(project_id))

    def find_repository_project(
        self,
        provider: str,
        repository_external_id: str,
    ) -> ProjectId | None:
        for binding in self.bindings:
            if (
                binding.provider == provider
                and binding.repository_external_id == repository_external_id
            ):
                return binding.project_id
        return None

    def has_unassigned_resources(self) -> bool:
        return self.has_unassigned

    def bind_repository(
        self,
        binding: RepositoryBinding,
        *,
        actor_id: str,
        reason_hash: str,
    ) -> RepositoryBinding:
        self.bindings.append(binding)
        self.audits.append((actor_id, reason_hash, None))
        return binding

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
        self.adoptions.append((project_id, resource_type, resource_id))

        self.audits.append((actor_id, reason_hash, occurred_at))


def _project(**changes: object) -> ProjectRecord:
    project = ProjectRecord(
        project_id=ProjectId("project-a"),
        name="Project A",
        policy_hash=_HASH,
        status=ProjectStatus.ACTIVE,
        lock_version=1,
        created_at=_NOW,
        updated_at=_NOW,
    )
    return replace(project, **changes)


@pytest.mark.case("UT-M5-001")
@pytest.mark.parametrize(
    "changes",
    [
        {"name": " "},
        {"name": "x" * 101},
        {"name": "bad\nname"},
        {"policy_hash": "bad"},
        {"lock_version": 0},
        {"created_at": _NOW.replace(tzinfo=None)},
        {"updated_at": _NOW.replace(tzinfo=None)},
    ],
)
def test_project_record_rejects_invalid_values(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidInputError):
        _project(**changes)


@pytest.mark.case("UT-M5-001")
@pytest.mark.parametrize(
    ("provider", "repository"),
    [
        ("", "owner/repository"),
        ("Git Hub", "owner/repository"),
        ("github", ""),
        ("github", "owner /repository"),
    ],
)
def test_repository_binding_rejects_invalid_identity(provider: str, repository: str) -> None:
    with pytest.raises(InvalidInputError):
        RepositoryBinding(ProjectId("project-a"), provider, repository, _NOW)


@pytest.mark.case("UT-M5-001")
def test_repository_binding_rejects_non_utc_timestamp() -> None:
    """リポジトリ所属の日時に UTC 以外を許可しないことを確認する."""
    with pytest.raises(InvalidInputError):
        RepositoryBinding(
            ProjectId("project-a"), "github", "owner/repository", _NOW.replace(tzinfo=None)
        )


@pytest.mark.case("UT-M5-001")
def test_repository_binding_normalizes_identity() -> None:
    binding = RepositoryBinding(ProjectId("project-a"), " GitHub ", "owner/repository", _NOW)

    assert binding.provider == "github"
    assert binding.repository_external_id == "owner/repository"


@pytest.mark.case("UT-M5-001")
@pytest.mark.parametrize("reason", ["", " ", "bad\nreason", "x" * 501])
def test_governance_reason_is_validated(reason: str) -> None:
    with pytest.raises(InvalidInputError):
        validate_governance_reason(reason)


@pytest.mark.case("UT-M5-001")
def test_manage_projects_registers_binds_and_adopts() -> None:
    repository = _Repository()
    authorizer = _Authorizer()
    use_case = ManageProjects(repository, authorizer, _Clock())

    project = use_case.register(
        ProjectId("project-a"),
        name=" Project A ",
        policy_hash=_HASH,
        actor_id="tanaka",
        reason=" initial setup ",
    )
    binding = use_case.bind_repository(
        project.project_id,
        provider="github",
        repository_external_id="owner/repository",
        actor_id="tanaka",
        reason="bind",
    )
    use_case.adopt_existing(
        project.project_id,
        resource_type=GovernanceResourceType.GROUND_TRUTH_SET,
        resource_id=" set-1 ",
        actor_id="tanaka",
        reason="adopt",
    )

    assert project.name == "Project A"
    assert binding.repository_external_id == "owner/repository"
    assert repository.adoptions == [
        (ProjectId("project-a"), GovernanceResourceType.GROUND_TRUTH_SET, "set-1")
    ]
    assert authorizer.calls == [
        ("tanaka", Permission.MANAGE_PROJECT),
        ("tanaka", Permission.MANAGE_PROJECT),
        ("tanaka", Permission.MANAGE_PROJECT),
    ]
    assert use_case.list_projects() == (project,)


@pytest.mark.case("UT-M5-001")
def test_manage_projects_rejects_unknown_archived_and_invalid_resource() -> None:
    repository = _Repository()
    use_case = ManageProjects(repository, _Authorizer(), _Clock())

    with pytest.raises(InvalidInputError, match=re.escape(Messages.PROJECT_NOT_REGISTERED)):
        use_case.bind_repository(
            ProjectId("unknown"),
            provider="github",
            repository_external_id="owner/repository",
            actor_id="tanaka",
            reason="bind",
        )

    repository.projects["project-a"] = _project(status=ProjectStatus.ARCHIVED)
    with pytest.raises(InvalidInputError, match=re.escape(Messages.PROJECT_ARCHIVED)):
        use_case.adopt_existing(
            ProjectId("project-a"),
            resource_type=GovernanceResourceType.GROUND_TRUTH_SET,
            resource_id="set-1",
            actor_id="tanaka",
            reason="adopt",
        )
    repository.projects["project-a"] = _project()
    with pytest.raises(InvalidInputError, match=re.escape(Messages.GOVERNANCE_RESOURCE_ID_INVALID)):
        use_case.adopt_existing(
            ProjectId("project-a"),
            resource_type=GovernanceResourceType.GROUND_TRUTH_SET,
            resource_id="bad id",
            actor_id="tanaka",
            reason="adopt",
        )


@pytest.mark.case("UT-M5-001")
def test_project_context_validator_accepts_matching_active_project() -> None:

    repository = _Repository()
    repository.projects["project-a"] = _project()
    repository.bindings.append(
        RepositoryBinding(ProjectId("project-a"), "github", "owner/repository", _NOW)
    )

    ProjectContextValidator(repository).validate(
        ProjectId("project-a"),
        provider="github",
        repository_external_id="owner/repository",
    )


@pytest.mark.case("UT-M5-001")
@pytest.mark.parametrize("case", ["missing", "archived", "mismatch"])
def test_project_context_validator_hides_invalid_context(case: str) -> None:

    repository = _Repository()
    if case != "missing":
        repository.projects["project-a"] = _project(
            status=ProjectStatus.ARCHIVED if case == "archived" else ProjectStatus.ACTIVE
        )
    if case != "mismatch":
        repository.bindings.append(
            RepositoryBinding(ProjectId("project-a"), "github", "owner/repository", _NOW)
        )

    with pytest.raises(InvalidInputError, match=re.escape(Messages.PROJECT_CONTEXT_NOT_FOUND)):
        ProjectContextValidator(repository).validate(
            ProjectId("project-a"),
            provider="github",
            repository_external_id="owner/repository",
        )


@pytest.mark.case("UT-M5-001")
def test_project_context_validator_rejects_unassigned_legacy_resources() -> None:

    repository = _Repository()
    repository.projects["project-a"] = _project()
    repository.bindings.append(
        RepositoryBinding(ProjectId("project-a"), "github", "owner/repository", _NOW)
    )
    repository.has_unassigned = True

    with pytest.raises(
        InvalidInputError, match=re.escape(Messages.GOVERNANCE_RESOURCES_UNASSIGNED)
    ):
        ProjectContextValidator(repository).validate(
            ProjectId("project-a"),
            provider="github",
            repository_external_id="owner/repository",
        )
