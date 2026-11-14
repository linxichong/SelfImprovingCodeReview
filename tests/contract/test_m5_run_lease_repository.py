from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from self_improving_code_review.application.review.models import RunRecord, RunVersions
from self_improving_code_review.domain.common.enums import ReviewMode, RunStatus
from self_improving_code_review.domain.common.errors import DatabaseError
from self_improving_code_review.domain.common.values import ProjectId, RunId
from self_improving_code_review.domain.rule.loader import RuleLoader
from self_improving_code_review.infrastructure.persistence.run_lease_repository import (
    SqliteRunLeaseRepository,
)
from self_improving_code_review.infrastructure.persistence.sqlite import SqliteUnitOfWorkFactory
from self_improving_code_review.messages import Messages

RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")
NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
LOCKED_ERROR = "database is locked"


@pytest.mark.case("UT-M5-008")
def test_lease_lifecycle_uses_monotonic_fencing_and_rejects_stale_owner(
    tmp_path: Path,
) -> None:
    database = tmp_path / "review.db"
    _create_run(database)
    repository = SqliteRunLeaseRepository(database)

    first = repository.acquire(RUN_ID, "owner-1", NOW, NOW + timedelta(seconds=90))
    assert first is not None
    assert first.fencing_token == 1
    assert repository.acquire(RUN_ID, "owner-2", NOW, NOW + timedelta(seconds=90)) is None
    assert repository.is_owned(first, NOW)
    assert repository.heartbeat(first, NOW, NOW + timedelta(seconds=120))

    takeover_at = NOW + timedelta(seconds=121)
    second = repository.acquire(
        RUN_ID,
        "owner-2",
        takeover_at,
        takeover_at + timedelta(seconds=90),
    )
    assert second is not None
    assert second.fencing_token == 2
    assert repository.heartbeat(first, takeover_at, takeover_at + timedelta(seconds=90)) is False
    assert repository.is_owned(first, takeover_at) is False

    repository.release(first)
    assert repository.is_owned(second, takeover_at)
    repository.release(second)
    assert repository.is_owned(second, takeover_at) is False


def test_locked_database_is_retried_with_bounded_backoff(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    repository = SqliteRunLeaseRepository(
        database,
        max_locked_retries=1,
        sleeper=lambda seconds: waits.append(seconds),
    )
    waits: list[float] = []
    calls = 0

    def operation(_connection: sqlite3.Connection) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError(LOCKED_ERROR)
        return "ok"

    assert repository._execute(operation) == "ok"
    assert waits == [0.01]


@pytest.mark.parametrize(
    ("error", "max_retries"),
    [
        (sqlite3.OperationalError("not a database"), 2),
        (sqlite3.OperationalError("database is locked"), 0),
        (sqlite3.IntegrityError("constraint"), 2),
    ],
)
def test_non_retryable_or_exhausted_database_errors_are_translated(
    tmp_path: Path,
    error: sqlite3.Error,
    max_retries: int,
) -> None:
    repository = SqliteRunLeaseRepository(
        tmp_path / "review.db",
        max_locked_retries=max_retries,
    )

    def operation(_connection: sqlite3.Connection) -> None:
        raise error

    with pytest.raises(DatabaseError, match=re.escape(Messages.RUN_LEASE_UPDATE_FAILED)):
        repository._execute(operation)


def test_negative_retry_limit_reaches_defensive_failure(tmp_path: Path) -> None:
    repository = SqliteRunLeaseRepository(
        tmp_path / "review.db",
        max_locked_retries=-1,
    )

    with pytest.raises(AssertionError, match="retry loop"):
        repository._execute(lambda _connection: None)


def _create_run(database: Path) -> None:
    snapshot = RuleLoader().load(Path("missing-rules"), git_sha="a" * 40)
    run = RunRecord(
        run_id=RUN_ID,
        project_id=ProjectId("test-project"),
        mode=ReviewMode.RULES,
        status=RunStatus.RUNNING,
        source_revision="a" * 40,
        model="dataset/test",
        versions=RunVersions(
            package_version="0.1.0",
            config_hash="config-hash",
            prompt_version="review_general_v1@1",
            prompt_hash="prompt-hash",
            rule_snapshot_id=snapshot.snapshot_id,
            rule_set_hash=snapshot.rule_set_hash,
            is_rule_set_empty=True,
            secret_engine_version="1.5.0",
        ),
        idempotency_key="idempotency-key",
        started_at=NOW,
    )
    with SqliteUnitOfWorkFactory(database)() as unit_of_work:
        unit_of_work.rule_snapshots.save(snapshot)
        unit_of_work.runs.create(run)
