"""永続化ポート."""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Protocol

from self_improving_code_review.application.ports.context_source import SourceFingerprint
from self_improving_code_review.application.ports.memory_persistence import (
    ExtractionRepository,
    FeedbackRepository,
    MemoryRepository,
    MemorySnapshotRepository,
)
from self_improving_code_review.application.ports.reviewer_audit import ReviewerAuditRepository
from self_improving_code_review.application.ports.run_lease import RunLeaseIdentity
from self_improving_code_review.application.review.context_tools import ContextToolOutcome
from self_improving_code_review.application.review.dependency_bundler import BundleEdge
from self_improving_code_review.application.review.models import (
    BundleOutcome,
    ReviewBundle,
    ReviewMetrics,
    ReviewPlan,
    ReviewResult,
    RunRecord,
)
from self_improving_code_review.application.review.resume_review import StoredResumeIdentity
from self_improving_code_review.domain.change.file_filter import FileFilterResult
from self_improving_code_review.domain.common.enums import RunStatus
from self_improving_code_review.domain.common.values import BundleId, RunId
from self_improving_code_review.domain.finding.models import Finding
from self_improving_code_review.domain.rule.models import RuleSnapshot

__all__ = [
    "BundleRepository",
    "FindingRepository",
    "LlmCallRecord",
    "LlmCallRepository",
    "M3AuditRepository",
    "ReviewResultRepository",
    "RuleSnapshotRepository",
    "RunRepository",
    "UnitOfWork",
    "UnitOfWorkFactory",
]

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class LlmCallRecord:
    """1 回の LLM 呼び出しの測定値.

    要求ハッシュはプロンプト自体を保存せずに送信内容を識別する.
    """

    run_id: RunId
    bundle_sequence: int
    request_hash: str
    model: str
    status: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: Decimal
    attempt: int
    call_kind: str = "review"
    turn_index: int = 1


class RunRepository(Protocol):
    """レビュー実行を保存および更新する."""

    def create(self, run: RunRecord) -> None:
        """新しい実行を登録する."""
        ...

    def finalize(
        self,
        run_id: RunId,
        status: RunStatus,
        metrics: ReviewMetrics,
        finished_at: datetime,
        memory_snapshot_id: str | None = None,
        *,
        lease_identity: RunLeaseIdentity | None = None,
        lease_checked_at: datetime | None = None,
    ) -> None:
        """実行の最終状態と測定値を記録する."""
        ...

    def find_completed_by_idempotency_key(self, key: str) -> RunRecord | None:
        """``key`` に一致する完了済み実行を返す。存在しない場合は None を返す."""
        ...

    def acquire_resume(
        self,
        run_id: RunId,
        *,
        lease_identity: RunLeaseIdentity | None = None,
        lease_checked_at: datetime | None = None,
    ) -> bool:
        """再開可能状態または取得済みの中断実行を原子的に実行中へ遷移する."""
        ...


class BundleRepository(Protocol):
    """レビュー単位、ファイル判定、処理結果を保存する."""

    def save(
        self,
        bundle: ReviewBundle,
        outcome: BundleOutcome,
        file_results: tuple[FileFilterResult, ...],
    ) -> None:
        """レビュー単位を処理結果およびファイル判定とともに登録する."""
        ...

    def save_filtered_files(
        self, run_id: RunId, file_results: tuple[FileFilterResult, ...]
    ) -> None:
        """どのレビュー単位にも属さないファイル判定を登録する."""
        ...


class FindingRepository(Protocol):
    """検証済みの指摘を保存する."""

    def save_all(self, run_id: RunId, findings: tuple[Finding, ...]) -> None:
        """実行の指摘を登録する."""
        ...


class RuleSnapshotRepository(Protocol):
    """ルールスナップショット参照を保存する."""

    def save(self, snapshot: RuleSnapshot) -> None:
        """未登録の場合にスナップショットを登録する."""
        ...

    def link_findings(self, snapshot: RuleSnapshot, findings: tuple[Finding, ...]) -> None:
        """指摘と使用した固定済みルールを関連付ける."""
        ...


