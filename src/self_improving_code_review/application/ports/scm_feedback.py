"""SCM フィードバック同期の保存と M2 処理ポート."""

from __future__ import annotations

from typing import Protocol

from self_improving_code_review.application.feedback.models import FeedbackOutcome
from self_improving_code_review.application.scm.models import (
    ParsedFeedbackCommand,
    ScmFeedbackEventRecord,
    ScmFeedbackTarget,
)
from self_improving_code_review.domain.scm.models import ChangeRef

__all__ = ["ScmFeedbackProcessor", "ScmFeedbackRepository"]


class ScmFeedbackProcessor(Protocol):
    """検証済み SCM 入力を M2 保存と候補抽出へ接続する."""

    def process(
        self,
        target: ScmFeedbackTarget,
        command: ParsedFeedbackCommand,
        actor_id: str,
    ) -> FeedbackOutcome:
        """フィードバックを保存し、メモリ候補抽出結果を返す."""
        ...


class ScmFeedbackRepository(Protocol):
    """出版物照合、イベント重複排除およびカーソル保存を行う."""

    def get_cursor(self, ref: ChangeRef) -> str | None:
        """前回確定済みの取得カーソルを返す."""
        ...

    def has_event(self, provider: str, event_external_id: str) -> bool:
        """外部イベントが保存済みの場合は True を返す."""
        ...

    def find_target(
        self,
        ref: ChangeRef,
        external_comment_id: str,
    ) -> ScmFeedbackTarget | None:
        """公開済み行コメントに対応する内部指摘を返す."""
        ...

    def save_event_and_cursor(
        self,
        ref: ChangeRef,
        record: ScmFeedbackEventRecord,
        cursor: str,
    ) -> None:
        """イベント監査とカーソルを同じ短いトランザクションで保存する."""
        ...

    def save_cursor(self, ref: ChangeRef, cursor: str) -> None:
        """監査対象外または重複イベントまで確定したカーソルを保存する."""
        ...
