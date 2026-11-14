from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from threading import Event

import pytest

from self_improving_code_review.application.governance.run_lease import (
    ManagedRunLeaseSessionFactory,
    NoopRunLeaseSessionFactory,
)
from self_improving_code_review.application.ports.run_lease import RunLeaseIdentity
from self_improving_code_review.domain.common.errors import ConcurrencyConflictError
from self_improving_code_review.domain.common.values import RunId
from self_improving_code_review.infrastructure.system import (
    ThreadHeartbeatScheduler,
    Uuid4IdGenerator,
)

RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")
NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)


class FixedClock:
    def now_utc(self) -> datetime:
        return NOW


class RecordingHandle:
    def __init__(self) -> None:
        self.is_stopped = False

    def stop(self) -> None:
        self.is_stopped = True


class RecordingScheduler:
    def __init__(self) -> None:
        self.callback: Callable[[], None] | None = None
        self.interval_seconds = 0
        self.handle = RecordingHandle()

    def start(self, interval_seconds: int, callback: Callable[[], None]) -> RecordingHandle:
        self.interval_seconds = interval_seconds
        self.callback = callback
        return self.handle


class RecordingRepository:
    def __init__(self) -> None:
        self.acquired: RunLeaseIdentity | None = RunLeaseIdentity(RUN_ID, "owner-1", 1)
        self.is_owned_result = True
        self.heartbeat_result = True
        self.heartbeat_error = False
        self.released: list[RunLeaseIdentity] = []

    def acquire(
        self,
        run_id: RunId,
        owner: str,
        _now: datetime,
        _expires_at: datetime,
    ) -> RunLeaseIdentity | None:
        if self.acquired is None:
            return None
        return RunLeaseIdentity(run_id, owner, self.acquired.fencing_token)

    def heartbeat(
        self,
        _identity: RunLeaseIdentity,
        _now: datetime,
        _expires_at: datetime,
    ) -> bool:
        if self.heartbeat_error:
            raise RuntimeError("heartbeat")
        return self.heartbeat_result

    def is_owned(self, _identity: RunLeaseIdentity, _now: datetime) -> bool:
        return self.is_owned_result

    def release(self, identity: RunLeaseIdentity) -> None:
        self.released.append(identity)


def _factory(
    repository: RecordingRepository,
    scheduler: RecordingScheduler,
) -> ManagedRunLeaseSessionFactory:
    return ManagedRunLeaseSessionFactory(
        repository=repository,
        clock=FixedClock(),
        scheduler=scheduler,
        owner_factory=lambda: "owner-1",
        lease_seconds=90,
        heartbeat_seconds=30,
    )


@pytest.mark.case("UT-M5-008")
def test_noop_session_preserves_disabled_behavior() -> None:
    with NoopRunLeaseSessionFactory()() as session:
        session.acquire(RUN_ID)
        session.assert_owned()
        assert session.identity is None


@pytest.mark.case("UT-M5-008")
def test_managed_session_acquires_renews_checks_and_releases() -> None:
    repository = RecordingRepository()
    scheduler = RecordingScheduler()

    with _factory(repository, scheduler)() as session:
        session.acquire(RUN_ID)
        assert scheduler.interval_seconds == 30
        assert scheduler.callback is not None
        scheduler.callback()
        session.assert_owned()
        identity = session.identity

    assert identity == RunLeaseIdentity(RUN_ID, "owner-1", 1)
    assert scheduler.handle.is_stopped is True
    assert repository.released == [identity]


@pytest.mark.case("UT-M5-008")
def test_active_lease_rejects_second_execution() -> None:
    repository = RecordingRepository()
    repository.acquired = None

    with (
        _factory(repository, RecordingScheduler())() as session,
        pytest.raises(ConcurrencyConflictError),
    ):
        session.acquire(RUN_ID)


@pytest.mark.case("UT-M5-008")
def test_assert_requires_acquisition_and_detects_repository_loss() -> None:
    repository = RecordingRepository()
    scheduler = RecordingScheduler()
    with _factory(repository, scheduler)() as session:
        with pytest.raises(ConcurrencyConflictError):
            session.assert_owned()
        session.acquire(RUN_ID)
        repository.is_owned_result = False
        with pytest.raises(ConcurrencyConflictError):
            session.assert_owned()
        with pytest.raises(ConcurrencyConflictError):
            session.assert_owned()


@pytest.mark.case("UT-M5-008")
@pytest.mark.parametrize("raises", [False, True])
def test_failed_heartbeat_marks_session_lost(raises: bool) -> None:
    repository = RecordingRepository()
    repository.heartbeat_result = False
    repository.heartbeat_error = raises
    scheduler = RecordingScheduler()

    with _factory(repository, scheduler)() as session:
        session.acquire(RUN_ID)
        assert scheduler.callback is not None
        scheduler.callback()
        with pytest.raises(ConcurrencyConflictError):
            session.assert_owned()


@pytest.mark.case("UT-M5-008")
def test_heartbeat_before_acquisition_is_a_no_operation() -> None:
    session = _factory(RecordingRepository(), RecordingScheduler())()

    session._heartbeat()


@pytest.mark.case("UT-M5-008")
def test_thread_scheduler_runs_callback_and_stops() -> None:
    called = Event()
    handle = ThreadHeartbeatScheduler().start(0, called.set)

    assert called.wait(timeout=1)
    handle.stop()


@pytest.mark.case("UT-M5-008")
def test_uuid_generator_creates_distinct_lease_owners() -> None:
    generator = Uuid4IdGenerator()

    assert generator.new_lease_owner_id() != generator.new_lease_owner_id()
