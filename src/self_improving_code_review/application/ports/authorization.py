"""実行者認可ポート.

承認と却下は人間の統制操作である. 非対話式実行でも実行者を特定できない
場合は失敗させ、本番承認を暗黙に有効化しない.
"""

from __future__ import annotations

from typing import Protocol

from self_improving_code_review.domain.common.enums import Permission

__all__ = ["ActorAuthorizer"]


class ActorAuthorizer(Protocol):
    """実行者が操作権限を持つことを検証する."""

    def require(self, actor_id: str, permission: Permission) -> None:
        """``actor_id`` に ``permission`` がない場合は例外を送出する.

        Raises:
            AuthorizationError: 実行者を特定できない、または権限がない場合.
        """
        ...
