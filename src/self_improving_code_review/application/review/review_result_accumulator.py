"""実行ローカルな結果と測定値を保存順に累積する."""

from __future__ import annotations

from collections import Counter

from self_improving_code_review.application.review.bundle_review_models import (
    BundleReview,
)
from self_improving_code_review.application.review.models import (
    BundleOutcome,
    ReviewMetrics,
)
from self_improving_code_review.application.review.review_manager import (
    RunAggregation,
)
from self_improving_code_review.application.review.review_metrics import build_metrics
from self_improving_code_review.application.review.reviewer_merge import (
    ReviewerExecutionSummary,
)
from self_improving_code_review.domain.common.enums import (
    ToolStatus,
)
from self_improving_code_review.domain.common.values import MemoryId, MemoryVersion
from self_improving_code_review.domain.finding.models import Finding


class ReviewResultAccumulator:
    """入力コレクションを複製し、当該実行だけの結果と計数を保持する."""

    def __init__(self, resumed_outcomes: tuple[BundleOutcome, ...]) -> None:
        """成功済み単位の結果を複製し、新規の計数を初期化する."""
        self.findings: list[Finding] = [
            finding for outcome in resumed_outcomes for finding in outcome.findings
        ]
        self.outcomes: list[BundleOutcome] = list(resumed_outcomes)
        self.reviewer_executions: list[ReviewerExecutionSummary] = []
        self.candidate_count: int = len(self.findings)
        self.rejection_reason_counts: Counter[str] = Counter()
        self.finding_memory_links: list[tuple[str, MemoryId, MemoryVersion]] = []
        self.tool_call_count: int = 0
        self.tool_success_count: int = 0
        self.tool_latency_ms: int = 0
        self.tool_reason_counts: Counter[str] = Counter()
        self.dynamic_context_tokens: int = 0
        self.reflection_call_count: int = 0
        self.reflection_supported_count: int = 0
        self.reflection_contradicted_count: int = 0
        self.reflection_needs_context_count: int = 0

    def add_review(
        self,
        review: BundleReview,
        outcome: BundleOutcome,
        validated_findings: tuple[Finding, ...],
        *,
        is_blocked: bool,
    ) -> None:
        """採用結果と除外前指摘を区別して計数し、遮断時は業務指摘を加算しない."""
        built = review.task.built
        self.outcomes.append(outcome)
        self.reviewer_executions.extend(review.reviewer_executions)
        self.tool_call_count += len(review.tool_outcomes)
        self.tool_success_count += sum(
            item.status is ToolStatus.SUCCESS for item in review.tool_outcomes
        )
        self.tool_latency_ms += sum(item.latency_ms for item in review.tool_outcomes)
        self.tool_reason_counts.update(item.reason_code for item in review.tool_outcomes)
        self.dynamic_context_tokens += sum(item.consumed_tokens for item in review.tool_outcomes)
        self.reflection_call_count += review.reflection_call_count
        if not is_blocked:
            self.findings.extend(validated_findings)
            self.candidate_count += review.candidate_count
            self.rejection_reason_counts.update(review.rejection_reason_counts)
            self.reflection_supported_count += sum(
                item.validation.reflection_status.value == "supported"
                for item in review.reflected_findings
            )
            self.reflection_contradicted_count += sum(
                item.validation.reflection_status.value == "contradicted"
                for item in review.reflected_findings
            )
            self.reflection_needs_context_count += sum(
                item.validation.reflection_status.value == "needs-context"
                for item in review.reflected_findings
            )
        self.finding_memory_links.extend(
            (str(finding.finding_id), memory_id, version)
            for finding in outcome.findings
            for memory_id, version in built.rendered_memory_ids
        )

    def to_metrics(
        self,
        aggregation: RunAggregation,
        *,
        memory_truncated_count: int,
        resumed_bundle_count: int,
        reexecuted_bundle_count: int,
        is_m3_enabled: bool,
    ) -> ReviewMetrics:
        """既存の安定順と分母で最終測定値を構築する."""
        return build_metrics(
            outcomes=tuple(self.outcomes),
            candidate_count=self.candidate_count,
            main_count=len(aggregation.main_findings),
            reference_count=len(aggregation.reference_findings),
            invalid_position_count=aggregation.invalid_position_count,
            invalid_evidence_count=aggregation.invalid_evidence_count,
            duplicate_count=len(aggregation.duplicates),
            memory_truncated_count=memory_truncated_count,
            rejection_reason_counts=tuple(sorted(self.rejection_reason_counts.items())),
            tool_call_count=self.tool_call_count,
            tool_success_count=self.tool_success_count,
            tool_latency_ms=self.tool_latency_ms,
            tool_reason_counts=tuple(sorted(self.tool_reason_counts.items())),
            dynamic_context_tokens=self.dynamic_context_tokens,
            reflection_call_count=self.reflection_call_count,
            reflection_supported_count=self.reflection_supported_count,
            reflection_contradicted_count=self.reflection_contradicted_count,
            reflection_needs_context_count=self.reflection_needs_context_count,
            resumed_bundle_count=resumed_bundle_count,
            reexecuted_bundle_count=reexecuted_bundle_count,
            is_m3_enabled=is_m3_enabled,
            reviewer_executions=tuple(self.reviewer_executions),
        )