class LlmCallRepository(Protocol):
    """LLM 呼び出しの測定値を保存する."""

    def save(self, record: LlmCallRecord) -> int:
        """呼び出し記録を登録し、データベース識別子を返す."""
        ...


class ReviewResultRepository(Protocol):
    """冪等な再実行のため、完了済みレビューを読み込む."""

    def find_by_idempotency_key(self, key: str) -> ReviewResult | None:
        """``key`` に一致する完了結果を返す。存在しない場合は None を返す."""
        ...

    def find_resumable_by_idempotency_key(self, key: str) -> ReviewResult | None:
        """再開候補の部分成功または失敗結果を返す."""
        ...

    def find_interrupted_by_idempotency_key(self, key: str) -> ReviewResult | None:
        """リース再取得の候補となる実行中結果を返す."""
        ...


class M3AuditRepository(Protocol):
    """M3 の決定結果、ツール、自省、チェックポイントを保存する."""

    def save_edges(self, run_id: RunId, edges: tuple[BundleEdge, ...]) -> None:
        """関係グラフの全辺と採否を保存する."""
        ...

    def save_run_identity(self, run_id: RunId, plan_hash: str) -> None:
        """実行全体の結合計画ハッシュを保存する."""
        ...

    def save_context_sources(
        self,
        run_id: RunId,
        bundle_id: BundleId,
        fingerprints: tuple[SourceFingerprint, ...],
    ) -> None:
        """レビュー単位が読んだ追加ソースの安全なハッシュを保存する."""
        ...

    def refresh_context_source_hash(self, run_id: RunId) -> str:
        """保存済み追加ソース集合を集約し、実行へ保存して返す."""
        ...

    def load_resume_identity(self, run_id: RunId) -> StoredResumeIdentity | None:
        """厳格な再開判定に必要な固定ハッシュを復元する."""
        ...

    def save_bundle_metadata(
        self,
        bundle: ReviewBundle,
        plan: ReviewPlan,
        input_hash: str,
        result_hash: str,
    ) -> None:
        """レビュー単位の担当と入出力ハッシュを保存する."""
        ...

    def save_tool_outcomes(
        self,
        run_id: RunId,
        bundle_id: BundleId,
        outcomes: tuple[ContextToolOutcome, ...],
        occurred_at: datetime,
    ) -> None:
        """本文を除いたツール監査情報を保存する."""
        ...

    def save_reflections(
        self,
        run_id: RunId,
        findings: tuple[Finding, ...],
        reflection_call_id: int,
    ) -> None:
        """自己検証済み指摘と LLM 呼出を関連付ける."""
        ...

    def save_checkpoint(
        self,
        run_id: RunId,
        stage: str,
        input_hash: str,
        created_at: datetime,
        *,
        bundle_id: BundleId | None = None,
        result_hash: str | None = None,
    ) -> None:
        """中断再開用の安全なハッシュだけを保存する."""
        ...


class UnitOfWork(Protocol):
    """各リポジトリをまとめるトランザクション境界.

    外部 LLM 呼び出しはトランザクション外で行うため、呼び出し元は
    永続化工程ごとに短い作業単位を開く.
    """

    runs: RunRepository
    bundles: BundleRepository
    findings: FindingRepository
    rule_snapshots: RuleSnapshotRepository
    llm_calls: LlmCallRepository
    results: ReviewResultRepository
    m3_audit: M3AuditRepository
    feedback: FeedbackRepository
    extractions: ExtractionRepository
    memories: MemoryRepository
    memory_snapshots: MemorySnapshotRepository
    reviewer_audit: ReviewerAuditRepository

    def __enter__(self) -> UnitOfWork:
        """トランザクションを開始する."""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """成功時はコミットし、失敗時はロールバックする."""
        ...

    def commit(self) -> None:
        """現在のトランザクションをコミットする."""
        ...

    def rollback(self) -> None:
        """現在のトランザクションをロールバックする."""
        ...


class UnitOfWorkFactory(Protocol):
    """短いトランザクション用に新しい作業単位を作成する."""

    def __call__(self) -> UnitOfWork:
        """新しい作業単位を返す."""
        ...
