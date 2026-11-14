"""SCM 提供元へアクセスするためのポート."""

from __future__ import annotations

from typing import Protocol

from self_improving_code_review.application.scm_metrics import ScmApiMetrics
from self_improving_code_review.domain.scm.models import (
    ChangeRef,
    CreateComment,
    EventPage,
    PublishedComment,
    ScmChange,
    UpdateComment,
)

__all__ = ["ScmClient"]


class ScmClient(Protocol):
    """変更要求とコメントを提供元の型に依存せず扱う."""

    def get_change(self, ref: ChangeRef) -> ScmChange:
        """変更要求の固定可能な最新スナップショットを返す."""
        ...

    def get_authenticated_actor(self) -> str:
        """現在の認証主体の外部識別子を返す."""
        ...

    def create_comment(self, ref: ChangeRef, request: CreateComment) -> PublishedComment:
        """変更要求へ新規コメントを作成する."""
        ...

    def find_created_comment(
        self,
        ref: ChangeRef,
        request: CreateComment,
    ) -> PublishedComment | None:
        """作成応答を失った要求と一致する自分のコメントを検索する."""
        ...

    def update_comment(self, ref: ChangeRef, request: UpdateComment) -> PublishedComment:
        """本システムが作成済みのコメント本文を更新する."""
        ...

    def list_comment_events(
        self,
        ref: ChangeRef,
        *,
        cursor: str | None,
    ) -> EventPage:
        """カーソル以後のフィードバック候補イベントを返す."""
        ...

    def get_metrics(self) -> ScmApiMetrics:
        """このクライアント生成後に観測した API 計測値を返す."""
        ...
