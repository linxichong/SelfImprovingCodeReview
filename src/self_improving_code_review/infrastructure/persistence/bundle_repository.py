"""レビュー単位と対象ファイル判定を同一接続で保存する."""

from __future__ import annotations

import sqlite3

from self_improving_code_review.application.review.models import (
    BundleOutcome,
    ReviewBundle,
)
from self_improving_code_review.domain.change.file_filter import FileFilterResult
from self_improving_code_review.domain.common.values import (
    RunId,
)


class SqliteBundleRepository:
    """レビュー単位永続化の SQLite 実装."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """レビュー単位とファイル判定を保存する接続を保持する."""
        self._connection = connection

    def save(
        self,
        bundle: ReviewBundle,
        outcome: BundleOutcome,
        file_results: tuple[FileFilterResult, ...],
    ) -> None:
        """レビュー単位、処理結果、ファイル判定を登録する."""
        run_id = str(bundle.bundle_id.run_id)
        self._connection.execute(
            """
            INSERT INTO bundles(
                run_id, bundle_id, sequence, risk, status, diff_token_count,
                input_tokens, output_tokens, latency_ms, cost_usd,
                error_code, error_message
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id, bundle_id) DO UPDATE SET
                status = excluded.status,
                diff_token_count = excluded.diff_token_count,
                input_tokens = excluded.input_tokens,
                output_tokens = excluded.output_tokens,
                latency_ms = excluded.latency_ms,
                cost_usd = excluded.cost_usd,
                error_code = excluded.error_code,
                error_message = excluded.error_message
            """,
            (
                run_id,
                str(bundle.bundle_id),
                bundle.bundle_id.sequence,
                bundle.risk.value,
                outcome.status.value,
                bundle.diff_tokens,
                outcome.input_tokens,
                outcome.output_tokens,
                outcome.latency_ms,
                str(outcome.cost_usd),
                outcome.error_code,
                outcome.error_message,
            ),
        )
        rows = [
            (
                run_id,
                str(bundle.bundle_id),
                result.path.value,
                result.decision.value,
                result.reason.value if result.reason else None,
            )
            for result in file_results
            if result.path.value in bundle.paths
        ]
        self._connection.execute(
            "DELETE FROM bundle_files WHERE run_id = ? AND bundle_id = ?",
            (run_id, str(bundle.bundle_id)),
        )
        self._connection.executemany(
            "INSERT INTO bundle_files VALUES(?,?,?,?,?)",
            rows,
        )

    def save_filtered_files(
        self, run_id: RunId, file_results: tuple[FileFilterResult, ...]
    ) -> None:
        """レビュー単位に属さないファイル判定を登録する."""
        self._connection.executemany(
            "INSERT OR REPLACE INTO filtered_files VALUES(?,?,?,?)",
            [
                (
                    str(run_id),
                    result.path.value,
                    result.decision.value,
                    result.reason.value if result.reason else None,
                )
                for result in file_results
            ],
        )
