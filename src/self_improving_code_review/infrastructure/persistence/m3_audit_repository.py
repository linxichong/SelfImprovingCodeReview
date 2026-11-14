"""同一作業単位の接続でM3監査と再開識別情報を保存・復元する."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from self_improving_code_review.application.ports.context_source import SourceFingerprint
from self_improving_code_review.application.review.context_tools import ContextToolOutcome
from self_improving_code_review.application.review.dependency_bundler import BundleEdge
from self_improving_code_review.application.review.models import (
    ReviewBundle,
    ReviewPlan,
)
from self_improving_code_review.application.review.resume_review import (
    BundleResumeIdentity,
    StoredResumeIdentity,
    context_source_hash,
)
from self_improving_code_review.domain.common.enums import (
    ReflectionStatus,
)
from self_improving_code_review.domain.common.errors import (
    DatabaseError,
)
from self_improving_code_review.domain.common.hashing import hash_lines
from self_improving_code_review.domain.common.values import (
    BundleId,
    NormalizedPath,
    RunId,
)
from self_improving_code_review.domain.finding.models import (
    Finding,
)
from self_improving_code_review.messages import Messages


class SqliteM3AuditRepository:
    """M3 の監査メタデータを本文なしで保存する."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """同一作業単位の SQLite 接続を保持する."""
        self._connection = connection

    def save_edges(self, run_id: RunId, edges: tuple[BundleEdge, ...]) -> None:
        """関係辺と採否を保存する."""
        self._connection.executemany(
            """
            INSERT OR REPLACE INTO bundle_edges(
                run_id, left_path, right_path, relation, weight, decision
            ) VALUES(?,?,?,?,?,?)
            """,
            [
                (
                    str(run_id),
                    edge.left_path,
                    edge.right_path,
                    edge.relation.value,
                    edge.weight,
                    edge.decision.value,
                )
                for edge in edges
            ],
        )

    def save_run_identity(self, run_id: RunId, plan_hash: str) -> None:
        """M3 設定と結合計画の同一性ハッシュを実行へ保存する."""
        cursor = self._connection.execute(
            "UPDATE review_runs SET bundle_plan_hash = ? WHERE run_id = ?",
            (plan_hash, str(run_id)),
        )
        if cursor.rowcount != 1:
            raise DatabaseError(Messages.RUN_NOT_FOUND_FOR_M3_IDENTITY)

    def save_context_sources(
        self,
        run_id: RunId,
        bundle_id: BundleId,
        fingerprints: tuple[SourceFingerprint, ...],
    ) -> None:
        """追加ソースのパス、固定版、内容ハッシュだけを保存する."""
        self._connection.execute(
            "DELETE FROM context_source_files WHERE run_id = ? AND bundle_id = ?",
            (str(run_id), str(bundle_id)),
        )
        self._connection.executemany(
            """
            INSERT INTO context_source_files(
                run_id, bundle_id, path, source_revision, content_hash
            ) VALUES(?,?,?,?,?)
            """,
            (
                (
                    str(run_id),
                    str(bundle_id),
                    item.path.value,
                    item.source_revision,
                    item.content_hash,
                )
                for item in fingerprints
            ),
        )

    def refresh_context_source_hash(self, run_id: RunId) -> str:
        """追加ソース集合の安定ハッシュを計算して実行へ保存する."""
        files = self._load_source_files(run_id)
        aggregate = context_source_hash(files)
        cursor = self._connection.execute(
            "UPDATE review_runs SET context_source_hash = ? WHERE run_id = ?",
            (aggregate, str(run_id)),
        )
        if cursor.rowcount != 1:
            raise DatabaseError(Messages.RUN_NOT_FOUND_FOR_CONTEXT_HASH)
        return aggregate

    def load_resume_identity(self, run_id: RunId) -> StoredResumeIdentity | None:
        """実行、レビュー単位、チェックポイントの固定ハッシュを復元する."""
        run_row = self._connection.execute(
            """
            SELECT pipeline_config_hash, bundle_plan_hash, context_source_hash
            FROM review_runs WHERE run_id = ?
            """,
            (str(run_id),),
        ).fetchone()
        if run_row is None:
            return None
        diff_row = self._connection.execute(
            """
            SELECT input_hash FROM run_checkpoints
            WHERE run_id = ? AND stage = 'diff-parsed' AND bundle_id = ''
            """,
            (str(run_id),),
        ).fetchone()
        rows = self._connection.execute(
            """
            SELECT b.sequence, b.bundle_input_hash, b.reviewer_type,
                   b.bundle_result_hash, c.result_hash
            FROM bundles AS b
            LEFT JOIN run_checkpoints AS c
              ON c.run_id = b.run_id AND c.bundle_id = b.bundle_id
             AND c.stage = 'bundle-completed'
            WHERE b.run_id = ?
            ORDER BY b.sequence
            """,
            (str(run_id),),
        ).fetchall()
        bundles = tuple(
            BundleResumeIdentity(
                sequence=int(row[0]),
                input_hash=str(row[1]),
                reviewer_type=str(row[2]),
                result_hash=str(row[3]) if row[3] is not None else None,
                checkpoint_result_hash=str(row[4]) if row[4] is not None else None,
            )
            for row in rows
            if row[1] is not None and row[2] is not None
        )
        return StoredResumeIdentity(
            pipeline_config_hash=str(run_row[0]) if run_row[0] is not None else None,
            diff_hash=str(diff_row[0]) if diff_row is not None else None,
            plan_hash=str(run_row[1]) if run_row[1] is not None else None,
            context_source_hash=str(run_row[2]) if run_row[2] is not None else None,
            bundles=bundles,
            source_files=self._load_source_files(run_id),
        )

    def _load_source_files(self, run_id: RunId) -> tuple[SourceFingerprint, ...]:
        """追加ソース識別情報をレビュー単位とパスの順で復元する."""
        rows = self._connection.execute(
            """
            SELECT b.sequence, s.path, s.source_revision, s.content_hash
            FROM context_source_files AS s
            JOIN bundles AS b
              ON b.run_id = s.run_id AND b.bundle_id = s.bundle_id
            WHERE s.run_id = ?
            ORDER BY b.sequence, s.path
            """,
            (str(run_id),),
        ).fetchall()
        return tuple(
            SourceFingerprint(
                bundle_sequence=int(row[0]),
                path=NormalizedPath(str(row[1])),
                source_revision=str(row[2]) if row[2] is not None else None,
                content_hash=str(row[3]),
            )
            for row in rows
        )

    def save_bundle_metadata(
        self,
        bundle: ReviewBundle,
        plan: ReviewPlan,
        input_hash: str,
        result_hash: str,
    ) -> None:
        """担当種別、理由、入出力ハッシュを保存する."""
        cursor = self._connection.execute(
            """
            UPDATE bundles
            SET reviewer_type = ?, profile_reason_code = ?,
                bundle_input_hash = ?, bundle_result_hash = ?
            WHERE run_id = ? AND bundle_id = ?
            """,
            (
                plan.reviewer_type.value,
                plan.profile_reason_code,
                input_hash,
                result_hash,
                str(bundle.bundle_id.run_id),
                str(bundle.bundle_id),
            ),
        )
        if cursor.rowcount != 1:
            message = Messages.BUNDLE_NOT_FOUND_FOR_M3_METADATA
            raise DatabaseError(message)

    def save_tool_outcomes(
        self,
        run_id: RunId,
        bundle_id: BundleId,
        outcomes: tuple[ContextToolOutcome, ...],
        occurred_at: datetime,
    ) -> None:
        """ツール本文を除いた要求ハッシュと測定値を保存する."""
        rows = []
        for sequence, outcome in enumerate(outcomes, start=1):
            call_id = hash_lines((str(run_id), str(bundle_id), str(sequence), outcome.request_hash))
            rows.append(
                (
                    call_id,
                    str(run_id),
                    str(bundle_id),
                    sequence,
                    outcome.request_hash,
                    outcome.tool_name,
                    outcome.status.value,
                    outcome.consumed_tokens,
                    outcome.result_count,
                    outcome.reason_code,
                    None,
                    (occurred_at - timedelta(milliseconds=outcome.latency_ms)).isoformat(),
                    occurred_at.isoformat(),
                )
            )
        self._connection.executemany(
            """
            INSERT OR REPLACE INTO tool_calls(
                call_id, run_id, bundle_id, sequence, request_hash, tool_name,
                status, consumed_tokens, result_count, reason_code,
                source_metadata_hash, started_at, finished_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )

    def save_reflections(
        self,
        run_id: RunId,
        findings: tuple[Finding, ...],
        reflection_call_id: int,
    ) -> None:
        """自己検証状態を指摘と LLM 呼出へ関連付ける."""
        reason_by_status = {
            ReflectionStatus.SUPPORTED: "EVIDENCE_SUFFICIENT",
            ReflectionStatus.CONTRADICTED: "EVIDENCE_CONTRADICTED",
            ReflectionStatus.NEEDS_CONTEXT: "CONTEXT_REQUIRED",
        }
        rows = [
            (
                str(run_id),
                str(finding.finding_id),
                reflection_call_id,
                finding.validation.reflection_status.value,
                reason_by_status[finding.validation.reflection_status],
            )
            for finding in findings
            if finding.validation.reflection_status in reason_by_status
        ]
        self._connection.executemany(
            """
            INSERT OR REPLACE INTO reflection_results(
                run_id, finding_id, reflection_call_id, status, reason_code
            ) VALUES(?,?,?,?,?)
            """,
            rows,
        )

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
        """チェックポイントの安全なハッシュだけを保存する."""
        bundle_value = str(bundle_id) if bundle_id is not None else ""
        checkpoint_id = hash_lines(
            (str(run_id), stage, bundle_value, input_hash, result_hash or "")
        )
        self._connection.execute(
            """
            INSERT OR REPLACE INTO run_checkpoints(
                checkpoint_id, run_id, stage, bundle_id,
                input_hash, result_hash, created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                checkpoint_id,
                str(run_id),
                stage,
                bundle_value,
                input_hash,
                result_hash,
                created_at.isoformat(),
            ),
        )
