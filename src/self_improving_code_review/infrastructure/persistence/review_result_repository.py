"""固定された保存結果を決定的な順序で読み戻す."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from decimal import Decimal

from self_improving_code_review.application.review.models import (
    BundleOutcome,
    ReviewMetrics,
    ReviewResult,
)
from self_improving_code_review.domain.change.file_filter import FileFilterResult
from self_improving_code_review.domain.common.enums import (
    BundleStatus,
    FileDecision,
    FilterReason,
    ReflectionStatus,
    ValidationStatus,
)
from self_improving_code_review.domain.common.values import (
    BundleId,
    NormalizedPath,
    RunId,
)
from self_improving_code_review.domain.finding.models import (
    Finding,
)
from self_improving_code_review.infrastructure.persistence.review_metrics_codec import (
    metrics_from_document,
)
from self_improving_code_review.infrastructure.persistence.review_row_mapper import (
    row_to_finding,
    row_to_run,
)

_REVIEW_RUN_BY_KEY_SQL = """
    SELECT run_id, project_id, mode, status, source_revision, model,
           package_version, config_hash, prompt_version, prompt_hash,
           rule_snapshot_id, rule_set_hash, secret_engine_version,
           memory_snapshot_id, idempotency_key, started_at, finished_at,
           agent_prompt_version, agent_prompt_hash,
           reflection_prompt_version, reflection_prompt_hash, resume_count,
           metrics_json, pipeline_config_hash
    FROM review_runs
    WHERE idempotency_key = ? AND status IN ({placeholders})
    ORDER BY started_at DESC, rowid DESC
    LIMIT 1
"""


class SqliteReviewResultRepository:
    """冪等な再実行のために完了済みレビュー結果を再構築する."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """保存済みの実行結果を再構築する SQLite 接続を保持する."""
        self._connection = connection

    def find_by_idempotency_key(self, key: str) -> ReviewResult | None:
        """完全に復元した完了結果を返す。存在しない場合は None を返す."""
        return self._find_by_idempotency_key(key, statuses=("success",))

    def find_resumable_by_idempotency_key(self, key: str) -> ReviewResult | None:
        """部分成功または失敗の保存済み結果を返す."""
        return self._find_by_idempotency_key(
            key,
            statuses=("partial-success", "failed"),
        )

    def find_interrupted_by_idempotency_key(self, key: str) -> ReviewResult | None:
        """有効なリースで再取得するため、実行中の保存済み結果を返す."""
        return self._find_by_idempotency_key(
            key,
            statuses=("running",),
            allow_incomplete_metrics=True,
        )

    def _find_by_idempotency_key(
        self,
        key: str,
        *,
        statuses: tuple[str, ...],
        allow_incomplete_metrics: bool = False,
    ) -> ReviewResult | None:
        """指定状態の完全な保存結果を再構築する."""
        placeholders = ",".join("?" for _item in statuses)
        # IN 句には内部列挙の件数分の疑問符だけを生成し、状態値は別引数へ束縛する。
        query = _REVIEW_RUN_BY_KEY_SQL.format(placeholders=placeholders)  # nosec B608
        row = self._connection.execute(
            query,
            (key, *statuses),
        ).fetchone()
        if row is None:
            return None
        if row[22] is None and not allow_incomplete_metrics:
            return None
        run = row_to_run(row[:22])
        item_count = self._connection.execute(
            "SELECT COUNT(*) FROM rule_snapshot_items WHERE rule_snapshot_id = ?",
            (run.versions.rule_snapshot_id,),
        ).fetchone()[0]
        run = replace(
            run,
            versions=replace(run.versions, is_rule_set_empty=int(item_count) == 0),
        )
        findings = self._load_findings(run.run_id)
        main = tuple(item for item in findings if item.is_main)
        reference = tuple(
            item
            for item in findings
            if item.validation.reflection_status is ReflectionStatus.NEEDS_CONTEXT
        )
        outcomes = self._load_outcomes(run.run_id, findings)
        metrics = (
            metrics_from_document(json.loads(str(row[22])))
            if row[22] is not None
            else _interrupted_metrics(findings, outcomes)
        )
        filtered = self._load_filtered(run.run_id)
        return ReviewResult(
            run=run,
            main_findings=main,
            reference_findings=reference,
            filtered_files=filtered,
            metrics=metrics,
            bundle_outcomes=outcomes,
        )

    def _load_findings(self, run_id: RunId) -> tuple[Finding, ...]:
        """指摘を決定的な識別子順で読み込む."""
        rows = self._connection.execute(
            "SELECT * FROM findings WHERE run_id = ? ORDER BY finding_id",
            (str(run_id),),
        ).fetchall()
        return tuple(row_to_finding(run_id, row) for row in rows)

    def _load_outcomes(
        self, run_id: RunId, findings: tuple[Finding, ...]
    ) -> tuple[BundleOutcome, ...]:
        """レビュー単位の結果を読み込み、対応する指摘を関連付ける."""
        rows = self._connection.execute(
            """
            SELECT bundle_id, sequence, status, input_tokens, output_tokens,
                   latency_ms, cost_usd, error_code, error_message
            FROM bundles WHERE run_id = ? ORDER BY sequence
            """,
            (str(run_id),),
        ).fetchall()
        return tuple(
            BundleOutcome(
                bundle_id=BundleId(run_id, int(row[1])),
                status=BundleStatus(str(row[2])),
                findings=tuple(item for item in findings if str(item.bundle_id) == str(row[0])),
                input_tokens=int(row[3]),
                output_tokens=int(row[4]),
                latency_ms=int(row[5]),
                cost_usd=Decimal(str(row[6])),
                error_code=str(row[7]) if row[7] is not None else None,
                error_message=str(row[8]) if row[8] is not None else None,
            )
            for row in rows
        )

    def _load_filtered(self, run_id: RunId) -> tuple[FileFilterResult, ...]:
        """保存済みの選別結果をパス順で読み込む."""
        rows = self._connection.execute(
            """
            SELECT path, decision, reason FROM filtered_files
            WHERE run_id = ? ORDER BY path
            """,
            (str(run_id),),
        ).fetchall()
        return tuple(
            FileFilterResult(
                path=NormalizedPath(str(row[0])),
                decision=FileDecision(str(row[1])),
                reason=FilterReason(str(row[2])) if row[2] is not None else None,
            )
            for row in rows
        )


def _interrupted_metrics(
    findings: tuple[Finding, ...],
    outcomes: tuple[BundleOutcome, ...],
) -> ReviewMetrics:
    """最終集計前に中断した実行から再開判定用の暫定測定値を構築する."""
    return ReviewMetrics(
        input_tokens=sum(outcome.input_tokens for outcome in outcomes),
        output_tokens=sum(outcome.output_tokens for outcome in outcomes),
        latency_ms=sum(outcome.latency_ms for outcome in outcomes),
        cost_usd=sum(
            (outcome.cost_usd for outcome in outcomes),
            start=Decimal("0"),
        ),
        candidate_count=len(findings),
        main_finding_count=sum(finding.is_main for finding in findings),
        reference_finding_count=sum(
            finding.validation.reflection_status is ReflectionStatus.NEEDS_CONTEXT
            for finding in findings
        ),
        invalid_position_count=sum(
            finding.validation.position_status is ValidationStatus.INVALID for finding in findings
        ),
        invalid_evidence_count=sum(
            finding.validation.evidence_status is ValidationStatus.INVALID for finding in findings
        ),
        duplicate_count=0,
        bundle_count=len(outcomes),
        failed_bundle_count=sum(outcome.status is BundleStatus.FAILED for outcome in outcomes),
    )
