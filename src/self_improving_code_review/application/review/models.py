"""アプリケーション層のレビューモデル."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from self_improving_code_review.application.ports.diff_provider import DiffSource
from self_improving_code_review.domain.change.file_filter import FileFilterResult
from self_improving_code_review.domain.change.models import ChangedFile
from self_improving_code_review.domain.common.enums import (
    BundleStatus,
    ReviewerType,
    ReviewMode,
    RiskLevel,
    RunStatus,
)
from self_improving_code_review.domain.common.values import BundleId, ProjectId, RunId
from self_improving_code_review.domain.finding.models import Finding
from self_improving_code_review.domain.rule.models import FileRuleBinding
from self_improving_code_review.security.models import SecretFinding

__all__ = [
    "BundleOutcome",
    "ReviewBudget",
    "ReviewBundle",
    "ReviewMetrics",
    "ReviewPlan",
    "ReviewRequest",
    "ReviewResult",
    "ReviewerAssignment",
    "ReviewerProfileMetrics",
    "RunRecord",
    "RunVersions",
]


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    """検証済みのレビュー実行入力."""

    repository_path: Path
    project_id: ProjectId
    diff_source: DiffSource
    mode: ReviewMode
    output_path: Path | None = None
    config_path: Path | None = None
    execution_nonce: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewBundle:
    """まとめてレビューする関連ファイルの集合."""

    bundle_id: BundleId
    files: tuple[ChangedFile, ...]
    risk: RiskLevel
    diff_text: str
    diff_tokens: int

    @property
    def paths(self) -> tuple[str, ...]:
        """レビュー単位に含まれる正規化済みパスを返す."""
        return tuple(changed_file.new_path.value for changed_file in self.files)


@dataclass(frozen=True, slots=True)
class ReviewBudget:
    """レビュー単位に適用するトークン数とツール呼び出し回数の上限."""

    bundle_diff_tokens: int
    rule_tokens: int
    max_output_tokens: int
    max_tool_calls: int = 0
    dynamic_context_tokens: int = 0
    reflection_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ReviewerAssignment:
    """決定的に選択した一つのレビュー担当と選択根拠."""

    reviewer_type: ReviewerType
    reason_code: str
    score: int


@dataclass(frozen=True, slots=True)
class ReviewPlan:
    """1 つのレビュー単位に対するレビュー担当の選択と予算.

    M1 では ``is_reflection_enabled`` は常に False とする. このフィールドは
    M3 で計画の構造を変更せずに済むよう設ける.
    """

    bundle_id: BundleId
    reviewer_type: ReviewerType
    perspective: str
    applied_rule_ids: tuple[str, ...]
    budget: ReviewBudget
    is_reflection_enabled: bool = False
    is_context_tool_enabled: bool = False
    file_rule_bindings: tuple[FileRuleBinding, ...] = ()
    profile_reason_code: str = "GENERAL_DEFAULT"
    additional_reviewers: tuple[ReviewerAssignment, ...] = ()
    selection_exclusion_reason: str | None = None
    is_specialist: bool = False
    profile_score: int = 0


@dataclass(frozen=True, slots=True)
class BundleOutcome:
    """単一レビュー単位のレビュー結果."""

    bundle_id: BundleId
    status: BundleStatus
    findings: tuple[Finding, ...]
    error_code: str | None = None
    error_message: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))


@dataclass(frozen=True, slots=True)
class RunVersions:
    """実行の再現に必要な識別子."""

    package_version: str
    config_hash: str
    prompt_version: str
    prompt_hash: str
    rule_snapshot_id: str
    rule_set_hash: str
    is_rule_set_empty: bool
    secret_engine_version: str
    memory_snapshot_id: str | None = None
    agent_prompt_version: str | None = None
    agent_prompt_hash: str | None = None
    reflection_prompt_version: str | None = None
    reflection_prompt_hash: str | None = None
    pipeline_config_hash: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewerProfileMetrics:
    """一つの担当種別に集約した安全な使用量と指摘件数."""

    reviewer_type: ReviewerType
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: Decimal
    valid_finding_count: int
    duplicate_count: int


@dataclass(frozen=True, slots=True)
class ReviewMetrics:
    """実行単位で集計したコストと品質の測定値."""

    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: Decimal
    candidate_count: int
    main_finding_count: int
    reference_finding_count: int
    invalid_position_count: int
    invalid_evidence_count: int
    duplicate_count: int
    bundle_count: int
    failed_bundle_count: int
    memory_truncated_count: int = 0
    rejection_reason_counts: tuple[tuple[str, int], ...] = ()
    tool_call_count: int = 0
    tool_success_count: int = 0
    tool_latency_ms: int = 0
    tool_reason_counts: tuple[tuple[str, int], ...] = ()
    dynamic_context_tokens: int = 0
    reflection_call_count: int = 0
    reflection_supported_count: int = 0
    reflection_contradicted_count: int = 0
    reflection_needs_context_count: int = 0
    resumed_bundle_count: int = 0
    reexecuted_bundle_count: int = 0
    is_m3_enabled: bool = False
    reviewer_profile_metrics: tuple[ReviewerProfileMetrics, ...] = ()

    @property
    def rejected_candidate_count(self) -> int:
        """解析またはルール検証で除外された候補数を返す."""
        return sum(count for _reason, count in self.rejection_reason_counts)

    @property
    def accepted_candidate_count(self) -> int:
        """解析およびルール参照検証を通過した候補数を返す."""
        return self.candidate_count - self.rejected_candidate_count

    @property
    def validation_pass_rate(self) -> float:
        """主要指摘一覧に採用された候補の割合を返す."""
        if self.candidate_count == 0:
            return 0.0
        return self.main_finding_count / self.candidate_count


@dataclass(frozen=True, slots=True)
class RunRecord:
    """永続化されたレビュー実行状態."""

    run_id: RunId
    project_id: ProjectId
    mode: ReviewMode
    status: RunStatus
    source_revision: str | None
    model: str
    versions: RunVersions
    idempotency_key: str
    started_at: datetime
    finished_at: datetime | None = None
    resume_count: int = 0


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """呼び出し元へ返す完全な処理結果."""

    run: RunRecord
    main_findings: tuple[Finding, ...]
    reference_findings: tuple[Finding, ...]
    filtered_files: tuple[FileFilterResult, ...]
    metrics: ReviewMetrics
    bundle_outcomes: tuple[BundleOutcome, ...]
    secret_findings: tuple[SecretFinding, ...] = field(default_factory=tuple)
