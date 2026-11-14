"""M5 のレビュー担当監査永続化ポート。."""

from __future__ import annotations

from typing import Protocol

from self_improving_code_review.application.review.reviewer_merge import (
    FindingSource,
    ReviewerExecutionSummary,
)
from self_improving_code_review.domain.common.values import BundleId, RunId


class ReviewerAuditRepository(Protocol):
    """担当別実行と統合後指摘の採用元を保存する。."""

    def save_executions(
        self,
        run_id: RunId,
        bundle_id: BundleId,
        executions: tuple[ReviewerExecutionSummary, ...],
    ) -> None:
        """一レビュー単位の担当別実行結果を冪等に保存する。."""
        ...

    def save_finding_sources(
        self,
        run_id: RunId,
        sources: tuple[FindingSource, ...],
    ) -> None:
        """統合後指摘と全担当元指摘の対応を冪等に保存する。."""
        ...
