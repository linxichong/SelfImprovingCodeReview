from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from self_improving_code_review.domain.common.hashing import hash_lines, hash_text
from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.infrastructure.persistence.backup_inspector import (
    SqliteBackupInspector,
    _memory_row_hash_is_valid,
)
from self_improving_code_review.infrastructure.persistence.sqlite import initialize_database

PROJECT_ID = ProjectId("project-a")


def _database(tmp_path: Path) -> Path:
    """M5 スキーマと検査対象プロジェクトを持つ DB を返す。"""
    database = tmp_path / "backup.db"
    initialize_database(database)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO projects VALUES(?,?,?,?,?,?,?)",
            (
                str(PROJECT_ID),
                "Project A",
                "a" * 64,
                "active",
                1,
                "2026-01-02T03:04:05+00:00",
                "2026-01-02T03:04:05+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO rule_snapshots VALUES(?,?,?,?)",
            ("rules-empty", None, hash_lines(()), "2026-01-02T03:04:05+00:00"),
        )
        connection.commit()
    return database


def test_valid_backup_is_verified_without_modifying_database(tmp_path: Path) -> None:
    database = _database(tmp_path)
    before = database.stat().st_mtime_ns

    result = SqliteBackupInspector(busy_timeout_ms=1).inspect(database, PROJECT_ID)

    assert result.is_valid is True
    assert result.failure_codes == ()
    assert dict(result.checks) == {
        "database_readable": True,
        "required_schema": True,
        "integrity": True,
        "foreign_keys": True,
        "project_membership": True,
        "memory_content_hashes": True,
        "memory_snapshot_hashes": True,
        "rule_snapshot_hashes": True,
    }
    assert dict(result.counts) == {
        "ground_truth_sets": 0,
        "memories": 0,
        "memory_snapshots": 0,
        "review_runs": 0,
        "scm_changes": 0,
    }
    assert database.stat().st_mtime_ns == before


def test_missing_schema_and_unreadable_database_return_safe_codes(tmp_path: Path) -> None:
    incomplete = tmp_path / "incomplete.db"
    with closing(sqlite3.connect(incomplete)) as connection:
        connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY)")
    corrupted = tmp_path / "corrupted.db"
    corrupted.write_text("not a database", encoding="utf-8")

    schema_result = SqliteBackupInspector().inspect(incomplete, PROJECT_ID)
    unreadable_result = SqliteBackupInspector().inspect(corrupted, PROJECT_ID)

    assert "REQUIRED_SCHEMA_MISSING" in schema_result.failure_codes
    assert schema_result.is_valid is False
    assert unreadable_result.failure_codes == ("DATABASE_UNREADABLE",)


def test_unknown_project_and_unassigned_ground_truth_are_rejected(tmp_path: Path) -> None:
    database = _database(tmp_path)
    unknown = SqliteBackupInspector().inspect(database, ProjectId("project-b"))
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO ground_truth_sets VALUES(?,?,?,?,?,?,?)",
            ("set-1", "test", 1, "b" * 64, "draft", None, None),
        )
        connection.commit()
    unassigned = SqliteBackupInspector().inspect(database, PROJECT_ID)

    assert "PROJECT_MEMBERSHIP_INVALID" in unknown.failure_codes
    assert "PROJECT_MEMBERSHIP_INVALID" in unassigned.failure_codes


@pytest.mark.parametrize(
    ("mutation", "failure_code"),
    [
        ("memory", "MEMORY_CONTENT_HASH_MISMATCH"),
        ("snapshot", "MEMORY_SNAPSHOT_HASH_MISMATCH"),
        ("rule", "RULE_SNAPSHOT_HASH_MISMATCH"),
    ],
)
def test_saved_hash_mismatch_is_reported(
    tmp_path: Path,
    mutation: str,
    failure_code: str,
) -> None:
    database = _database(tmp_path)
    with closing(sqlite3.connect(database)) as connection:
        if mutation == "memory":
            _insert_memory_version(connection, content_hash="0" * 64)
        elif mutation == "snapshot":
            connection.execute(
                "INSERT INTO memory_snapshots VALUES(?,?,?,?)",
                ("snapshot-1", str(PROJECT_ID), "0" * 64, "2026-01-02T03:04:05+00:00"),
            )
        else:
            connection.execute(
                "INSERT INTO rule_snapshots VALUES(?,?,?,?)",
                ("rules-bad", None, "0" * 64, "2026-01-02T03:04:05+00:00"),
            )
        connection.commit()

    result = SqliteBackupInspector().inspect(database, PROJECT_ID)

    assert failure_code in result.failure_codes


def test_memory_and_snapshot_hashes_accept_normalized_saved_content(tmp_path: Path) -> None:
    database = _database(tmp_path)
    content_hash = _memory_hash(())
    with closing(sqlite3.connect(database)) as connection:
        _insert_memory_version(connection, content_hash=content_hash)
        connection.execute(
            "INSERT INTO memories("
            "memory_id, project_id, current_version, current_status, lock_version, "
            "created_at, updated_at"
            ") VALUES(?,?,?,?,?,?,?)",
            (
                "memory-1",
                str(PROJECT_ID),
                1,
                "active",
                1,
                "2026-01-02T03:04:05+00:00",
                "2026-01-02T03:04:05+00:00",
            ),
        )
        snapshot_hash = hash_lines((f"memory-1:1:{content_hash}",))
        connection.execute(
            "INSERT INTO memory_snapshots VALUES(?,?,?,?)",
            ("snapshot-1", str(PROJECT_ID), snapshot_hash, "2026-01-02T03:04:05+00:00"),
        )
        connection.execute(
            "INSERT INTO memory_snapshot_items VALUES(?,?,?,?)",
            ("snapshot-1", "memory-1", 1, content_hash),
        )
        connection.commit()

    result = SqliteBackupInspector().inspect(database, PROJECT_ID)

    assert result.is_valid is True
    assert dict(result.counts)["memories"] == 1
    assert dict(result.counts)["memory_snapshots"] == 1


