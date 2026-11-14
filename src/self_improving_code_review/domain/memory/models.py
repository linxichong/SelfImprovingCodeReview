"""メモリのドメインモデル.

内容は ``(memory_id, version)`` で不変化する. 承認や却下だけでは
内容バージョンを増やさず、内容変更は必ず新しいバージョンを作る.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from packaging.requirements import InvalidRequirement, Requirement

from self_improving_code_review.domain.common.enums import (
    MemoryCategory,
    MemoryPolarity,
    MemoryStatus,
)
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_lines, hash_text
from self_improving_code_review.domain.common.values import (
    MemoryId,
    MemoryVersion,
    ProjectId,
)
from self_improving_code_review.domain.memory.scope import MemoryScope
from self_improving_code_review.messages import Messages

__all__ = [
    "MemoryContent",
    "MemoryRecord",
    "MemorySnapshot",
    "MemorySnapshotItem",
    "MemoryTransition",
    "compute_memory_snapshot_hash",
]

_MAX_FACT_LENGTH = 500
_MAX_INSTRUCTION_LENGTH = 500
_MAX_EVIDENCE_SUMMARY_LENGTH = 500


@dataclass(frozen=True, slots=True)
class MemoryContent:
    """不変のメモリ内容バージョン.

    ``content_hash`` は構造化内容を安定順 JSON へ正規化した SHA-256 とし、
    保存前と読込時の双方で再計算して一致を確認する.
    """

    memory_id: MemoryId
    version: MemoryVersion
    category: MemoryCategory
    scope: MemoryScope
    fact: str
    review_instruction: str
    polarity: MemoryPolarity
    confidence: float
    evidence_summary: str
    created_by: str
    created_at: datetime
    valid_from: datetime | None = None
    review_after: datetime | None = None
    framework_version_constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """空の内容、範囲外の確信度、未加工の時刻を拒否する.

        Raises:
            InvalidInputError: 必須テキストが空か長すぎる、確信度が
                0.0 から 1.0 の範囲外、または作成時刻が UTC ではない場合.
        """
        self._require_text("fact", self.fact, _MAX_FACT_LENGTH)
        self._require_text("review_instruction", self.review_instruction, _MAX_INSTRUCTION_LENGTH)
        self._require_text(
            "evidence_summary",
            self.evidence_summary,
            _MAX_EVIDENCE_SUMMARY_LENGTH,
        )
        if not 0.0 <= self.confidence <= 1.0:
            message = Messages.MEMORY_CONFIDENCE_INVALID
            raise InvalidInputError(message)
        if not self.created_by.strip():
            message = Messages.MEMORY_CREATOR_REQUIRED
            raise InvalidInputError(message)
        if (self.valid_from is None) != (self.review_after is None):
            message = Messages.MEMORY_VALIDITY_PAIR_REQUIRED
            raise InvalidInputError(message)
        if self.valid_from is not None and self.review_after is not None:
            if self.valid_from.tzinfo is None or self.review_after.tzinfo is None:
                message = Messages.MEMORY_VALIDITY_NOT_UTC
                raise InvalidInputError(message)
            if self.valid_from >= self.review_after:
                message = Messages.MEMORY_VALIDITY_ORDER_INVALID
                raise InvalidInputError(message)
        for item in self.framework_version_constraints:
            try:
                requirement = Requirement(item)
            except InvalidRequirement as exc:
                message = Messages.FRAMEWORK_CONSTRAINT_INVALID
                raise InvalidInputError(message) from exc
            if (
                len(item) > 200
                or requirement.extras
                or requirement.marker is not None
                or requirement.url is not None
                or not requirement.specifier
            ):
                message = Messages.FRAMEWORK_CONSTRAINT_INVALID
                raise InvalidInputError(message)
        if self.created_at.tzinfo is None:
            message = Messages.MEMORY_CREATED_AT_NOT_UTC
            raise InvalidInputError(message)

    @property
    def content_hash(self) -> str:
        """構造化内容の安定した SHA-256 ダイジェストを返す."""
        return hash_text(
            json.dumps(
                self.to_document(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
        )

    def to_document(self) -> dict[str, object]:
        """ハッシュ計算と永続化に使用する正規化済み文書を返す."""
        document: dict[str, object] = {
            "category": self.category.value,
            "scope": {
                "languages": list(self.scope.languages),
                "frameworks": list(self.scope.frameworks),
                "paths": list(self.scope.paths),
                "symbols": list(self.scope.symbols),
                "keywords": list(self.scope.keywords),
                "risk_tags": list(self.scope.risk_tags),
            },
            "fact": self.fact,
            "review_instruction": self.review_instruction,
            "polarity": self.polarity.value,
            "confidence": round(self.confidence, 4),
            "evidence_summary": self.evidence_summary,
        }

        if self.framework_version_constraints:
            document["framework_version_constraints"] = list(self.framework_version_constraints)
        return document

    def _require_text(self, field_name: str, value: str, maximum: int) -> None:
        """空でなく上限内のテキストであることを確認する.

        Raises:
            InvalidInputError: テキストが空、または上限を超える場合.
        """
        normalized = value.strip()
        if not normalized:
            message = f"{Messages.MEMORY_FIELD_EMPTY}: {field_name}"
            raise InvalidInputError(message)
        if len(normalized) > maximum:
            message = f"{Messages.MEMORY_FIELD_TOO_LONG}: {field_name}: 最大 {maximum} 文字"
            raise InvalidInputError(message)


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """論理メモリの現在状態.

    ``current_version`` が現在の内容を指し、``lock_version`` は
    同じバージョンからの二重更新を防ぐ楽観ロックに使用する.
    """

    memory_id: MemoryId
    project_id: ProjectId
    current_version: MemoryVersion
    current_status: MemoryStatus
    lock_version: int
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None = None

    def __post_init__(self) -> None:
        """正でないロックバージョンと未加工の時刻を拒否する.

        Raises:
            InvalidInputError: ロックバージョンが 1 未満、または
                時刻が UTC ではない場合.
        """
        if self.lock_version < 1:
            message = Messages.MEMORY_LOCK_VERSION_INVALID
            raise InvalidInputError(message)
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            message = Messages.MEMORY_TIME_NOT_UTC
            raise InvalidInputError(message)

    @property
    def is_retrievable(self) -> bool:
        """検索でレビューへ注入できる状態の場合は True を返す."""
        return self.current_status.is_retrievable


@dataclass(frozen=True, slots=True)
class MemoryTransition:
    """メモリ状態変更の監査記録."""

    memory_id: MemoryId
    memory_version: MemoryVersion
    from_status: MemoryStatus | None
    to_status: MemoryStatus
    actor_id: str
    reason: str
    created_at: datetime

    def __post_init__(self) -> None:
        """実行者、理由、時刻の欠落を拒否する.

        Raises:
            InvalidInputError: 実行者か理由が空、または時刻が UTC ではない場合.
        """
        if not self.actor_id.strip():
            message = Messages.MEMORY_TRANSITION_ACTOR_REQUIRED
            raise InvalidInputError(message)
        if not self.reason.strip():
            message = Messages.MEMORY_TRANSITION_REASON_REQUIRED
            raise InvalidInputError(message)
        if self.created_at.tzinfo is None:
            message = Messages.MEMORY_TRANSITION_TIME_NOT_UTC
            raise InvalidInputError(message)


@dataclass(frozen=True, slots=True)
class MemorySnapshotItem:
    """スナップショットが固定した 1 件のメモリバージョン参照."""

    memory_id: MemoryId
    version: MemoryVersion
    content_hash: str


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    """実行で利用可能として固定したメモリ集合.

    スナップショットは作成時点のバージョンと内容ハッシュを固定参照するため、
    その後の編集や状態変更の影響を受けない.
    """

    snapshot_id: str
    project_id: ProjectId
    snapshot_hash: str
    created_at: datetime
    items: tuple[MemorySnapshotItem, ...]

    @property
    def is_empty(self) -> bool:
        """固定したメモリが 0 件の場合は True を返す."""
        return not self.items


def compute_memory_snapshot_hash(items: tuple[MemorySnapshotItem, ...]) -> str:
    """``items`` のメモリスナップショットハッシュを返す.

    メモリ識別子の昇順で連結するため、検索順序が変わっても
    同じ集合からは同じダイジェストを生成する.
    """
    ordered = sorted(items, key=lambda item: (item.memory_id.value, item.version.value))
    return hash_lines(f"{item.memory_id}:{item.version}:{item.content_hash}" for item in ordered)
