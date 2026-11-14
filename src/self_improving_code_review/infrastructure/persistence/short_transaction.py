"""メモリ読み書きを短い作業単位へ委譲するアダプター.

メモリ検索とスナップショット固定はレビュー処理の途中で必要になるが、
外部 LLM 呼び出し中はトランザクションを保持できない. このアダプターは
呼び出しごとに短い作業単位を開き、接続を保持しない.
"""

from __future__ import annotations

from self_improving_code_review.application.ports.memory_persistence import (
    ExtractionEvidence,
    ExtractionRecord,
    FeedbackRunContext,
    MemoryEvidenceLink,
)
from self_improving_code_review.application.ports.unit_of_work import UnitOfWorkFactory
from self_improving_code_review.domain.common.enums import MemoryStatus
from self_improving_code_review.domain.common.values import (
    ExtractionId,
    FeedbackId,
    MemoryId,
    MemoryVersion,
    ProjectId,
    RunId,
)
from self_improving_code_review.domain.feedback.models import FeedbackRecord
from self_improving_code_review.domain.memory.models import (
    MemoryContent,
    MemoryRecord,
    MemorySnapshot,
    MemoryTransition,
)

__all__ = [
    "ShortTransactionExtractionRepository",
    "ShortTransactionFeedbackRepository",
    "ShortTransactionMemoryRepository",
    "ShortTransactionSnapshotRepository",
]


class ShortTransactionFeedbackRepository:
    """呼び出しごとに作業単位を開くフィードバックリポジトリ."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        """作業単位の生成処理を保持する."""
        self._unit_of_work_factory = unit_of_work_factory

    def save(self, record: FeedbackRecord) -> None:
        """フィードバックを 1 件登録する."""
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.feedback.save(record)

    def find_by_idempotency_key(self, key: str) -> FeedbackRecord | None:
        """``key`` に一致するフィードバックを返す。存在しない場合は None を返す."""
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.feedback.find_by_idempotency_key(key)

    def find_by_ids(self, feedback_ids: tuple[FeedbackId, ...]) -> tuple[FeedbackRecord, ...]:
        """指定した識別子のフィードバックを識別子順で返す."""
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.feedback.find_by_ids(feedback_ids)

    def exists_finding(self, run_id: RunId, finding_id: str) -> bool:
        """``run_id`` に ``finding_id`` が属する場合は True を返す."""
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.feedback.exists_finding(run_id, finding_id)

    def find_run_context(self, run_id: RunId) -> FeedbackRunContext | None:
        """対象実行のプロジェクトとソース改訂を返す."""
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.feedback.find_run_context(run_id)

    def find_finding_context(self, run_id: RunId, finding_id: str) -> str | None:
        """検証済み指摘の候補抽出用最小コンテキストを返す."""
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.feedback.find_finding_context(run_id, finding_id)


class ShortTransactionExtractionRepository:
    """呼び出しごとに作業単位を開く抽出実行リポジトリ."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        """作業単位の生成処理を保持する."""
        self._unit_of_work_factory = unit_of_work_factory

    def create(self, record: ExtractionRecord, evidence: tuple[ExtractionEvidence, ...]) -> None:
        """抽出意図と根拠集合を登録する."""
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.extractions.create(record, evidence)

    def find_by_idempotency_key(self, key: str) -> ExtractionRecord | None:
        """``key`` に一致する抽出実行を返す。存在しない場合は None を返す."""
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.extractions.find_by_idempotency_key(key)

    def acquire(self, extraction_id: ExtractionId, attempt: int) -> bool:
        """``PENDING`` または ``FAILED`` を ``RUNNING`` へ取得する."""
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.extractions.acquire(extraction_id, attempt)

    def finalize(self, record: ExtractionRecord) -> None:
        """抽出実行の最終状態、使用量、候補参照を記録する."""
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.extractions.finalize(record)

    def finalize_success(
        self,
        extraction: ExtractionRecord,
        memory: MemoryRecord,
        content: MemoryContent,
        evidence: tuple[MemoryEvidenceLink, ...],
        transition: MemoryTransition,
    ) -> None:
        """候補一式と抽出成功状態を一つの短い作業単位で保存する."""
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.extractions.finalize_success(
                extraction,
                memory,
                content,
                evidence,
                transition,
            )


class ShortTransactionMemoryRepository:
    """呼び出しごとに作業単位を開くメモリリポジトリ."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        """作業単位の生成処理を保持する."""
        self._unit_of_work_factory = unit_of_work_factory

    def create(
        self,
        record: MemoryRecord,
        content: MemoryContent,
        evidence: tuple[MemoryEvidenceLink, ...],
        transition: MemoryTransition,
    ) -> None:
        """論理メモリとバージョン 1 を同一トランザクションで登録する."""
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.memories.create(record, content, evidence, transition)

    def add_version(
        self,
        memory_id: MemoryId,
        content: MemoryContent,
        expected_lock_version: int,
    ) -> None:
        """現在バージョンを上書きせず新しいバージョンを追加する."""
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.memories.add_version(memory_id, content, expected_lock_version)

    def change_status(
        self,
        transition: MemoryTransition,
        expected_lock_version: int,
    ) -> None:
        """状態を遷移させ、監査記録を残す."""
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.memories.change_status(transition, expected_lock_version)

    def add_version_and_change_status(
        self,
        memory_id: MemoryId,
        content: MemoryContent,
        transition: MemoryTransition,
        expected_lock_version: int,
    ) -> None:
        """新しい内容バージョンと状態遷移を一つの作業単位で保存する."""
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.memories.add_version_and_change_status(
                memory_id,
                content,
                transition,
                expected_lock_version,
            )

    def find(self, memory_id: MemoryId) -> tuple[MemoryRecord, MemoryContent] | None:
        """メモリと現在バージョンの内容を返す。存在しない場合は None を返す."""
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.memories.find(memory_id)

    def list_by_status(
        self,
        project_id: ProjectId,
        status: MemoryStatus,
    ) -> tuple[tuple[MemoryRecord, MemoryContent], ...]:
        """状態が一致するメモリを識別子順で返す."""
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.memories.list_by_status(project_id, status)


class ShortTransactionSnapshotRepository:
    """呼び出しごとに作業単位を開くスナップショットリポジトリ."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        """作業単位の生成処理を保持する."""
        self._unit_of_work_factory = unit_of_work_factory

    def save(self, snapshot: MemorySnapshot) -> None:
        """未登録の場合にスナップショットと各項目を登録する."""
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.memory_snapshots.save(snapshot)

    def link_findings(
        self,
        snapshot: MemorySnapshot,
        run_id: RunId,
        finding_memory_ids: tuple[tuple[str, MemoryId, MemoryVersion], ...],
    ) -> None:
        """実際にプロンプトへ入れたメモリだけを指摘へ関連付ける."""
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.memory_snapshots.link_findings(snapshot, run_id, finding_memory_ids)
