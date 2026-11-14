"""実行リースの取得、心拍、喪失検査および解放を調停する."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from threading import Lock
from types import TracebackType

from self_improving_code_review.application.ports.clock import Clock
from self_improving_code_review.application.ports.run_lease import (
    HeartbeatHandle,
    HeartbeatScheduler,
    RunLeaseIdentity,
    RunLeaseRepository,
    RunLeaseSession,
)
from self_improving_code_review.domain.common.errors import ConcurrencyConflictError
from self_improving_code_review.domain.common.values import RunId
from self_improving_code_review.messages import Messages

__all__ = ["ManagedRunLeaseSessionFactory", "NoopRunLeaseSessionFactory"]


class _NoopRunLeaseSession:
    """M5 実行リース無効時に既存動作を維持する空実装."""

    def __enter__(self) -> _NoopRunLeaseSession:
        """同じ空実装を返す."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """終了処理を行わない."""

    def acquire(self, run_id: RunId) -> None:
        """実行 ID を使用せず成功として扱う."""

    def assert_owned(self) -> None:
        """所有権検査を行わない."""

    @property
    def identity(self) -> RunLeaseIdentity | None:
        """リース条件がないことを返す."""
        return None


class NoopRunLeaseSessionFactory:
    """無効時の空の実行リース会話を生成する."""

    def __call__(self) -> RunLeaseSession:
        """状態を持たない空の会話を返す."""
        return _NoopRunLeaseSession()


class _ManagedRunLeaseSession:
    """一回のレビュー中に実行リースと心拍を管理する."""

    def __init__(
        self,
        *,
        repository: RunLeaseRepository,
        clock: Clock,
        scheduler: HeartbeatScheduler,
        owner: str,
        lease_seconds: int,
        heartbeat_seconds: int,
    ) -> None:
        """リース境界と注入済みの時刻・心拍実行処理を保持する."""
        self._repository = repository
        self._clock = clock
        self._scheduler = scheduler
        self._owner = owner
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._identity: RunLeaseIdentity | None = None
        self._heartbeat_handle: HeartbeatHandle | None = None
        self._is_lost = False
        self._lock = Lock()

    def __enter__(self) -> _ManagedRunLeaseSession:
        """同じリース会話を返す."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """心拍を先に停止し、古い所有者条件では削除しない形で解放する."""
        if self._heartbeat_handle is not None:
            self._heartbeat_handle.stop()
        if self._identity is not None:
            self._repository.release(self._identity)

    def acquire(self, run_id: RunId) -> None:
        """未取得または期限切れの実行リースを取得して心拍を開始する."""
        now = self._clock.now_utc()
        identity = self._repository.acquire(
            run_id,
            self._owner,
            now,
            now + timedelta(seconds=self._lease_seconds),
        )
        if identity is None:
            raise ConcurrencyConflictError(Messages.RUN_LEASE_CONFLICT)
        self._identity = identity
        self._heartbeat_handle = self._scheduler.start(
            self._heartbeat_seconds,
            self._heartbeat,
        )

    def assert_owned(self) -> None:
        """心拍喪失または DB 上の所有権変更を安全な競合として送出する."""
        with self._lock:
            is_lost = self._is_lost
            identity = self._identity
        if identity is None or is_lost:
            raise ConcurrencyConflictError(Messages.RUN_LEASE_LOST)
        if not self._repository.is_owned(identity, self._clock.now_utc()):
            with self._lock:
                self._is_lost = True
            raise ConcurrencyConflictError(Messages.RUN_LEASE_LOST)

    @property
    def identity(self) -> RunLeaseIdentity | None:
        """最終更新の囲い込み条件に使う現在のリース識別子を返す."""
        return self._identity

    def _heartbeat(self) -> None:
        """所有権が変わっていない場合だけ期限を延長する."""
        identity = self._identity
        if identity is None:
            return
        now = self._clock.now_utc()
        try:
            renewed = self._repository.heartbeat(
                identity,
                now,
                now + timedelta(seconds=self._lease_seconds),
            )
        except Exception:
            renewed = False
        if not renewed:
            with self._lock:
                self._is_lost = True


class ManagedRunLeaseSessionFactory:
    """設定値から状態を共有しない実行リース会話を作成する."""

    def __init__(
        self,
        *,
        repository: RunLeaseRepository,
        clock: Clock,
        scheduler: HeartbeatScheduler,
        owner_factory: Callable[[], str],
        lease_seconds: int,
        heartbeat_seconds: int,
    ) -> None:
        """会話生成に必要な依存関係と間隔を保持する."""
        self._repository = repository
        self._clock = clock
        self._scheduler = scheduler
        self._owner_factory = owner_factory
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds

    def __call__(self) -> RunLeaseSession:
        """一意な所有者を持つ新しい実行リース会話を返す."""
        return _ManagedRunLeaseSession(
            repository=self._repository,
            clock=self._clock,
            scheduler=self._scheduler,
            owner=self._owner_factory(),
            lease_seconds=self._lease_seconds,
            heartbeat_seconds=self._heartbeat_seconds,
        )
