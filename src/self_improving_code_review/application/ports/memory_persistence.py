"""M2 のフィードバックおよびメモリ永続化ポート.

外部 LLM 呼び出し中はトランザクションを保持しないため、
呼び出し元は永続化工程ごとに短い作業単位を開く.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from self_improving_code_review.domain.common.enums import ExtractionStatus, MemoryStatus
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
    "ExtractionEvidence",
    "ExtractionRecord",
    "ExtractionRepository",
    "FeedbackRepository",
    "FeedbackRunContext",
    "MemoryEvidenceLink",
    "MemoryRepository",
    "MemorySnapshotRepository",
]


@dataclass(frozen=True, slots=True)
class FeedbackRunContext:
    """フィードバック対象実行のプロジェクトとソース改訂."""

    project_id: ProjectId
    source_revision: str | None
    is_test_evaluation: bool = False


@dataclass(frozen=True, slots=True)
class ExtractionRecord:
    """メモリ候補抽出実行の状態と測定値.

    プロンプト本文と未加工応答は保存せず、バージョン、ハッシュ、
    使用量、安全なエラーコードだけを残す.
    """

    extraction_id: ExtractionId
    project_id: ProjectId
    source_revision: str | None
    idempotency_key: str
    status: ExtractionStatus
    attempt: int
    prompt_version: str
    prompt_hash: str
    model: str
    started_at: datetime
    request_hash: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    safe_error_code: str | None = None
    candidate_memory_id: MemoryId | None = None
    candidate_version: MemoryVersion | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ExtractionEvidence:
    """抽出入力として固定した 1 件の根拠フィードバック.

    内容ハッシュを保存することで、同じ識別子でも内容が変わった場合に
    別入力として識別できる.
    """

    feedback_id: FeedbackId
    feedback_content_hash: str


@dataclass(frozen=True, slots=True)
class MemoryEvidenceLink:
    """メモリバージョンと根拠フィードバックの関連."""

    feedback_id: FeedbackId
    relation: str


class FeedbackRepository(Protocol):
    """フィードバックを追記のみで保存する."""

    def save(self, record: FeedbackRecord) -> None:
        """フィードバックを 1 件登録する."""
        ...

    def find_by_idempotency_key(self, key: str) -> FeedbackRecord | None:
        """``key`` に一致するフィードバックを返す。存在しない場合は None を返す."""
        ...

    def find_by_ids(self, feedback_ids: tuple[FeedbackId, ...]) -> tuple[FeedbackRecord, ...]:
        """指定した識別子のフィードバックを識別子順で返す."""
        ...

    def exists_finding(self, run_id: RunId, finding_id: str) -> bool:
        """``run_id`` に ``finding_id`` が属する場合は True を返す."""
        ...

    def find_run_context(self, run_id: RunId) -> FeedbackRunContext | None:
        """対象実行のコンテキストを返す。存在しない場合は None を返す."""
        ...

    def find_finding_context(self, run_id: RunId, finding_id: str) -> str | None:
        """検証済み指摘の候補抽出用最小コンテキストを返す."""
        ...


class ExtractionRepository(Protocol):
    """メモリ候補抽出実行を保存および更新する."""

    def create(self, record: ExtractionRecord, evidence: tuple[ExtractionEvidence, ...]) -> None:
        """抽出意図と根拠集合を登録する."""
        ...

    def find_by_idempotency_key(self, key: str) -> ExtractionRecord | None:
        """``key`` に一致する抽出実行を返す。存在しない場合は None を返す."""
        ...

    def acquire(self, extraction_id: ExtractionId, attempt: int) -> bool:
        """``PENDING`` または ``FAILED`` を ``RUNNING`` へ取得する.

        Returns:
            取得できた場合は True。他プロセスが実行中の場合は False。
        """
        ...

    def finalize(self, record: ExtractionRecord) -> None:
        """抽出実行の最終状態、使用量、候補参照を記録する."""
        ...

    def finalize_success(
        self,
        extraction: ExtractionRecord,
        memory: MemoryRecord,
        content: MemoryContent,
        evidence: tuple[MemoryEvidenceLink, ...],
        transition: MemoryTransition,
    ) -> None:
        """候補一式と抽出成功状態を同一トランザクションで保存する."""
        ...


class MemoryRepository(Protocol):
    """メモリの論理レコードと不変バージョンを保存する."""

    def create(
        self,
        record: MemoryRecord,
        content: MemoryContent,
        evidence: tuple[MemoryEvidenceLink, ...],
        transition: MemoryTransition,
    ) -> None:
        """論理メモリとバージョン 1 を同一トランザクションで登録する."""
        ...

    def add_version(
        self,
        memory_id: MemoryId,
        content: MemoryContent,
        expected_lock_version: int,
    ) -> None:
        """現在バージョンを上書きせず新しいバージョンを追加する."""
        ...

    def change_status(
        self,
        transition: MemoryTransition,
        expected_lock_version: int,
    ) -> None:
        """状態を遷移させ、監査記録を残す."""
        ...

    def add_version_and_change_status(
        self,
        memory_id: MemoryId,
        content: MemoryContent,
        transition: MemoryTransition,
        expected_lock_version: int,
    ) -> None:
        """新しい内容バージョンと状態遷移を同一トランザクションで保存する."""
        ...

    def find(self, memory_id: MemoryId) -> tuple[MemoryRecord, MemoryContent] | None:
        """メモリと現在バージョンの内容を返す。存在しない場合は None を返す."""
        ...

    def list_by_status(
        self,
        project_id: ProjectId,
        status: MemoryStatus,
    ) -> tuple[tuple[MemoryRecord, MemoryContent], ...]:
        """状態が一致するメモリを識別子順で返す."""
        ...


class MemorySnapshotRepository(Protocol):
    """メモリスナップショット参照を保存する."""

    def save(self, snapshot: MemorySnapshot) -> None:
        """未登録の場合にスナップショットと各項目を登録する."""
        ...

    def link_findings(
        self,
        snapshot: MemorySnapshot,
        run_id: RunId,
        finding_memory_ids: tuple[tuple[str, MemoryId, MemoryVersion], ...],
    ) -> None:
        """実際にプロンプトへ入れたメモリだけを指摘へ関連付ける."""
        ...
