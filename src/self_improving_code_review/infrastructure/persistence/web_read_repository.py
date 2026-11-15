"""ローカル Web 画面から参照する SQLite 投影."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from self_improving_code_review.infrastructure.persistence.web_file_completion import (
    read_file_completion,
)

Row = dict[str, Any]
PAGE_SIZE = 20
DETAIL_LIMIT = 200


class WebReadRepository:
    """運用データベースを読み取り専用で開き、画面用の列を取得する."""

    def __init__(self, database: Path) -> None:
        """読み取り対象のファイルを固定する."""
        self.database = database.resolve()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """同一画面の複数クエリを一つの読み取りスナップショットにまとめる."""
        connection = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            yield connection
        finally:
            connection.close()

    def validate(self) -> None:
        """必要なテーブルを開けることを確認する."""
        with self.connection() as connection:
            connection.execute("SELECT run_id FROM review_runs LIMIT 1").fetchone()
            connection.execute("SELECT finding_id FROM findings LIMIT 1").fetchone()
            connection.execute("SELECT bundle_id FROM bundles LIMIT 1").fetchone()
            connection.execute("SELECT path FROM filtered_files LIMIT 1").fetchone()

    def overview(self) -> Row:
        """全運用履歴の集計と最近の実行を取得する."""
        with self.connection() as connection:
            counts = dict(
                connection.execute("""
                SELECT COUNT(*) AS runs,
                       COUNT(CASE WHEN status = 'success' THEN 1 END) AS successes,
                       COUNT(CASE WHEN status IN ('failed', 'blocked', 'partial-success')
                                  THEN 1 END) AS issues,
                       COALESCE(SUM(json_extract(metrics, '$.input_tokens')), 0) AS input_tokens,
                       COALESCE(SUM(json_extract(metrics, '$.output_tokens')), 0) AS output_tokens
                FROM (SELECT status, CASE WHEN json_valid(metrics_json) THEN metrics_json
                                          ELSE '{}' END AS metrics FROM review_runs)
            """).fetchone()
            )
            severities = {
                row["severity"]: row["count"]
                for row in connection.execute("""
                    SELECT severity, COUNT(*) AS count FROM findings
                    WHERE is_main = 1 GROUP BY severity
                """)
            }
            recent = connection.execute(
                """
                SELECT run_id, project_id, mode, status, source_revision, model, prompt_version,
                       started_at, finished_at, metrics_json
                FROM review_runs ORDER BY started_at DESC, run_id DESC LIMIT 6
                """
            )
            return {**counts, "severities": severities, "recent": [_run(row) for row in recent]}

    def runs(self, query: str, status: str, page: int) -> tuple[list[Row], int]:
        """検索条件に該当するページと全件数を取得する."""
        params = {
            "status": status,
            "query": query,
            "limit": PAGE_SIZE,
            "offset": (page - 1) * PAGE_SIZE,
        }
        with self.connection() as connection:
            total = connection.execute(
                """SELECT COUNT(*) FROM review_runs
                WHERE (:status = '' OR status = :status) AND
                      (:query = '' OR instr(lower(project_id || ' ' || model || ' ' ||
                        coalesce(source_revision, '') || ' ' || run_id), lower(:query)) > 0)
                """,
                params,
            )
            rows = connection.execute(
                """SELECT run_id, project_id, mode, status, source_revision, model, prompt_version,
                          started_at, finished_at, metrics_json
                FROM review_runs
                WHERE (:status = '' OR status = :status) AND
                      (:query = '' OR instr(lower(project_id || ' ' || model || ' ' ||
                        coalesce(source_revision, '') || ' ' || run_id), lower(:query)) > 0)
                ORDER BY started_at DESC, run_id DESC LIMIT :limit OFFSET :offset
                """,
                params,
            )
            return [_run(row) for row in rows], int(total.fetchone()[0])

    def detail(self, run_id: str, *, file_page: int = 1) -> Row | None:
        """一つの実行と報告内容、単位、除外ファイル、SCM 投稿状態を取得する."""
        with self.connection() as connection:
            header = connection.execute(
                """SELECT run_id, project_id, mode, status, source_revision, model, prompt_version,
                          started_at, finished_at, metrics_json
                FROM review_runs WHERE run_id = ?""",
                (run_id,),
            ).fetchone()
            if header is None:
                return None
            findings = _rows(
                connection,
                """
                SELECT finding_id AS id, path AS file, start_line, end_line, severity, title,
                       description, rule_id, suggestion, is_main, reflection_status
                FROM findings WHERE run_id = ?
                ORDER BY is_main DESC,
                    CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                                  WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END,
                    path, start_line, finding_id LIMIT ?
            """,
                run_id,
            )
            bundles = _rows(
                connection,
                """
                SELECT bundle_id, status, input_tokens, output_tokens, latency_ms, error_code
                FROM bundles WHERE run_id = ? ORDER BY sequence LIMIT ?
            """,
                run_id,
            )
            return {
                **_run(header),
                "findings": findings,
                "bundles": bundles,
                "file_completion": read_file_completion(connection, run_id, file_page),
                "publications": _publications(connection, run_id),
            }


def _run(row: sqlite3.Row) -> Row:
    """保存済み測定値を画面向けに展開する."""
    data = dict(row)
    raw = data.pop("metrics_json", None)
    parsed = json.loads(raw) if isinstance(raw, str) and raw else None
    data["metrics"] = parsed if isinstance(parsed, dict) else {}
    return data


def _rows(connection: sqlite3.Connection, sql: str, run_id: str) -> list[Row]:
    """詳細画面の表示上限を検出できる件数だけ取得する."""
    return [dict(row) for row in connection.execute(sql, (run_id, DETAIL_LIMIT + 1))]


def _publications(connection: sqlite3.Connection, run_id: str) -> list[Row]:
    """SCM 表が存在する場合に公開記録を取得する."""
    exists = connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'scm_publications'"
    ).fetchone()
    if exists is None:
        return []
    return _rows(
        connection,
        """
        SELECT repository_external_id, change_external_id, kind, status, updated_at
        FROM scm_publications WHERE run_id = ? ORDER BY updated_at DESC LIMIT ?
    """,
        run_id,
    )
