"""長時間の外部呼出しを短い DB リースで保護するポート."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol

from self_improving_code_review.domain.common.values import RunId

__all__ = [
    "HeartbeatHandle",
    "HeartbeatScheduler",
    "RunLeaseIdentity",
    "RunLeaseRepository",
    "RunLeaseSession",
    "RunLeaseSessionFactory",
]


@dataclass(frozen=True, slots=True)
class RunLeaseIdentity:
    """実行リースの所有者と単調増加する囲い込みトークン."""

    run_id: RunId
    owner: str
    fencing_token: int


class RunLeaseRepository(Protocol):
    """独立した短時間トランザクションで実行リースを更新する."""

    def acquire(
        self,
        run_id: RunId,
        owner: str,
        now: datetime,
        expires_at: datetime,
    ) -> RunLeaseIdentity | None:
        """未取得または期限切れならリースを取得する."""

    def heartbeat(
        self,
        identity: RunLeaseIdentity,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        """所有権が変わっていないリースだけを延長する."""

    def is_owned(self, identity: RunLeaseIdentity, now: datetime) -> bool:
        """指定した所有者と囲い込みトークンが現在も有効か返す."""

    def release(self, identity: RunLeaseIdentity) -> None:
        """指定した所有権と一致するリースだけを解放する."""


class HeartbeatHandle(Protocol):
    """開始済みの定期心拍を停止する契約."""

    def stop(self) -> None:
        """待機を解除して心拍処理の終了を待つ."""


class HeartbeatScheduler(Protocol):
    """注入可能な定期心拍実行処理."""

    def start(self, interval_seconds: int, callback: Callable[[], None]) -> HeartbeatHandle:
        """指定間隔で処理を呼び出し、停止ハンドルを返す."""


class RunLeaseSession(Protocol):
    """一回のレビュー実行が保持するリースセッション."""

    def __enter__(self) -> RunLeaseSession:
        """リースセッションを開始する."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """心拍を停止して保持中のリースを解放する."""

    def acquire(self, run_id: RunId) -> None:
        """対象実行のリースを取得する."""

    def assert_owned(self) -> None:
        """リース喪失時に並行実行競合を送出する."""

    @property
    def identity(self) -> RunLeaseIdentity | None:
        """有効化時の最終更新条件に使うリース識別子を返す."""


class RunLeaseSessionFactory(Protocol):
    """レビューごとに状態を共有しないリースセッションを生成する."""

    def __call__(self) -> RunLeaseSession:
        """新しいリースセッションを返す."""
