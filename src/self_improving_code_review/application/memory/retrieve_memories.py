"""メモリ検索とスナップショット固定.

検索対象は ``ACTIVE`` かつ現在バージョンだけとする. 未加工の
フィードバックを検索へ直接渡さず、抽象化済みメモリのみを扱う.
"""

from __future__ import annotations

from dataclasses import dataclass

from self_improving_code_review.application.ports.clock import Clock
from self_improving_code_review.application.ports.memory_persistence import (
    MemoryRepository,
    MemorySnapshotRepository,
)
from self_improving_code_review.domain.change.models import ChangedFile
from self_improving_code_review.domain.common.enums import MemoryStatus, RiskLevel
from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.domain.memory.models import (
    MemoryContent,
    MemorySnapshot,
    MemorySnapshotItem,
    compute_memory_snapshot_hash,
)
from self_improving_code_review.domain.memory.retrieval import (
    DEFAULT_MAX_MEMORIES,
    DEFAULT_MEMORY_TOKENS,
    DEFAULT_PER_MEMORY_TOKENS,
    ScoredMemory,
    estimate_memory_tokens,
    score_memory,
    select_memories,
)

__all__ = ["MemoryRetriever", "MemorySnapshotService", "RetrievedMemories"]


@dataclass(frozen=True, slots=True)
class RetrievedMemories:
    """レビュー単位へ注入するメモリと切詰め件数."""

    contents: tuple[MemoryContent, ...]
    truncated_count: int
    total_tokens: int


class MemoryRetriever:
    """有効メモリからレビュー単位に関連するものを選ぶ."""

    def __init__(
        self,
        memory_repository: MemoryRepository,
        *,
        max_memories: int = DEFAULT_MAX_MEMORIES,
        memory_token_budget: int = DEFAULT_MEMORY_TOKENS,
        per_memory_token_budget: int = DEFAULT_PER_MEMORY_TOKENS,
        clock: Clock | None = None,
    ) -> None:
        """リポジトリと注入予算を保持する.

        Args:
            memory_repository: メモリ読み込み処理.
            max_memories: 注入する最大件数.
            memory_token_budget: メモリ全体のトークン上限.
            per_memory_token_budget: 1 件あたりのトークン上限.
            clock: 期限とフレームワーク条件を M5 で検証する時刻取得処理.
        """
        self._repository = memory_repository
        self._max_memories = max_memories
        self._memory_token_budget = memory_token_budget
        self._per_memory_token_budget = per_memory_token_budget
        self._clock = clock

    def retrieve(
        self,
        project_id: ProjectId,
        files: tuple[ChangedFile, ...],
        *,
        risk: RiskLevel,
        perspective: str,
    ) -> RetrievedMemories:
        """``files`` に関連する有効メモリを予算内で返す.

        Args:
            project_id: 検索対象のプロジェクト.
            files: レビュー単位の変更ファイル.
            risk: レビュー単位のリスク水準.
            perspective: 計画処理が決めたレビュー観点.

        Returns:
            注入するメモリ、切詰め件数、合計トークン数.
        """
        active = self._repository.list_by_status(project_id, MemoryStatus.ACTIVE)
        if self._clock is not None:
            now = self._clock.now_utc()
            active = tuple(
                item
                for item in active
                if (item[1].review_after is None or item[1].review_after > now)
                and not item[1].framework_version_constraints
            )
        scored = tuple(
            ScoredMemory(
                content=content,
                score=score_memory(content, files, risk=risk, perspective=perspective),
                estimated_tokens=estimate_memory_tokens(content),
                confirmed_at=record.confirmed_at,
            )
            for record, content in active
        )
        selection = select_memories(
            scored,
            max_memories=self._max_memories,
            memory_token_budget=self._memory_token_budget,
            per_memory_token_budget=self._per_memory_token_budget,
        )
        return RetrievedMemories(
            contents=tuple(item.content for item in selection.selected),
            truncated_count=selection.truncated_count,
            total_tokens=selection.total_tokens,
        )


class MemorySnapshotService:
    """実行で利用可能なメモリ集合を固定する."""

    def __init__(
        self,
        snapshot_repository: MemorySnapshotRepository,
        *,
        clock: Clock,
    ) -> None:
        """スナップショット永続化処理と時刻取得処理を保持する."""
        self._repository = snapshot_repository
        self._clock = clock

    def create(
        self,
        project_id: ProjectId,
        contents: tuple[MemoryContent, ...],
    ) -> MemorySnapshot:
        """``contents`` のバージョンと内容ハッシュを固定する.

        Args:
            project_id: スナップショットが属するプロジェクト.
            contents: 実行で利用可能として固定する有効メモリ内容.

        Returns:
            保存済みのスナップショット. 空集合でも 1 件生成する.
        """
        items = tuple(
            MemorySnapshotItem(
                memory_id=content.memory_id,
                version=content.version,
                content_hash=content.content_hash,
            )
            for content in contents
        )
        snapshot_hash = compute_memory_snapshot_hash(items)
        snapshot = MemorySnapshot(
            snapshot_id=snapshot_hash[:32],
            project_id=project_id,
            snapshot_hash=snapshot_hash,
            created_at=self._clock.now_utc(),
            items=items,
        )
        self._repository.save(snapshot)
        return snapshot
