from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from self_improving_code_review.domain.common.errors import DatabaseError, InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.domain.governance.models import (
    GovernanceResourceType,
    ProjectRecord,
    ProjectStatus,
    RepositoryBinding,
)
from self_improving_code_review.infrastructure.persistence.governance_repository import (
    SqliteGovernanceRepository,
)
from self_improving_code_review.messages import Messages

_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
_HASH = "a" * 64


def _project(project_id: str, name: str | None = None) -> ProjectRecord:
    return ProjectRecord(
        project_id=ProjectId(project_id),
        name=name or project_id,
        policy_hash=_HASH,
        status=ProjectStatus.ACTIVE,
        lock_version=1,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _repository(tmp_path: Path) -> tuple[Path, SqliteGovernanceRepository]:
    database = tmp_path / "review.db"
    return database, SqliteGovernanceRepository(database)


def test_project_registration_is_idempotent_and_audited(tmp_path: Path) -> None:
    database, repository = _repository(tmp_path)
    project = _project("project-a", "Project A")

    first = repository.register_project(
        project,
        actor_id="tanaka",
        reason_hash=hash_text("initial setup"),
    )
    second = repository.register_project(
        project,
        actor_id="tanaka",
        reason_hash=hash_text("initial setup"),
    )

    assert first == second
    assert repository.find_project(ProjectId("project-a")) == project
    assert repository.list_projects() == (project,)
    with closing(sqlite3.connect(database)) as connection:
        audit = connection.execute(
            "SELECT operation, target_id_hash, actor_id, reason_hash FROM governance_audit"
        ).fetchall()
    assert audit == [
        (
            "register-project",
            hash_text("project-a"),
            "tanaka",
            hash_text("initial setup"),
        )
    ]


def test_conflicting_project_registration_is_rejected(tmp_path: Path) -> None:
    _database, repository = _repository(tmp_path)
    repository.register_project(
        _project("project-a", "Project A"),
        actor_id="tanaka",
        reason_hash=_HASH,
    )

    with pytest.raises(InvalidInputError, match=re.escape(Messages.PROJECT_ALREADY_REGISTERED)):
        repository.register_project(
            _project("project-a", "Different"),
            actor_id="tanaka",
            reason_hash=_HASH,
        )


def test_repository_binding_is_unique_across_projects(tmp_path: Path) -> None:
    _database, repository = _repository(tmp_path)
    for project_id in ("project-a", "project-b"):
        repository.register_project(
            _project(project_id),
            actor_id="tanaka",
            reason_hash=_HASH,
        )
    binding = RepositoryBinding(
        ProjectId("project-a"),
        "github",
        "owner/repository",
        _NOW,
    )

    assert repository.bind_repository(binding, actor_id="tanaka", reason_hash=_HASH) == binding
    assert repository.bind_repository(binding, actor_id="tanaka", reason_hash=_HASH) == binding
    assert repository.find_repository_project("github", "owner/repository") == ProjectId(
        "project-a"
    )
    assert repository.find_repository_project("github", "missing") is None
    assert not repository.has_unassigned_resources()

    with pytest.raises(InvalidInputError, match=re.escape(Messages.REPOSITORY_ALREADY_BOUND)):
        repository.bind_repository(
            RepositoryBinding(
                ProjectId("project-b"),
                "github",
                "owner/repository",
                _NOW,
            ),
            actor_id="tanaka",
            reason_hash=_HASH,
        )


def test_existing_ground_truth_and_scm_resources_are_adopted_one_by_one(
    tmp_path: Path,
) -> None:
    database, repository = _repository(tmp_path)
    for project_id in ("project-a", "project-b"):
        repository.register_project(
            _project(project_id),
            actor_id="tanaka",
            reason_hash=_HASH,
        )
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO ground_truth_sets("
            "set_id, split, version, set_hash, status, approved_by, published_at"
            ") VALUES('set-1', 'test', 1, ?, 'published', 'tanaka', ?)",
            (_HASH, _NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO scm_changes("
            "provider, repository_external_id, change_external_id, base_sha, head_sha, "
            "state, is_draft, source_repository_external_id, updated_at, observed_at"
            ") VALUES('github', 'owner/repository', '15', 'base', 'head', "
            "'open', 0, 'owner/repository', ?, ?)",
            (_NOW.isoformat(), _NOW.isoformat()),
        )
        connection.commit()

    assert repository.has_unassigned_resources()

    repository.adopt_existing(
        ProjectId("project-a"),
        GovernanceResourceType.GROUND_TRUTH_SET,
        "set-1",
        actor_id="tanaka",
        reason_hash=_HASH,
        occurred_at=_NOW,
    )
    repository.adopt_existing(
        ProjectId("project-a"),
        GovernanceResourceType.SCM_CHANGE,
        "github|owner/repository|15",
        actor_id="tanaka",
        reason_hash=_HASH,
        occurred_at=_NOW,
    )
    repository.adopt_existing(
        ProjectId("project-a"),
        GovernanceResourceType.GROUND_TRUTH_SET,
        "set-1",
        actor_id="tanaka",
        reason_hash=_HASH,
        occurred_at=_NOW,
    )
    repository.adopt_existing(
        ProjectId("project-a"),
        GovernanceResourceType.SCM_CHANGE,
        "github|owner/repository|15",
        actor_id="tanaka",
        reason_hash=_HASH,
        occurred_at=_NOW,
    )
    assert not repository.has_unassigned_resources()

    with closing(sqlite3.connect(database)) as connection:
        ground_truth = connection.execute(
            "SELECT project_id, set_id FROM project_ground_truth_sets"
        ).fetchall()
        scm = connection.execute(
            "SELECT project_id, provider, repository_external_id, change_external_id "
            "FROM project_scm_changes"
        ).fetchall()
        adoption_audits = connection.execute(
            "SELECT COUNT(*) FROM governance_audit WHERE operation = 'adopt-existing'"
        ).fetchone()
    assert ground_truth == [("project-a", "set-1")]
    assert scm == [("project-a", "github", "owner/repository", "15")]
    assert adoption_audits == (2,)

    with pytest.raises(
        InvalidInputError, match=re.escape(Messages.GOVERNANCE_RESOURCE_OTHER_PROJECT)
    ):
        repository.adopt_existing(
            ProjectId("project-b"),
            GovernanceResourceType.GROUND_TRUTH_SET,
            "set-1",
            actor_id="tanaka",
            reason_hash=_HASH,
            occurred_at=_NOW,
        )
    with pytest.raises(
        InvalidInputError, match=re.escape(Messages.GOVERNANCE_RESOURCE_OTHER_PROJECT)
    ):
        repository.adopt_existing(
            ProjectId("project-b"),
            GovernanceResourceType.SCM_CHANGE,
            "github|owner/repository|15",
            actor_id="tanaka",
            reason_hash=_HASH,
            occurred_at=_NOW,
        )


@pytest.mark.parametrize(
    ("resource_type", "resource_id", "message"),
    [
        (
            GovernanceResourceType.GROUND_TRUTH_SET,
            "missing",
            Messages.GOVERNANCE_RESOURCE_NOT_FOUND,
        ),
        (GovernanceResourceType.SCM_CHANGE, "invalid", Messages.SCM_RESOURCE_ID_INVALID),
        (
            GovernanceResourceType.SCM_CHANGE,
            "github|owner/repository|999",
            Messages.GOVERNANCE_RESOURCE_NOT_FOUND,
        ),
    ],
)
def test_adoption_rejects_invalid_or_missing_resource(
    tmp_path: Path,
    resource_type: GovernanceResourceType,
    resource_id: str,
    message: str,
) -> None:
    _database, repository = _repository(tmp_path)
    repository.register_project(
        _project("project-a"),
        actor_id="tanaka",
        reason_hash=_HASH,
    )

    with pytest.raises(InvalidInputError, match=re.escape(message)):
        repository.adopt_existing(
            ProjectId("project-a"),
            resource_type,
            resource_id,
            actor_id="tanaka",
            reason_hash=_HASH,
            occurred_at=_NOW,
        )


@pytest.mark.parametrize(
    "operation",
    [
        "register",
        "list",
        "find",
        "find-repository",
        "unassigned",
        "bind",
        "adopt",
    ],
)
def test_repository_wraps_sqlite_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    _database, repository = _repository(tmp_path)

    def fail_connection() -> sqlite3.Connection:
        """SQLite 接続障害を再現する."""
        raise sqlite3.OperationalError("simulated")

    monkeypatch.setattr(repository, "_connection", fail_connection)

    def execute_operation() -> None:
        """対象のリポジトリ操作を一回実行する."""
        if operation == "register":
            repository.register_project(_project("project-a"), actor_id="tanaka", reason_hash=_HASH)
        elif operation == "list":
            repository.list_projects()
        elif operation == "find":
            repository.find_project(ProjectId("project-a"))
        elif operation == "find-repository":
            repository.find_repository_project("github", "owner/repository")
        elif operation == "unassigned":
            repository.has_unassigned_resources()
        elif operation == "bind":
            repository.bind_repository(
                RepositoryBinding(ProjectId("project-a"), "github", "owner/repository", _NOW),
                actor_id="tanaka",
                reason_hash=_HASH,
            )
        else:
            repository.adopt_existing(
                ProjectId("project-a"),
                GovernanceResourceType.GROUND_TRUTH_SET,
                "set-1",
                actor_id="tanaka",
                reason_hash=_HASH,
                occurred_at=_NOW,
            )

    with pytest.raises(DatabaseError):
        execute_operation()
