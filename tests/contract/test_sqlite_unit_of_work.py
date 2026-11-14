from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from self_improving_code_review.application.ports.run_lease import RunLeaseIdentity
from self_improving_code_review.application.ports.unit_of_work import LlmCallRecord
from self_improving_code_review.application.review.models import (
    ReviewMetrics,
    RunRecord,
    RunVersions,
)
from self_improving_code_review.domain.common.enums import ReviewerType, ReviewMode, RunStatus
from self_improving_code_review.domain.common.errors import (
    ConcurrencyConflictError,
    DatabaseError,
)
from self_improving_code_review.domain.common.values import BundleId, ProjectId, RunId
from self_improving_code_review.domain.memory.models import MemorySnapshot
from self_improving_code_review.domain.rule.loader import RuleLoader
from self_improving_code_review.domain.rule.models import RuleSnapshot
from self_improving_code_review.infrastructure.persistence import review_metrics_codec
from self_improving_code_review.infrastructure.persistence.llm_call_repository import (
    SqliteLlmCallRepository,
)
from self_improving_code_review.infrastructure.persistence.run_lease_repository import (
    SqliteRunLeaseRepository,
)
from self_improving_code_review.infrastructure.persistence.sqlite import (
    SqliteUnitOfWork,
    SqliteUnitOfWorkFactory,
    initialize_database,
)
from self_improving_code_review.messages import Messages

RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")


@pytest.mark.case("CT-M3-004")
def test_initialization_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "nested" / "review.db"

    initialize_database(database)
    initialize_database(database)

    with closing(sqlite3.connect(database)) as connection:
        versions = connection.execute("SELECT version FROM schema_migrations").fetchall()
    assert versions == [(i,) for i in range(1, 16)]


def test_initialization_failure_is_translated(tmp_path: Path) -> None:
    corrupted = tmp_path / "review.db"
    corrupted.write_text("not a database", encoding="utf-8")

    with pytest.raises(DatabaseError, match=re.escape(Messages.DATABASE_INITIALIZATION_FAILED)):
        initialize_database(corrupted)


def test_m3_identity_repository_rejects_missing_run(tmp_path: Path) -> None:
    """存在しない実行の更新を拒否し、読取は未存在として返す。"""
    database = tmp_path / "review.db"
    factory = SqliteUnitOfWorkFactory(database)

    with factory() as unit_of_work:
        assert unit_of_work.m3_audit.load_resume_identity(RUN_ID) is None
        with pytest.raises(DatabaseError, match=re.escape(Messages.RUN_NOT_FOUND_FOR_M3_IDENTITY)):
            unit_of_work.m3_audit.save_run_identity(RUN_ID, "plan")
    with (
        factory() as unit_of_work,
        pytest.raises(DatabaseError, match=re.escape(Messages.RUN_NOT_FOUND_FOR_CONTEXT_HASH)),
    ):
        unit_of_work.m3_audit.refresh_context_source_hash(RUN_ID)


def test_opening_a_transaction_on_an_invalid_path_is_translated(tmp_path: Path) -> None:
    unit_of_work = SqliteUnitOfWork(tmp_path)

    with pytest.raises(DatabaseError, match=re.escape(Messages.TRANSACTION_OPEN_FAILED)):
        unit_of_work.__enter__()


@pytest.mark.case("UT-M1-013")
def test_commit_without_an_active_transaction_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    initialize_database(database)

    with pytest.raises(DatabaseError, match=re.escape(Messages.UNIT_OF_WORK_NOT_ACTIVE)):
        SqliteUnitOfWork(database).commit()


