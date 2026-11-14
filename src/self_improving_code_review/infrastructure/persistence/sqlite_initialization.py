"""既存の順序と条件でSQLite初期化と前進移行を適用する."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from self_improving_code_review.domain.common.errors import (
    DatabaseError,
)
from self_improving_code_review.infrastructure.persistence.acceptance_change_schema import (
    ACCEPTANCE_CHANGE_SCHEMA,
)
from self_improving_code_review.infrastructure.persistence.acceptance_decision_schema import (
    ACCEPTANCE_DECISION_SCHEMA,
)
from self_improving_code_review.infrastructure.persistence.acceptance_publication_schema import (
    ACCEPTANCE_PUBLICATION_SCHEMA,
)
from self_improving_code_review.infrastructure.persistence.acceptance_schema import (
    ACCEPTANCE_SCHEMA,
)
from self_improving_code_review.infrastructure.persistence.governance_schema import (
    GOVERNANCE_SCHEMA,
)
from self_improving_code_review.infrastructure.persistence.memory_governance_schema import (
    MEMORY_GOVERNANCE_SCHEMA,
)
from self_improving_code_review.infrastructure.persistence.memory_schema import MEMORY_SCHEMA
from self_improving_code_review.infrastructure.persistence.review_schema import (
    M3_SCHEMA,
    M4_SCHEMA,
    REVIEW_SCHEMA,
)
from self_improving_code_review.infrastructure.persistence.reviewer_schema import (
    REVIEWER_LEASE_SCHEMA,
)
from self_improving_code_review.messages import Messages


def initialize_database(database_path: Path, *, busy_timeout_ms: int = 5000) -> None:
    """M1 から M5 プロジェクト統制までのスキーマを前進適用する."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(sqlite3.connect(database_path)) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
            connection.executescript(REVIEW_SCHEMA)
            connection.executescript(MEMORY_SCHEMA)
            _apply_memory_snapshot_run_migration(connection)
            _apply_evaluation_adjudication_migration(connection)
            _apply_ground_truth_case_migration(connection)
            _apply_m3_migration(connection)
            _apply_m4_migration(connection)
            _apply_m5_project_migration(connection)
            _apply_m5_memory_migration(connection)
            _apply_m5_reviewer_lease_migration(connection)
            # 廃止した機能の公開済み移行と既存の監査記録を保持する。
            connection.executescript(ACCEPTANCE_SCHEMA)
            connection.executescript(ACCEPTANCE_DECISION_SCHEMA)
            connection.executescript(ACCEPTANCE_CHANGE_SCHEMA)
            connection.executemany(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(?, ?)",
                [
                    (1, datetime.now(UTC).isoformat()),
                    (2, datetime.now(UTC).isoformat()),
                    (3, datetime.now(UTC).isoformat()),
                    (4, datetime.now(UTC).isoformat()),
                    (5, datetime.now(UTC).isoformat()),
                    (6, datetime.now(UTC).isoformat()),
                    (7, datetime.now(UTC).isoformat()),
                    (8, datetime.now(UTC).isoformat()),
                    (9, datetime.now(UTC).isoformat()),
                    (10, datetime.now(UTC).isoformat()),
                    (11, datetime.now(UTC).isoformat()),
                    (12, datetime.now(UTC).isoformat()),
                    (13, datetime.now(UTC).isoformat()),
                    (14, datetime.now(UTC).isoformat()),
                ],
            )
            connection.commit()
            if (
                connection.execute("SELECT 1 FROM schema_migrations WHERE version=15").fetchone()
                is None
            ):
                connection.executescript(ACCEPTANCE_PUBLICATION_SCHEMA)
    except sqlite3.Error as exc:
        message = Messages.DATABASE_INITIALIZATION_FAILED
        raise DatabaseError(message) from exc


def _apply_memory_snapshot_run_migration(connection: sqlite3.Connection) -> None:
    """既存 DB のレビュー実行へメモリスナップショット参照を追加する."""
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(review_runs)").fetchall()
    }
    if "memory_snapshot_id" not in columns:
        connection.execute(
            "ALTER TABLE review_runs ADD COLUMN memory_snapshot_id TEXT "
            "REFERENCES memory_snapshots(memory_snapshot_id) ON DELETE RESTRICT"
        )


def _apply_evaluation_adjudication_migration(connection: sqlite3.Connection) -> None:
    """既存 DB の評価照合へ人工裁定の監査列を追加する."""
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(evaluation_matches)").fetchall()
    }
    additions = (
        ("adjudication_token", "TEXT"),
        ("adjudication_reason", "TEXT"),
        ("adjudicated_at", "TEXT"),
        ("is_superseded", "INTEGER NOT NULL DEFAULT 0"),
    )
    for name, data_type in additions:
        if name not in columns:
            connection.execute(f"ALTER TABLE evaluation_matches ADD COLUMN {name} {data_type}")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS evaluation_matches_adjudication_token "
        "ON evaluation_matches(adjudication_token) WHERE adjudication_token IS NOT NULL"
    )