def test_malformed_memory_document_is_a_hash_failure() -> None:
    row = ("style", "[]", "fact", "instruction", "reinforce", 0.8, "evidence", "x", "{}")
    assert _memory_row_hash_is_valid(row) is False


def test_orphan_project_and_unassigned_scm_are_rejected(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO memory_snapshots VALUES(?,?,?,?)",
            ("orphan", "project-b", hash_lines(()), "2026-01-02T03:04:05+00:00"),
        )
        connection.commit()
    orphan = SqliteBackupInspector().inspect(database, PROJECT_ID)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("DELETE FROM memory_snapshots WHERE memory_snapshot_id = 'orphan'")
        connection.execute(
            "INSERT INTO scm_changes VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "github",
                "owner/repository",
                "1",
                "a" * 40,
                "b" * 40,
                "open",
                0,
                "owner/repository",
                None,
                "2026-01-02T03:04:05+00:00",
                "2026-01-02T03:04:05+00:00",
            ),
        )
        connection.commit()
    scm = SqliteBackupInspector().inspect(database, PROJECT_ID)

    assert "PROJECT_MEMBERSHIP_INVALID" in orphan.failure_codes
    assert "PROJECT_MEMBERSHIP_INVALID" in scm.failure_codes


def test_snapshot_item_and_run_rule_hash_mismatch_are_rejected(tmp_path: Path) -> None:
    database = _database(tmp_path)
    content_hash = _memory_hash(())
    with closing(sqlite3.connect(database)) as connection:
        _insert_memory_version(connection, content_hash=content_hash)
        connection.execute(
            "INSERT INTO memories("
            "memory_id, project_id, current_version, current_status, lock_version, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (
                "memory-1",
                str(PROJECT_ID),
                1,
                "active",
                1,
                "2026-01-02T03:04:05+00:00",
                "2026-01-02T03:04:05+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO memory_snapshots VALUES(?,?,?,?)",
            ("snapshot-1", str(PROJECT_ID), "0" * 64, "2026-01-02T03:04:05+00:00"),
        )
        connection.execute(
            "INSERT INTO memory_snapshot_items VALUES(?,?,?,?)",
            ("snapshot-1", "memory-1", 1, "1" * 64),
        )
        connection.commit()
    snapshot = SqliteBackupInspector().inspect(database, PROJECT_ID)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("DELETE FROM memory_snapshot_items")
        connection.execute("DELETE FROM memory_snapshots")
        connection.execute(
            "INSERT INTO review_runs("
            "run_id, project_id, mode, status, model, package_version, config_hash, "
            "prompt_version, prompt_hash, rule_snapshot_id, rule_set_hash, "
            "secret_engine_version, idempotency_key, started_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "12345678-1234-4234-9234-123456789abc",
                str(PROJECT_ID),
                "baseline",
                "running",
                "test",
                "1",
                "a" * 64,
                "v1",
                "b" * 64,
                "rules-empty",
                "f" * 64,
                "test",
                "key",
                "2026-01-02T03:04:05+00:00",
            ),
        )
        connection.commit()
    rule = SqliteBackupInspector().inspect(database, PROJECT_ID)

    assert "MEMORY_SNAPSHOT_HASH_MISMATCH" in snapshot.failure_codes
    assert "RULE_SNAPSHOT_HASH_MISMATCH" in rule.failure_codes


def test_memory_hash_supports_constraints_and_rejects_invalid_json() -> None:
    constraints = ["django>=5"]
    valid_row = (
        "style",
        _scope_json(),
        "fact",
        "instruction",
        "reinforce",
        0.8,
        "evidence",
        _memory_hash(tuple(constraints)),
        json.dumps(constraints),
    )
    invalid_row = (
        "style",
        "{",
        "fact",
        "instruction",
        "reinforce",
        0.8,
        "evidence",
        "x",
        "[]",
    )

    assert _memory_row_hash_is_valid(valid_row) is True
    assert _memory_row_hash_is_valid(invalid_row) is False


def _insert_memory_version(connection: sqlite3.Connection, *, content_hash: str) -> None:
    """固定メモリ版を検査用 DB へ登録する。"""
    connection.execute(
        "INSERT INTO memory_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "memory-1",
            1,
            "style",
            _scope_json(),
            "fact",
            "instruction",
            "reinforce",
            0.8,
            "evidence",
            content_hash,
            "tanaka",
            "2026-01-02T03:04:05+00:00",
            None,
            None,
            "[]",
        ),
    )


def _scope_json() -> str:
    """空の適用範囲を安定 JSON で返す。"""
    return json.dumps(
        {
            name: []
            for name in ("languages", "frameworks", "paths", "symbols", "keywords", "risk_tags")
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _memory_hash(constraints: tuple[str, ...]) -> str:
    """検査用メモリ内容の正規化ハッシュを返す。"""
    document: dict[str, object] = {
        "category": "style",
        "scope": json.loads(_scope_json()),
        "fact": "fact",
        "review_instruction": "instruction",
        "polarity": "reinforce",
        "confidence": 0.8,
        "evidence_summary": "evidence",
    }
    if constraints:
        document["framework_version_constraints"] = list(constraints)
    return hash_text(
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )
