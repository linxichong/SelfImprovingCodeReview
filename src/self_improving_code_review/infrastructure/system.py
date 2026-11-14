"""システム時刻および識別子のアダプター."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from threading import Event, Thread

from self_improving_code_review.application.ports.run_lease import HeartbeatHandle
from self_improving_code_review.domain.common.values import (
    EvaluationId,
    ExtractionId,
    FeedbackId,
    MemoryId,
    RunId,
)

__all__ = ["SystemClock", "ThreadHeartbeatScheduler", "Uuid4IdGenerator"]


class SystemClock:
    """オペレーティングシステムの時計から時刻を返す."""

    def now_utc(self) -> datetime:
        """タイムゾーン情報を持つ現在の UTC 時刻を返す."""
        return datetime.now(UTC)


class Uuid4IdGenerator:
    """小文字の UUIDv4 識別子を生成する."""

    def new_run_id(self) -> RunId:
        """新しい実行識別子を返す."""
        return RunId(self._new_uuid())

    def new_feedback_id(self) -> FeedbackId:
        """新しいフィードバック識別子を返す."""
        return FeedbackId(self._new_uuid())

    def new_extraction_id(self) -> ExtractionId:
        """新しい抽出実行識別子を返す."""
        return ExtractionId(self._new_uuid())

    def new_memory_id(self) -> MemoryId:
        """新しいメモリ識別子を返す."""
        return MemoryId(self._new_uuid())

    def new_evaluation_id(self) -> EvaluationId:
        """新しい評価実行識別子を返す."""
        return EvaluationId(self._new_uuid())

    def new_lease_owner_id(self) -> str:
        """実行リース所有者に使う新しい UUIDv4 文字列を返す."""
        return self._new_uuid()

    def _new_uuid(self) -> str:
        """小文字の UUIDv4 文字列を返す."""
        return str(uuid.uuid4())


class _ThreadHeartbeatHandle:
    """停止可能な単一バックグラウンド心拍を保持する."""

    def __init__(self, interval_seconds: int, callback: Callable[[], None]) -> None:
        """停止通知を待つデーモンスレッドを開始する."""
        self._stop_event = Event()
        self._thread = Thread(
            target=self._run,
            args=(interval_seconds, callback),
            name="review-run-lease-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """次回待機を解除し、進行中の心拍処理が終わるまで待つ."""
        self._stop_event.set()
        self._thread.join()

    def _run(self, interval_seconds: int, callback: Callable[[], None]) -> None:
        """停止通知まで指定間隔で心拍処理を呼び出す."""
        while not self._stop_event.wait(interval_seconds):
            callback()


class ThreadHeartbeatScheduler:
    """標準スレッドで上限付き間隔の心拍処理を実行する."""

    def start(self, interval_seconds: int, callback: Callable[[], None]) -> HeartbeatHandle:
        """バックグラウンド心拍を開始して停止ハンドルを返す."""
        return _ThreadHeartbeatHandle(interval_seconds, callback)