def _apply_ground_truth_case_migration(connection: sqlite3.Connection) -> None:
    """既存の正解問題から評価ケースを補完する."""
    connection.execute(
        "INSERT OR IGNORE INTO ground_truth_cases("
        "set_id, sequence, change_ref, created_by, verified_by, approved_by, status) "
        "SELECT set_id, ROW_NUMBER() OVER (PARTITION BY set_id ORDER BY change_ref), "
        "change_ref, MIN(created_by), MIN(verified_by), MIN(approved_by), "
        "CASE WHEN MIN(status) = 'published' THEN 'published' ELSE MIN(status) END "
        "FROM ground_truth_issues GROUP BY set_id, change_ref"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS evaluation_review_runs ("
        "evaluation_id TEXT NOT NULL, run_id TEXT NOT NULL, "
        "PRIMARY KEY (evaluation_id, run_id), "
        "FOREIGN KEY (evaluation_id) REFERENCES evaluation_runs(evaluation_id) ON DELETE RESTRICT, "
        "FOREIGN KEY (run_id) REFERENCES review_runs(run_id) ON DELETE RESTRICT)"
    )


def _apply_m3_migration(connection: sqlite3.Connection) -> None:
    """既存の M1/M2 データベースへ M3 の追加列と監査表を作成する."""
    _add_columns(
        connection,
        "review_runs",
        (
            ("pipeline_config_hash", "TEXT"),
            ("bundle_plan_hash", "TEXT"),
            ("context_source_hash", "TEXT"),
            ("agent_prompt_version", "TEXT"),
            ("agent_prompt_hash", "TEXT"),
            ("reflection_prompt_version", "TEXT"),
            ("reflection_prompt_hash", "TEXT"),
            ("resume_count", "INTEGER NOT NULL DEFAULT 0 CHECK (resume_count >= 0)"),
        ),
    )
    _add_columns(
        connection,
        "bundles",
        (
            (
                "reviewer_type",
                "TEXT CHECK (reviewer_type IN ('general', 'security', 'test'))",
            ),
            ("profile_reason_code", "TEXT"),
            ("bundle_input_hash", "TEXT"),
            ("bundle_result_hash", "TEXT"),
        ),
    )
    _add_columns(
        connection,
        "llm_calls",
        (
            (
                "call_kind",
                "TEXT NOT NULL DEFAULT 'review' "
                "CHECK (call_kind IN ('review', 'review-turn', 'reflection'))",
            ),
            ("turn_index", "INTEGER NOT NULL DEFAULT 1 CHECK (turn_index >= 1)"),
        ),
    )
    connection.execute("DROP INDEX IF EXISTS review_runs_completed_idempotency")
    connection.execute(
        "CREATE UNIQUE INDEX review_runs_completed_idempotency "
        "ON review_runs(idempotency_key) "
        "WHERE status IN ('running', 'success')"
    )
    connection.executescript(M3_SCHEMA)


def _apply_m4_migration(connection: sqlite3.Connection) -> None:
    """既存の M1 から M3 のデータベースへ M4 の SCM 監査表を作成する."""
    connection.executescript(M4_SCHEMA)


def _apply_m5_project_migration(connection: sqlite3.Connection) -> None:
    """既存データベースへ M5 のプロジェクト分離表を作成する."""
    connection.executescript(GOVERNANCE_SCHEMA)


def _apply_m5_memory_migration(connection: sqlite3.Connection) -> None:
    """既存メモリへ期限列を補完し、M5 統制表を作成する."""
    _add_columns(
        connection,
        "memory_versions",
        (
            ("valid_from", "TEXT"),
            ("review_after", "TEXT"),
            ("framework_version_constraints_json", "TEXT NOT NULL DEFAULT '[]'"),
        ),
    )
    _add_columns(connection, "memories", (("last_confirmed_at", "TEXT"),))
    connection.execute(
        "UPDATE memory_versions SET valid_from = created_at WHERE valid_from IS NULL"
    )
    connection.execute(
        "UPDATE memory_versions SET review_after = "
        "strftime('%Y-%m-%dT%H:%M:%f+00:00', created_at, '+180 days') "
        "WHERE review_after IS NULL"
    )
    connection.executescript(MEMORY_GOVERNANCE_SCHEMA)


def _apply_m5_reviewer_lease_migration(connection: sqlite3.Connection) -> None:
    """既存データベースへ担当監査表と実行リース表を作成する."""
    connection.executescript(REVIEWER_LEASE_SCHEMA)


def _add_columns(
    connection: sqlite3.Connection,
    table: str,
    additions: tuple[tuple[str, str], ...],
) -> None:
    """存在しない列だけを既存テーブルへ追加する."""
    columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in additions:
        if name not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
