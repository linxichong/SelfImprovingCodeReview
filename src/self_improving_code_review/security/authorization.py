"""実行者認可の実装.

TBD-06 が未解決のため、明示的に列挙したローカル開発環境の保守担当者だけを
許可する. 一覧が空の場合は統制操作を一切許可しない.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from self_improving_code_review.domain.common.enums import Permission
from self_improving_code_review.domain.common.errors import AuthorizationError
from self_improving_code_review.messages import Messages

__all__ = ["AllowListActorAuthorizer"]

_SELF_SERVICE_PERMISSIONS: frozenset[Permission] = frozenset({Permission.RECORD_FEEDBACK})


@dataclass(frozen=True, slots=True)
class AllowListActorAuthorizer:
    """明示的な一覧に基づいて実行者権限を検証する.

    Attributes:
        maintainers: 承認と発行を実行できる保守担当者の識別子.
        is_production: 本番環境として扱う場合は True. TBD-06 未解決のため、
            本番では統制操作を許可しない.
    """

    maintainers: tuple[str, ...] = field(default_factory=tuple)
    is_production: bool = False

    def require(self, actor_id: str, permission: Permission) -> None:
        """``actor_id`` に ``permission`` がない場合は例外を送出する.

        Raises:
            AuthorizationError: 実行者を特定できない、本番で統制操作を要求した、
                または保守担当者一覧に含まれない場合.
        """
        normalized = actor_id.strip()
        if not normalized:
            message = Messages.ACTOR_ID_REQUIRED
            raise AuthorizationError(message)
        if permission in _SELF_SERVICE_PERMISSIONS:
            return
        if self.is_production:
            message = f"{permission.value} is not enabled in production until TBD-06 is resolved"
            raise AuthorizationError(message)
        if normalized not in self.maintainers:
            message = f"{Messages.ACTOR_NOT_MAINTAINER}: {permission.value}"
            raise AuthorizationError(message)
