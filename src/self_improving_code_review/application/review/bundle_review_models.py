"""単位実行の入出力を不変モデルで受け渡す."""

from __future__ import annotations

from dataclasses import dataclass

from self_improving_code_review.application.ports.context_source import (
    SourceFingerprint,
)
from self_improving_code_review.application.ports.unit_of_work import (
    LlmCallRecord,
)
from self_improving_code_review.application.review.context_builder import (
    BuiltPrompt,
)
from self_improving_code_review.application.review.context_tools import (
    ContextToolOutcome,
)
from self_improving_code_review.application.review.models import (
    BundleOutcome,
    ReviewBundle,
    ReviewPlan,
)
from self_improving_code_review.application.review.reviewer_merge import (
    FindingSource,
    ReviewerExecutionSummary,
)
from self_improving_code_review.domain.finding.models import Finding
from self_improving_code_review.domain.rule.models import ReviewRule
from self_improving_code_review.security.models import (
    ScannedPayload,
    SecretFinding,
)


@dataclass(frozen=True, slots=True)
class BundleTask:
    """並列ワーカーへ渡す不変のレビュー単位入力."""

    bundle: ReviewBundle
    plan: ReviewPlan
    applicable_rules: tuple[ReviewRule, ...]
    scanned: ScannedPayload
    built: BuiltPrompt
    input_hash: str


@dataclass(frozen=True, slots=True)
class BundleReview:
    """ワーカーから調整処理へ返す永続化前のレビュー結果."""

    task: BundleTask
    outcome: BundleOutcome
    llm_records: tuple[LlmCallRecord, ...] = ()
    tool_outcomes: tuple[ContextToolOutcome, ...] = ()
    reflected_findings: tuple[Finding, ...] = ()
    secret_findings: tuple[SecretFinding, ...] = ()
    candidate_count: int = 0
    rejection_reason_counts: tuple[str, ...] = ()
    reflection_call_count: int = 0
    source_fingerprints: tuple[SourceFingerprint, ...] = ()
    finding_sources: tuple[FindingSource, ...] = ()
    reviewer_executions: tuple[ReviewerExecutionSummary, ...] = ()
