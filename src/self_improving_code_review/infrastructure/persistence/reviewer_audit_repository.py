"""M5 の担当別実行と指摘採用元を SQLite へ保存する。."""

from __future__ import annotations

import sqlite3

from self_improving_code_review.application.review.reviewer_merge import (
    FindingSource,
    ReviewerExecutionSummary,
)
from self_improving_code_review.domain.common.values import BundleId, RunId


class SqliteReviewerAuditRepository:
    """短い作業単位内で担当実行監査を保存する。."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """既存の作業単位接続を保持する。."""
        self._connection = connection

    def save_executions(
        self,
        run_id: RunId,
        bundle_id: BundleId,
        executions: tuple[ReviewerExecutionSummary, ...],
    ) -> None:
        """一レビュー単位の担当別結果を再開可能な形で保存する。."""
        self._connection.executemany(
            """
            INSERT INTO reviewer_executions(
                run_id, bundle_id, profile, reason_code, score,
                prompt_version, prompt_hash, input_hash, status,
                input_tokens, output_tokens, latency_ms, cost_usd,
                valid_finding_count, duplicate_count
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id, bundle_id, profile) DO UPDATE SET
                reason_code=excluded.reason_code,
                score=excluded.score,
                prompt_version=excluded.prompt_version,
                prompt_hash=excluded.prompt_hash,
                input_hash=excluded.input_hash,
                status=excluded.status,
                input_tokens=excluded.input_tokens,
                output_tokens=excluded.output_tokens,
                latency_ms=excluded.latency_ms,
                cost_usd=excluded.cost_usd,
                valid_finding_count=excluded.valid_finding_count,
                duplicate_count=excluded.duplicate_count
            """,
            tuple(
                (
                    str(run_id),
                    str(bundle_id),
                    item.reviewer_type.value,
                    item.reason_code,
                    item.score,
                    item.prompt_version,
                    item.prompt_hash,
                    item.input_hash,
                    item.status.value,
                    item.input_tokens,
                    item.output_tokens,
                    item.latency_ms,
                    str(item.cost_usd),
                    item.valid_finding_count,
                    item.duplicate_count,
                )
                for item in executions
            ),
        )

    def save_finding_sources(
        self,
        run_id: RunId,
        sources: tuple[FindingSource, ...],
    ) -> None:
        """統合後指摘と担当別元指摘の対応を冪等に保存する。."""
        self._connection.executemany(
            """
            INSERT OR REPLACE INTO reviewer_finding_sources(
                run_id, finding_id, profile, source_finding_id,
                source_fingerprint, is_selected
            ) VALUES(?,?,?,?,?,?)
            """,
            tuple(
                (
                    str(run_id),
                    str(item.finding_id),
                    item.reviewer_type.value,
                    str(item.source_finding_id),
                    item.source_fingerprint,
                    int(item.is_selected),
                )
                for item in sources
            ),
        )