@pytest.mark.case("UT-M1-013")
def test_rollback_without_an_active_transaction_is_a_no_operation(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    initialize_database(database)

    SqliteUnitOfWork(database).rollback()


@pytest.mark.case("IT-M1-011", "UT-M1-013")
def test_failed_transaction_is_rolled_back(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    factory = SqliteUnitOfWorkFactory(database)

    def fail_transaction() -> None:
        with factory() as unit_of_work:
            unit_of_work.rule_snapshots.save(_snapshot())
            unit_of_work.runs.create(_run())
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        fail_transaction()

    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM review_runs").fetchone()[0] == 0


def test_finalizing_an_unknown_run_is_rejected(tmp_path: Path) -> None:
    factory = SqliteUnitOfWorkFactory(tmp_path / "review.db")

    with (
        pytest.raises(DatabaseError, match=re.escape(Messages.RUN_NOT_FOUND_DURING_FINALIZE)),
        factory() as unit,
    ):
        unit.runs.finalize(RUN_ID, RunStatus.SUCCESS, _metrics(), datetime.now(UTC))


def test_unknown_idempotency_key_returns_no_completed_run(tmp_path: Path) -> None:
    factory = SqliteUnitOfWorkFactory(tmp_path / "review.db")

    with factory() as unit_of_work:
        assert unit_of_work.results.find_by_idempotency_key("missing") is None


def test_running_run_is_not_treated_as_a_completed_result(tmp_path: Path) -> None:
    factory = SqliteUnitOfWorkFactory(tmp_path / "review.db")

    with factory() as unit_of_work:
        unit_of_work.rule_snapshots.save(_snapshot())
        unit_of_work.runs.create(_run())
    with factory() as unit_of_work:
        assert unit_of_work.results.find_by_idempotency_key("idempotency-key") is None


def test_completed_run_without_final_metrics_is_not_restored(tmp_path: Path) -> None:
    """破損した完了記録を冪等な正常結果として返さない."""
    database = tmp_path / "review.db"
    factory = SqliteUnitOfWorkFactory(database)
    with factory() as unit:
        unit.rule_snapshots.save(_snapshot())
        unit.runs.create(_run())
    with factory() as unit:
        unit.runs.finalize(
            RUN_ID,
            RunStatus.SUCCESS,
            _metrics(),
            datetime(2026, 1, 2, 3, 5, tzinfo=UTC),
        )
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE review_runs SET metrics_json = NULL")
        connection.commit()
    with factory() as unit:
        assert unit.results.find_by_idempotency_key("idempotency-key") is None


def test_completed_run_is_restored_with_its_metrics(tmp_path: Path) -> None:
    factory = SqliteUnitOfWorkFactory(tmp_path / "review.db")
    finished_at = datetime(2026, 1, 2, 3, 5, tzinfo=UTC)

    with factory() as unit_of_work:
        unit_of_work.rule_snapshots.save(_snapshot())
        unit_of_work.runs.create(_run())
    with factory() as unit_of_work:
        unit_of_work.runs.finalize(RUN_ID, RunStatus.SUCCESS, _metrics(), finished_at)
    with factory() as unit_of_work:
        restored = unit_of_work.results.find_by_idempotency_key("idempotency-key")

    assert restored is not None
    assert restored.run.status is RunStatus.SUCCESS
    assert restored.run.finished_at == finished_at
    assert restored.run.versions.is_rule_set_empty
    assert restored.metrics.cost_usd == Decimal("0.02")
    assert restored.metrics.bundle_count == 1


def test_memory_snapshot_id_is_restored_for_idempotent_replay(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    factory = SqliteUnitOfWorkFactory(database)
    snapshot_id = "a" * 32
    with factory() as unit_of_work:
        unit_of_work.rule_snapshots.save(_snapshot())
        unit_of_work.runs.create(_run())
        unit_of_work.memory_snapshots.save(
            MemorySnapshot(
                snapshot_id=snapshot_id,
                project_id=ProjectId("test-project"),
                snapshot_hash="b" * 64,
                created_at=datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
                items=(),
            )
        )
    with factory() as unit_of_work:
        unit_of_work.runs.finalize(
            RUN_ID,
            RunStatus.SUCCESS,
            _metrics(),
            datetime(2026, 1, 2, 3, 5, tzinfo=UTC),
            snapshot_id,
        )
    with factory() as unit_of_work:
        restored = unit_of_work.results.find_by_idempotency_key("idempotency-key")

    assert restored is not None
    assert restored.run.versions.memory_snapshot_id == snapshot_id


def test_third_migration_adds_review_memory_snapshot_reference(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    migrations = Path(__file__).parents[2] / "migrations"
    with closing(sqlite3.connect(database)) as connection:
        for name in (
            "0001_m1.sql",
            "0002_m2.sql",
            "0003_review_memory_snapshot.sql",
            "0004_evaluation_adjudication.sql",
        ):
            connection.executescript((migrations / name).read_text(encoding="utf-8"))
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(review_runs)")}
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()

    assert "memory_snapshot_id" in columns
    assert versions == [(1,), (2,), (3,), (4,)]


@pytest.mark.case("CT-M3-004", "IT-M2-016")
def test_initialization_upgrades_a_database_at_second_migration(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    migrations = Path(__file__).parents[2] / "migrations"
    with closing(sqlite3.connect(database)) as connection:
        for name in ("0001_m1.sql", "0002_m2.sql"):
            connection.executescript((migrations / name).read_text(encoding="utf-8"))

    initialize_database(database)

    with closing(sqlite3.connect(database)) as connection:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(review_runs)")}
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert "memory_snapshot_id" in columns
    assert versions == [(i,) for i in range(1, 16)]


def test_initialization_upgrades_an_m4_database_through_all_m5_migrations(
    tmp_path: Path,
) -> None:
    """0008 適用済み DB を M5 の 0009 から 0011 へ前進移行する."""
    database = tmp_path / "m4-backup.db"
    migrations = Path(__file__).parents[2] / "migrations"
    with closing(sqlite3.connect(database)) as connection:
        for migration in sorted(migrations.glob("*.sql"))[:8]:
            connection.executescript(migration.read_text(encoding="utf-8"))

    initialize_database(database)

    with closing(sqlite3.connect(database)) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert versions == [(index,) for index in range(1, 16)]
    assert {"projects", "memory_conflicts", "reviewer_executions", "run_leases"} <= tables


def _schema_signature(database: Path) -> dict[str, object]:
    """表、列、外部キー、索引の比較用署名を返す."""
    with closing(sqlite3.connect(database)) as connection:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        signature: dict[str, object] = {}
        for table in tables:
            indexes = sorted(
                (
                    str(row[1]),
                    int(row[2]),
                    int(row[4]),
                    tuple(
                        str(column[2])
                        for column in connection.execute(f"PRAGMA index_info('{row[1]}')")
                    ),
                )
                for row in connection.execute(f"PRAGMA index_list('{table}')")
                if not str(row[1]).startswith("sqlite_autoindex")
            )
            signature[table] = (
                [tuple(row[1:]) for row in connection.execute(f"PRAGMA table_info('{table}')")],
                sorted(
                    tuple(row[2:])
                    for row in connection.execute(f"PRAGMA foreign_key_list('{table}')")
                ),
                indexes,
            )
        signature["schema_migrations"] = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    return signature


@pytest.mark.case("CT-M3-004", "IT-M2-016")
def test_sql_migrations_match_runtime_schema(tmp_path: Path) -> None:
    """migrations/*.sql の全適用結果が実行時初期化と同一の構造になる."""
    runtime = tmp_path / "runtime.db"
    from_sql = tmp_path / "sql.db"
    initialize_database(runtime)
    migrations = Path(__file__).parents[2] / "migrations"
    with closing(sqlite3.connect(from_sql)) as connection:
        for migration in sorted(migrations.glob("*.sql")):
            connection.executescript(migration.read_text(encoding="utf-8"))

    assert _schema_signature(from_sql) == _schema_signature(runtime)


def test_initialization_preserves_retired_web_draft_data(tmp_path: Path) -> None:
    """廃止前の移行で保存した下書きを再初期化後も保持する。"""
    database = tmp_path / "legacy-web.db"
    migrations = Path(__file__).parents[2] / "migrations"
    with closing(sqlite3.connect(database)) as connection:
        for migration in sorted(migrations.glob("*.sql")):
            connection.executescript(migration.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?, 'active', 1, ?, ?)",
            ("pilot", "Pilot", "a" * 64, "2026-01-01", "2026-01-01"),
        )
        connection.execute(
            "INSERT INTO acceptance_drafts VALUES (?, ?, ?, ?, ?, ?)",
            ("pilot", "draft-1", "a" * 64, "{}", "b" * 64, "2026-01-01"),
        )
        connection.commit()

    initialize_database(database)

    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT project_id, draft_id, document_json FROM acceptance_drafts"
        ).fetchall() == [("pilot", "draft-1", "{}")]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_stored_metrics_that_are_not_an_object_are_rejected(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    factory = SqliteUnitOfWorkFactory(database)
    with factory() as unit_of_work:
        unit_of_work.rule_snapshots.save(_snapshot())
        unit_of_work.runs.create(_run())
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE review_runs SET status = 'success', metrics_json = ? WHERE run_id = ?",
            ("[]", str(RUN_ID)),
        )
        connection.commit()

    with (
        pytest.raises(DatabaseError, match=re.escape(Messages.STORED_REVIEW_METRICS_INVALID)),
        factory() as unit,
    ):
        unit.results.find_by_idempotency_key("idempotency-key")


@pytest.mark.parametrize(
    "reasons",
    [[], {1: 1}, {"UNKNOWN_RULE_ID": True}, {"UNKNOWN_RULE_ID": -1}],
)
def test_invalid_rejection_reason_counts_are_rejected(reasons: object) -> None:
    with pytest.raises(DatabaseError, match=re.escape(Messages.STORED_REJECTION_COUNTS_INVALID)):
        review_metrics_codec._rejection_reason_counts({"rejection_reasons": reasons})


def test_rejection_reason_counts_are_restored_in_stable_order() -> None:
    restored = review_metrics_codec._rejection_reason_counts(
        {"rejection_reasons": {"UNKNOWN_RULE_ID": 2, "INVALID_PATH": 1}}
    )

    assert restored == (("INVALID_PATH", 1), ("UNKNOWN_RULE_ID", 2))


@pytest.mark.parametrize(
    "document",
    [
        {"reviewer_profile_metrics": {}},
        {"reviewer_profile_metrics": [None]},
        {"reviewer_profile_metrics": [{}]},
        {"reviewer_profile_metrics": [{"profile": "unknown"}]},
        {"reviewer_profile_metrics": [{"profile": "general", "input_tokens": True}]},
        {"reviewer_profile_metrics": [{"profile": "general", "input_tokens": -1}]},
        {
            "reviewer_profile_metrics": [
                {
                    "profile": "general",
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "latency_ms": 1,
                    "cost_usd": "invalid",
                    "valid_finding_count": 1,
                    "duplicate_count": 0,
                }
            ]
        },
    ],
)
def test_invalid_reviewer_profile_metrics_are_rejected(document: dict[str, object]) -> None:
    """保存済み担当別計測の不正な型、値、列挙値を拒否する。"""
    with pytest.raises(DatabaseError, match=re.escape(Messages.STORED_REVIEWER_METRICS_INVALID)):
        review_metrics_codec._reviewer_profile_metrics(document)


def test_run_repository_separates_completed_and_resumable_runs(tmp_path: Path) -> None:
    factory = SqliteUnitOfWorkFactory(tmp_path / "review.db")

    with factory() as unit_of_work:
        unit_of_work.rule_snapshots.save(_snapshot())
        unit_of_work.runs.create(_run())
    with factory() as unit_of_work:
        assert unit_of_work.runs.find_completed_by_idempotency_key("idempotency-key") is None
    with factory() as unit_of_work:
        unit_of_work.runs.finalize(
            RUN_ID,
            RunStatus.PARTIAL_SUCCESS,
            _metrics(),
            datetime(2026, 1, 2, 3, 5, tzinfo=UTC),
        )
    with factory() as unit_of_work:
        completed = unit_of_work.runs.find_completed_by_idempotency_key("idempotency-key")
        resumable = unit_of_work.results.find_resumable_by_idempotency_key("idempotency-key")
        assert unit_of_work.runs.find_completed_by_idempotency_key("other-key") is None

    assert completed is None
    assert resumable is not None
    assert resumable.run.status is RunStatus.PARTIAL_SUCCESS
    assert resumable.run.source_revision == "a" * 40


def test_run_repository_restores_successful_completed_run(tmp_path: Path) -> None:
    factory = SqliteUnitOfWorkFactory(tmp_path / "review.db")
    with factory() as unit_of_work:
        unit_of_work.rule_snapshots.save(_snapshot())
        unit_of_work.runs.create(_run())
        unit_of_work.runs.finalize(
            RUN_ID,
            RunStatus.SUCCESS,
            _metrics(),
            datetime(2026, 1, 2, 3, 5, tzinfo=UTC),
        )

    with factory() as unit_of_work:
        completed = unit_of_work.runs.find_completed_by_idempotency_key("idempotency-key")

    assert completed is not None
    assert completed.status is RunStatus.SUCCESS


def test_m3_bundle_metadata_rejects_unknown_bundle(tmp_path: Path) -> None:
    factory = SqliteUnitOfWorkFactory(tmp_path / "review.db")
    bundle = SimpleNamespace(bundle_id=BundleId(RUN_ID, 1))
    plan = SimpleNamespace(
        reviewer_type=ReviewerType.GENERAL,
        profile_reason_code="GENERAL_DEFAULT",
    )

    with (
        pytest.raises(DatabaseError, match=re.escape(Messages.BUNDLE_NOT_FOUND_FOR_M3_METADATA)),
        factory() as unit_of_work,
    ):
        unit_of_work.m3_audit.save_bundle_metadata(bundle, plan, "input", "result")


def test_llm_call_repository_rejects_missing_insert_identifier() -> None:
    class ConnectionWithoutIdentifier:
        """挿入識別子を返さない異常な SQLite 接続。"""

        def execute(self, *_args: object) -> object:
            return SimpleNamespace(lastrowid=None)

    repository = SqliteLlmCallRepository(ConnectionWithoutIdentifier())
    record = LlmCallRecord(
        run_id=RUN_ID,
        bundle_sequence=1,
        request_hash="request",
        model="dataset/test",
        status="success",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
        cost_usd=Decimal("0"),
        attempt=1,
    )

    with pytest.raises(DatabaseError, match=re.escape(Messages.LLM_CALL_ID_UNAVAILABLE)):
        repository.save(record)


def _snapshot() -> RuleSnapshot:
    return RuleLoader().load(Path("missing-rules"), git_sha="a" * 40)


def _run() -> RunRecord:
    snapshot = _snapshot()
    return RunRecord(
        run_id=RUN_ID,
        project_id=ProjectId("test-project"),
        mode=ReviewMode.RULES,
        status=RunStatus.RUNNING,
        source_revision="a" * 40,
        model="claude-fixed-v1",
        versions=RunVersions(
            package_version="0.1.0",
            config_hash="config-hash",
            prompt_version="review_general_v1@1",
            prompt_hash="prompt-hash",
            rule_snapshot_id=snapshot.snapshot_id,
            rule_set_hash=snapshot.rule_set_hash,
            is_rule_set_empty=True,
            secret_engine_version="1.5.0",
        ),
        idempotency_key="idempotency-key",
        started_at=datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
    )


def _metrics() -> ReviewMetrics:
    return ReviewMetrics(
        input_tokens=100,
        output_tokens=20,
        latency_ms=5,
        cost_usd=Decimal("0.02"),
        candidate_count=1,
        main_finding_count=1,
        reference_finding_count=0,
        invalid_position_count=0,
        invalid_evidence_count=0,
        duplicate_count=0,
        bundle_count=1,
        failed_bundle_count=0,
    )


def test_connect_failure_before_assignment_is_translated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_connect(_path: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError

    monkeypatch.setattr(sqlite3, "connect", fail_connect)

    with pytest.raises(DatabaseError, match=re.escape(Messages.DATABASE_INITIALIZATION_FAILED)):
        initialize_database(tmp_path / "review.db")


@pytest.mark.case("UT-M1-013")
def test_exit_without_an_active_transaction_is_rejected(tmp_path: Path) -> None:
    unit_of_work = SqliteUnitOfWork(tmp_path / "review.db")

    with pytest.raises(DatabaseError, match=re.escape(Messages.UNIT_OF_WORK_NOT_ACTIVE)):
        unit_of_work.__exit__(None, None, None)


def test_finalization_with_lease_is_fenced_and_releases_lease(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    factory = SqliteUnitOfWorkFactory(database)
    identity = RunLeaseIdentity(RUN_ID, "owner-1", 1)
    finished_at = datetime(2026, 1, 2, 3, 5, tzinfo=UTC)
    with factory() as unit:
        unit.rule_snapshots.save(_snapshot())
        unit.runs.create(_run())
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            """
            INSERT INTO run_leases(
                run_id, lease_owner, lease_expires_at, heartbeat_at, fencing_token
            ) VALUES(?,?,?,?,?)
            """,
            (
                str(RUN_ID),
                identity.owner,
                "2026-01-02T03:06:00+00:00",
                "2026-01-02T03:04:00+00:00",
                identity.fencing_token,
            ),
        )
        connection.commit()

    with factory() as unit:
        unit.runs.finalize(
            RUN_ID,
            RunStatus.SUCCESS,
            _metrics(),
            finished_at,
            lease_identity=identity,
        )

    with closing(sqlite3.connect(database)) as connection:
        lease_count = connection.execute("SELECT COUNT(*) FROM run_leases").fetchone()[0]
    assert lease_count == 0


def test_finalization_rejects_stale_fencing_token(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    factory = SqliteUnitOfWorkFactory(database)
    with factory() as unit:
        unit.rule_snapshots.save(_snapshot())
        unit.runs.create(_run())
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO run_leases VALUES(?,?,?,?,?)",
            (
                str(RUN_ID),
                "owner-2",
                "2099-01-01T00:00:00+00:00",
                "2026-01-02T03:04:00+00:00",
                2,
            ),
        )
        connection.commit()

    stale = RunLeaseIdentity(RUN_ID, "owner-1", 1)
    with pytest.raises(ConcurrencyConflictError), factory() as unit:
        unit.runs.finalize(
            RUN_ID,
            RunStatus.SUCCESS,
            _metrics(),
            datetime(2026, 1, 2, 3, 5, tzinfo=UTC),
            lease_identity=stale,
            lease_checked_at=datetime(2026, 1, 2, 3, 5, tzinfo=UTC),
        )


def test_running_resume_requires_the_current_valid_lease(tmp_path: Path) -> None:
    """実行中レコードは現在の所有者と囲い込み番号が一致する場合だけ接管する."""
    database = tmp_path / "review.db"
    factory = SqliteUnitOfWorkFactory(database)
    checked_at = datetime(2026, 1, 2, 3, 5, tzinfo=UTC)
    with factory() as unit:
        unit.rule_snapshots.save(_snapshot())
        unit.runs.create(_run())
    identity = SqliteRunLeaseRepository(database).acquire(
        RUN_ID,
        "owner-2",
        checked_at,
        checked_at + timedelta(seconds=90),
    )
    assert identity is not None

    with factory() as unit:
        assert unit.runs.acquire_resume(RUN_ID) is False
    with factory() as unit:
        assert (
            unit.runs.acquire_resume(
                RUN_ID,
                lease_identity=identity,
            )
            is False
        )
    with factory() as unit:
        assert (
            unit.runs.acquire_resume(
                RUN_ID,
                lease_identity=replace(identity, owner="stale-owner"),
                lease_checked_at=checked_at,
            )
            is False
        )
    with factory() as unit:
        assert unit.runs.acquire_resume(
            RUN_ID,
            lease_identity=identity,
            lease_checked_at=checked_at,
        )


@pytest.mark.case("UT-M1-013")
def test_create_translates_active_idempotency_conflict(tmp_path: Path) -> None:
    """同じ冪等性キーの並行開始を並行実行競合へ変換する。"""
    factory = SqliteUnitOfWorkFactory(tmp_path / "review.db")
    with factory() as unit:
        unit.rule_snapshots.save(_snapshot())
        unit.runs.create(_run())

    concurrent_run = replace(
        _run(),
        run_id=RunId("22345678-1234-4234-9234-123456789abc"),
    )
    with pytest.raises(ConcurrencyConflictError), factory() as unit:
        unit.runs.create(concurrent_run)


def test_create_preserves_unrelated_integrity_error(tmp_path: Path) -> None:
    """冪等性キー以外の整合性違反は元の SQLite 例外を維持する。"""
    factory = SqliteUnitOfWorkFactory(tmp_path / "review.db")
    with factory() as unit:
        unit.rule_snapshots.save(_snapshot())
        unit.runs.create(_run())

    duplicate_run_id = replace(_run(), idempotency_key="different-idempotency-key")
    with pytest.raises(sqlite3.IntegrityError, match=r"review_runs\.run_id"), factory() as unit:
        unit.runs.create(duplicate_run_id)


@pytest.mark.case("CT-M3-004", "IT-M1-011")
@pytest.mark.parametrize("should_rollback", [False, True])
def test_review_repositories_share_run_transaction(tmp_path: Path, should_rollback: bool) -> None:
    """分離した監査・呼出倉庫も実行記録と同じトランザクションで確定・取消される."""
    database = tmp_path / "review.db"
    factory = SqliteUnitOfWorkFactory(database)
    with factory() as unit_of_work:
        unit_of_work.rule_snapshots.save(_snapshot())
        unit_of_work.runs.create(_run())
        unit_of_work.m3_audit.save_checkpoint(
            RUN_ID, "diff-parsed", "a" * 64, datetime(2026, 1, 1, tzinfo=UTC)
        )
        unit_of_work.llm_calls.save(
            LlmCallRecord(
                run_id=RUN_ID,
                bundle_sequence=1,
                request_hash="a" * 64,
                model="dataset/test",
                status="success",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                cost_usd=Decimal("0"),
                attempt=1,
            )
        )
        if should_rollback:
            unit_of_work.rollback()

    expected_count = 0 if should_rollback else 1
    with closing(sqlite3.connect(database)) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM review_runs").fetchone()[0] == expected_count
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM run_checkpoints").fetchone()[0]
            == expected_count
        )
        assert connection.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == expected_count
