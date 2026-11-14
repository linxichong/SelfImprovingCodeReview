"""復元済み SQLite バックアップの読取り専用検査."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from self_improving_code_review.application.ports.backup import BackupInspection
from self_improving_code_review.domain.common.hashing import hash_lines, hash_text
from self_improving_code_review.domain.common.values import ProjectId

__all__ = ["SqliteBackupInspector"]

_CHECK_NAMES = (
    "database_readable",
    "required_schema",
    "integrity",
    "foreign_keys",
    "project_membership",
    "memory_content_hashes",
    "memory_snapshot_hashes",
    "rule_snapshot_hashes",
)
_FAILURE_CODES = {
    "database_readable": "DATABASE_UNREADABLE",
    "required_schema": "REQUIRED_SCHEMA_MISSING",
    "integrity": "INTEGRITY_CHECK_FAILED",
    "foreign_keys": "FOREIGN_KEY_CHECK_FAILED",
    "project_membership": "PROJECT_MEMBERSHIP_INVALID",
    "memory_content_hashes": "MEMORY_CONTENT_HASH_MISMATCH",
    "memory_snapshot_hashes": "MEMORY_SNAPSHOT_HASH_MISMATCH",
    "rule_snapshot_hashes": "RULE_SNAPSHOT_HASH_MISMATCH",
}
_REQUIRED_TABLES = frozenset(
    {
        "projects",
        "project_ground_truth_sets",
        "project_scm_changes",
        "review_runs",
        "rule_snapshots",
        "rule_snapshot_items",
        "memories",
        "memory_versions",
        "memory_evidence",
        "memory_snapshots",
        "memory_snapshot_items",
        "memory_extraction_runs",
        "memory_conflicts",
        "memory_revalidations",
        "ground_truth_sets",
        "evaluation_runs",
        "scm_changes",
    }
)
_PROJECT_TABLES = (
    "review_runs",
    "memories",
    "memory_extraction_runs",
    "memory_snapshots",
    "memory_conflicts",
    "memory_revalidations",
)


class SqliteBackupInspector:
    """SQLite を変更せずに整合性、所属、保存済みハッシュを検査する."""

    def __init__(self, *, busy_timeout_ms: int = 5000) -> None:
        """読取り接続のロック待機上限を保持する."""
        self._busy_timeout_ms = busy_timeout_ms

    def inspect(self, database_path: Path, project_id: ProjectId) -> BackupInspection:
        """指定 DB を読取り専用で開き、公開可能な判定値だけを返す."""
        try:
            with closing(self._connect(database_path)) as connection:
                tables = self._tables(connection)
                if not _REQUIRED_TABLES.issubset(tables):
                    return _schema_failure_report()
                checks = {
                    "database_readable": True,
                    "required_schema": True,
                    "integrity": self._integrity_is_valid(connection),
                    "foreign_keys": self._foreign_keys_are_valid(connection),
                    "project_membership": self._project_membership_is_valid(connection, project_id),
                    "memory_content_hashes": self._memory_hashes_are_valid(connection),
                    "memory_snapshot_hashes": self._snapshot_hashes_are_valid(connection),
                    "rule_snapshot_hashes": self._rule_hashes_are_valid(connection),
                }
                counts = self._counts(connection, project_id)
        except sqlite3.Error, OSError, ValueError:
            return _unreadable_report()
        return _report(checks, counts)

    def _connect(self, database_path: Path) -> sqlite3.Connection:
        """URI 読取り専用モードで接続し、書込みを二重に禁止する."""
        uri = f"{database_path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=self._busy_timeout_ms / 1000)
        connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA query_only = ON")
        return connection

    def _tables(self, connection: sqlite3.Connection) -> frozenset[str]:
        """利用者テーブル名の集合を返す."""
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return frozenset(str(row[0]) for row in rows)

    def _integrity_is_valid(self, connection: sqlite3.Connection) -> bool:
        """SQLite 全体の整合性検査が ok だけを返すか確認する."""
        rows = connection.execute("PRAGMA integrity_check").fetchall()
        return bool(rows) and all(str(row[0]).lower() == "ok" for row in rows)

    def _foreign_keys_are_valid(self, connection: sqlite3.Connection) -> bool:
        """外部キー違反が一件もないことを確認する."""
        return connection.execute("PRAGMA foreign_key_check").fetchone() is None

    def _project_membership_is_valid(
        self,
        connection: sqlite3.Connection,
        project_id: ProjectId,
    ) -> bool:
        """対象プロジェクトの存在と全集約の明示所属を検証する."""
        project = connection.execute(
            "SELECT 1 FROM projects WHERE project_id = ?", (str(project_id),)
        ).fetchone()
        if project is None:
            return False
        for table in _PROJECT_TABLES:
            row = connection.execute(
                # テーブル名は外部入力ではなく、固定された _PROJECT_TABLES だけから選択する。
                f"SELECT 1 FROM {table} AS source LEFT JOIN projects AS target "  # nosec B608
                "ON target.project_id = source.project_id "
                "WHERE target.project_id IS NULL LIMIT 1"
            ).fetchone()
            if row is not None:
                return False
        if (
            connection.execute(
                "SELECT 1 FROM ground_truth_sets AS source "
                "LEFT JOIN project_ground_truth_sets AS assigned "
                "ON assigned.set_id = source.set_id "
                "WHERE assigned.set_id IS NULL LIMIT 1"
            ).fetchone()
            is not None
        ):
            return False
        if (
            connection.execute(
                "SELECT 1 FROM scm_changes AS source LEFT JOIN project_scm_changes AS assigned "
                "ON assigned.provider = source.provider "
                "AND assigned.repository_external_id = source.repository_external_id "
                "AND assigned.change_external_id = source.change_external_id "
                "WHERE assigned.change_external_id IS NULL LIMIT 1"
            ).fetchone()
            is not None
        ):
            return False
        return (
            connection.execute(
                "SELECT 1 FROM memory_snapshots AS snapshot "
                "JOIN memory_snapshot_items AS item USING(memory_snapshot_id) "
                "JOIN memories AS memory USING(memory_id) "
                "WHERE snapshot.project_id <> memory.project_id LIMIT 1"
            ).fetchone()
            is None
        )

    def _memory_hashes_are_valid(self, connection: sqlite3.Connection) -> bool:
        """全メモリ版の構造化内容を再計算して保存値と照合する."""
        rows = connection.execute(
            "SELECT category, scope_json, fact, review_instruction, polarity, confidence, "
            "evidence_summary, content_hash, framework_version_constraints_json "
            "FROM memory_versions"
        ).fetchall()
        return all(_memory_row_hash_is_valid(row) for row in rows)

    def _snapshot_hashes_are_valid(self, connection: sqlite3.Connection) -> bool:
        """全メモリスナップショットの集合ハッシュと項目ハッシュを再検証する."""
        snapshots = connection.execute(
            "SELECT memory_snapshot_id, snapshot_hash FROM memory_snapshots"
        ).fetchall()
        for snapshot_id, stored_hash in snapshots:
            items = connection.execute(
                "SELECT item.memory_id, item.version, item.content_hash, version.content_hash "
                "FROM memory_snapshot_items AS item "
                "JOIN memory_versions AS version USING(memory_id, version) "
                "WHERE item.memory_snapshot_id = ? ORDER BY item.memory_id, item.version",
                (str(snapshot_id),),
            ).fetchall()
            if any(str(item[2]) != str(item[3]) for item in items):
                return False
            calculated = hash_lines(f"{item[0]}:{item[1]}:{item[2]}" for item in items)
            if calculated != str(stored_hash):
                return False
        return True

    def _rule_hashes_are_valid(self, connection: sqlite3.Connection) -> bool:
        """ルール集合ハッシュと実行に固定した参照ハッシュを再検証する."""
        snapshots = connection.execute(
            "SELECT rule_snapshot_id, rule_set_hash FROM rule_snapshots"
        ).fetchall()
        for snapshot_id, stored_hash in snapshots:
            items = connection.execute(
                "SELECT path, content_hash FROM rule_snapshot_items "
                "WHERE rule_snapshot_id = ? ORDER BY path",
                (str(snapshot_id),),
            ).fetchall()
            if hash_lines(f"{item[0]}:{item[1]}" for item in items) != str(stored_hash):
                return False
        mismatch = connection.execute(
            "SELECT 1 FROM review_runs AS run JOIN rule_snapshots AS snapshot "
            "ON snapshot.rule_snapshot_id = run.rule_snapshot_id "
            "WHERE run.rule_set_hash <> snapshot.rule_set_hash LIMIT 1"
        ).fetchone()
        return mismatch is None

    def _counts(
        self,
        connection: sqlite3.Connection,
        project_id: ProjectId,
    ) -> tuple[tuple[str, int], ...]:
        """対象プロジェクトの安全な集約件数を安定順で返す."""
        project = str(project_id)
        queries = (
            (
                "ground_truth_sets",
                "SELECT COUNT(*) FROM project_ground_truth_sets WHERE project_id = ?",
            ),
            ("memories", "SELECT COUNT(*) FROM memories WHERE project_id = ?"),
            ("memory_snapshots", "SELECT COUNT(*) FROM memory_snapshots WHERE project_id = ?"),
            ("review_runs", "SELECT COUNT(*) FROM review_runs WHERE project_id = ?"),
            ("scm_changes", "SELECT COUNT(*) FROM project_scm_changes WHERE project_id = ?"),
        )
        return tuple(
            (name, int(connection.execute(query, (project,)).fetchone()[0]))
            for name, query in queries
        )


def _memory_row_hash_is_valid(row: tuple[object, ...]) -> bool:
    """メモリ版一行の正規化文書ハッシュが保存値と一致するか返す."""
    try:
        scope = json.loads(str(row[1]))
        constraints = json.loads(str(row[8]))
        if not isinstance(scope, dict) or not isinstance(constraints, list):
            return False
        document: dict[str, object] = {
            "category": str(row[0]),
            "scope": scope,
            "fact": str(row[2]),
            "review_instruction": str(row[3]),
            "polarity": str(row[4]),
            "confidence": round(float(str(row[5])), 4),
            "evidence_summary": str(row[6]),
        }
        if constraints:
            document["framework_version_constraints"] = constraints
        calculated = hash_text(
            json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        )
        return calculated == str(row[7])
    except json.JSONDecodeError, TypeError, ValueError:
        return False


def _report(
    checks: dict[str, bool],
    counts: tuple[tuple[str, int], ...],
) -> BackupInspection:
    """検査対応表から安定順の不変な検査結果を作る."""
    failures = tuple(_FAILURE_CODES[name] for name in _CHECK_NAMES if not checks[name])
    return BackupInspection(tuple((name, checks[name]) for name in _CHECK_NAMES), counts, failures)


def _schema_failure_report() -> BackupInspection:
    """必須スキーマ欠落時に後続 SQL を実行しない安全な結果を返す."""
    checks = {name: name == "database_readable" for name in _CHECK_NAMES}
    checks["required_schema"] = False
    return _report(checks, ())


def _unreadable_report() -> BackupInspection:
    """DB の生エラーを公開しない読取り失敗結果を返す."""
    checks = dict.fromkeys(_CHECK_NAMES, False)
    return BackupInspection(tuple(checks.items()), (), ("DATABASE_UNREADABLE",))
