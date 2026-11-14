from __future__ import annotations

import json
import sqlite3
import subprocess
from contextlib import closing, nullcontext
from pathlib import Path

import pytest

from self_improving_code_review.application.ports.telemetry import TelemetryHealth
from self_improving_code_review.cli.main import main
from self_improving_code_review.domain.common.errors import ConcurrencyConflictError
from self_improving_code_review.infrastructure.persistence.sqlite import initialize_database
from self_improving_code_review.messages import Messages


class _RecordingTelemetry:
    """M5 一連処理が送る安全な遠隔測定値をメモリ内へ記録する."""

    def __init__(self) -> None:
        """イベント、カウンターおよび分布値の記録先を初期化する."""
        self.spans: list[tuple[str, dict[str, object]]] = []
        self.events: list[tuple[str, dict[str, object]]] = []
        self.counters: list[tuple[str, int, dict[str, object]]] = []
        self.histograms: list[tuple[str, int | float, dict[str, object]]] = []

    def start_span(self, name: str, attributes: object | None = None) -> object:
        """スパン名と属性を記録して空の区間を返す."""
        self.spans.append((name, dict(attributes or {})))
        return nullcontext()

    def add_event(self, name: str, attributes: object | None = None) -> None:
        """イベント名と属性を記録する."""
        self.events.append((name, dict(attributes or {})))

    def record_counter(
        self,
        name: str,
        value: int,
        attributes: object | None = None,
    ) -> None:
        """カウンター名、値および属性を記録する."""
        self.counters.append((name, value, dict(attributes or {})))

    def record_histogram(
        self,
        name: str,
        value: int | float,
        attributes: object | None = None,
    ) -> None:
        """分布名、値および属性を記録する."""
        self.histograms.append((name, value, dict(attributes or {})))

    @property
    def health(self) -> TelemetryHealth:
        """記録破棄のない健全状態を返す."""
        return TelemetryHealth()

    def close(self) -> None:
        """メモリ内記録のため終了処理を行わない."""


class _LoseBeforeFinalizeLeaseSession:
    """最終保存の直前に所有権喪失を模擬するリース会話."""

    _ERROR = "run lease ownership was lost"

    def __init__(self) -> None:
        """所有権検査回数を初期化する."""
        self.check_count = 0

    def __enter__(self) -> _LoseBeforeFinalizeLeaseSession:
        """同じ模擬会話を返す."""
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: object | None,
    ) -> None:
        """終了処理を行わない."""

    def acquire(self, _run_id: object) -> None:
        """取得を成功扱いにする."""

    def assert_owned(self) -> None:
        """四回目の検査で最終保存前の所有権喪失を通知する."""
        self.check_count += 1
        if self.check_count == 4:
            raise ConcurrencyConflictError(self._ERROR)

    @property
    def identity(self) -> None:
        """模擬会話には永続化用のリース識別子がない."""
        return None


def _config(repository: Path) -> Path:
    config = repository / "review.yaml"
    config.write_text(
        """schema_version: 1
project_id: project-a
database:
  path: .review/review.db
memory:
  maintainers: [tanaka]
governance:
  enabled: false
""",
        encoding="utf-8",
    )
    return config


def test_m5_project_governance_cli_flow(
    tmp_path: Path,
    capsys: object,
) -> None:
    config = _config(tmp_path)
    shared = ["--repository", str(tmp_path), "--config", str(config)]

    assert (
        main(
            [
                "governance",
                "project",
                "register",
                *shared,
                "--project",
                "project-a",
                "--name",
                "Project A",
                "--actor",
                "tanaka",
                "--reason",
                "initial setup",
            ]
        )
        == 0
    )
    register_document = json.loads(capsys.readouterr().out)
    assert register_document["schema_version"] == 2
    assert register_document["project_id"] == "project-a"

    assert (
        main(
            [
                "governance",
                "project",
                "bind-repository",
                *shared,
                "--project",
                "project-a",
                "--provider",
                "github",
                "--external-repository",
                "owner/repository",
                "--actor",
                "tanaka",
                "--reason",
                "bind",
            ]
        )
        == 0
    )
    binding_document = json.loads(capsys.readouterr().out)
    assert binding_document["repository_external_id"] == "owner/repository"

    assert main(["governance", "project", "list", *shared]) == 0
    list_document = json.loads(capsys.readouterr().out)
    assert [item["project_id"] for item in list_document["projects"]] == ["project-a"]

    database = tmp_path / ".review/review.db"
    initialize_database(database)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO ground_truth_sets("
            "set_id, split, version, set_hash, status, approved_by, published_at"
            ") VALUES('set-1', 'test', 1, ?, 'published', 'tanaka', "
            "'2026-01-02T03:04:05+00:00')",
            ("a" * 64,),
        )
        connection.commit()

    assert (
        main(
            [
                "governance",
                "project",
                "adopt-existing",
                *shared,
                "--project",
                "project-a",
                "--resource-type",
                "ground-truth-set",
                "--resource-id",
                "set-1",
                "--actor",
                "tanaka",
                "--reason",
                "adopt",
            ]
        )
        == 0
    )
    adoption_document = json.loads(capsys.readouterr().out)
    assert adoption_document["status"] == "adopted"
    assert "set-1" not in json.dumps(adoption_document)


def test_m5_project_governance_cli_rejects_unauthorized_actor(
    tmp_path: Path,
    capsys: object,
) -> None:
    config = _config(tmp_path)

    code = main(
        [
            "governance",
            "project",
            "register",
            "--repository",
            str(tmp_path),
            "--config",
            str(config),
            "--project",
            "project-a",
            "--name",
            "Project A",
            "--actor",
            "unknown",
            "--reason",
            "initial setup",
        ]
    )
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "AuthorizationError"

    assert code == 5


def test_m5_backup_verify_cli_writes_safe_report(tmp_path: Path, capsys: object) -> None:
    config = _config(tmp_path)
    database = tmp_path / ".review" / "review.db"
    initialize_database(database)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO projects VALUES(?,?,?,?,?,?,?)",
            (
                "project-a",
                "Project A",
                "a" * 64,
                "active",
                1,
                "2026-01-02T03:04:05+00:00",
                "2026-01-02T03:04:05+00:00",
            ),
        )
        connection.commit()
    report = tmp_path / ".review" / "backup-report.json"

    exit_code = main(
        [
            "governance",
            "backup",
            "verify",
            "--repository",
            str(tmp_path),
            "--config",
            str(config),
            "--database",
            ".review/review.db",
            "--project",
            "project-a",
            "--actor",
            "tanaka",
            "--output",
            str(report),
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == ""
    document = json.loads(report.read_text(encoding="utf-8"))
    assert document["status"] == "verified"
    assert document["failure_codes"] == []
    assert document["checks"]["integrity"] is True
    assert "database" not in document


def test_m5_backup_verify_cli_reports_invalid_database(tmp_path: Path) -> None:
    config = _config(tmp_path)
    corrupted = tmp_path / "corrupted.db"
    corrupted.write_text("not a database", encoding="utf-8")
    report = tmp_path / "report.json"

    exit_code = main(
        [
            "governance",
            "backup",
            "verify",
            "--repository",
            str(tmp_path),
            "--config",
            str(config),
            "--database",
            str(corrupted),
            "--project",
            "project-a",
            "--actor",
            "tanaka",
            "--output",
            str(report),
        ]
    )

    assert exit_code == 5
    document = json.loads(report.read_text(encoding="utf-8"))
    assert document["status"] == "invalid"
    assert document["failure_codes"] == ["DATABASE_UNREADABLE"]


@pytest.mark.parametrize("database", ["missing.db", "../outside.db"])
def test_m5_backup_verify_cli_rejects_unsafe_database_path(
    tmp_path: Path,
    capsys: object,
    database: str,
) -> None:
    config = _config(tmp_path)

    exit_code = main(
        [
            "governance",
            "backup",
            "verify",
            "--repository",
            str(tmp_path),
            "--config",
            str(config),
            "--database",
            database,
            "--project",
            "project-a",
            "--actor",
            "tanaka",
            "--output",
            str(tmp_path / "report.json"),
        ]
    )

    assert exit_code == 1
    assert "InvalidInputError" in capsys.readouterr().err


def test_m5_backup_verify_cli_requires_maintainer(tmp_path: Path, capsys: object) -> None:
    config = _config(tmp_path)
    database = tmp_path / ".review" / "review.db"
    initialize_database(database)

    exit_code = main(
        [
            "governance",
            "backup",
            "verify",
            "--repository",
            str(tmp_path),
            "--config",
            str(config),
            "--database",
            str(database),
            "--project",
            "project-a",
            "--actor",
            "outsider",
            "--output",
            str(tmp_path / "report.json"),
        ]
    )

    assert exit_code == 5
    assert "AuthorizationError" in capsys.readouterr().err


def test_m5_review_cli_integrates_specialist_lease_metrics_and_telemetry(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """登録済みプロジェクトで専門担当、リース、計測を一連処理として検証する."""
    repository, config, patch = _prepare_m5_review_repository(tmp_path)
    shared = ["--repository", str(repository), "--config", str(config)]
    _register_and_bind_project(shared, capsys)
    config.write_text(
        config.read_text(encoding="utf-8").replace("enabled: false", "enabled: true"),
        encoding="utf-8",
    )

    telemetry = _RecordingTelemetry()
    import self_improving_code_review.cli.review_commands as review_commands

    monkeypatch.setattr(review_commands, "_build_telemetry", lambda _config: telemetry)
    output = repository / ".review" / "m5-result.json"

    exit_code = main(
        [
            "review",
            *shared,
            "--mode",
            "rules",
            "--diff-file",
            str(patch),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["schema_version"] == 2
    assert document["run"]["status"] == "success"
    assert len(document["findings"]) == 1
    assert document["metrics"]["input_tokens"] == 22
    assert document["metrics"]["output_tokens"] == 14
    assert document["metrics"]["latency_ms"] == 6
    assert document["metrics"]["cost_usd"] == "0.04"
    profiles = {item["profile"]: item for item in document["reviewer_metrics"]}
    assert set(profiles) == {"data", "general"}
    assert sum(item["valid_finding_count"] for item in profiles.values()) == 2
    assert sum(item["duplicate_count"] for item in profiles.values()) == 1

    database = repository / ".review" / "review.db"
    with closing(sqlite3.connect(database)) as connection:
        executions = connection.execute(
            "SELECT profile, input_tokens, output_tokens, latency_ms, cost_usd, "
            "valid_finding_count, duplicate_count FROM reviewer_executions ORDER BY profile"
        ).fetchall()
        call_totals = connection.execute(
            "SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(latency_ms) "
            "FROM llm_calls WHERE status = 'success'"
        ).fetchone()
        active_leases = connection.execute("SELECT COUNT(*) FROM run_leases").fetchone()[0]
    assert executions == [
        ("data", 11, 7, 3, "0.02", 1, 0),
        ("general", 11, 7, 3, "0.02", 1, 1),
    ]
    assert call_totals == (2, 22, 14, 6)
    assert active_leases == 0

    assert any(name == "review.completed" for name, _attributes in telemetry.events)
    assert any(
        name == "review.input_tokens" and value == 22
        for name, value, _attributes in telemetry.counters
    )
    serialized_telemetry = json.dumps(
        [telemetry.spans, telemetry.events, telemetry.counters, telemetry.histograms],
        ensure_ascii=False,
    )
    assert "migrations/0012_add_user.sql" not in serialized_telemetry
    assert "ALTER TABLE" not in serialized_telemetry


def _prepare_m5_review_repository(tmp_path: Path) -> tuple[Path, Path, Path]:
    """M5 の専門担当を起動する Git、Prompt、Dataset および設定を作成する."""
    repository = tmp_path / "repository"
    source = repository / "migrations" / "0012_add_user.sql"
    source.parent.mkdir(parents=True)
    source.write_text("CREATE TABLE users(id INTEGER PRIMARY KEY);\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=repository,
        check=True,
    )

    project_root = Path(__file__).parents[2]
    prompts = repository / "prompts"
    prompts.mkdir()
    for name in ("review_general_v1.yaml", "review_specialist_v1.yaml"):
        (prompts / name).write_text(
            (project_root / "prompts" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    rule = repository / "config" / "rules" / "sql" / "unsafe_schema_change.yaml"
    rule.parent.mkdir(parents=True)
    rule.write_text(
        """id: DATA.UNSAFE_SCHEMA_CHANGE
category: correctness
severity: high
scope:
  languages: [sql]
instruction: >
  スキーマ変更で既存データの整合性が失われる場合だけ指摘する。
""",
        encoding="utf-8",
    )

    dataset = repository / "datasets" / "m5-review.json"
    dataset.parent.mkdir()
    dataset.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model": "m5-e2e-dataset-v1",
                "default_result": {
                    "payload": {
                        "schema_version": 1,
                        "findings": [
                            {
                                "file": "migrations/0012_add_user.sql",
                                "start_line": 2,
                                "end_line": 2,
                                "category": "correctness",
                                "issue_type": "unsafe-schema-change",
                                "rule_id": "DATA.UNSAFE_SCHEMA_CHANGE",
                                "primary_entity": "users.email",
                                "severity": "high",
                                "title": "制約のない列追加",
                                "description": "移行後の値が保証されません。",
                                "evidence": {
                                    "quote": "ALTER TABLE users ADD COLUMN email TEXT;",
                                    "reason": "追加されたスキーマ変更です。",
                                },
                                "suggestion": "移行方針と制約を明示してください。",
                                "needs_context": False,
                            }
                        ],
                    },
                    "usage": {
                        "input_tokens": 11,
                        "output_tokens": 7,
                        "latency_ms": 3,
                        "cost_usd": "0.02",
                    },
                },
                "cases": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    config = repository / "review.yaml"
    config.write_text(
        """schema_version: 1
project_id: project-a
database:
  path: .review/review.db
llm:
  provider: dataset
  dataset_path: datasets/m5-review.json
review:
  mode: rules
memory:
  maintainers: [tanaka]
  is_production: false
security:
  allow_external_source: false
scm:
  provider: github
  repository_external_id: owner/repository
governance:
  enabled: false
  memory_lifecycle_enabled: false
  specialized_reviewers_enabled: false
  execution_leases_enabled: false
""",
        encoding="utf-8",
    )
    patch = repository / "change.patch"
    patch.write_text(
        "diff --git a/migrations/0012_add_user.sql b/migrations/0012_add_user.sql\n"
        "--- a/migrations/0012_add_user.sql\n"
        "+++ b/migrations/0012_add_user.sql\n"
        "@@ -1 +1,2 @@\n"
        " CREATE TABLE users(id INTEGER PRIMARY KEY);\n"
        "+ALTER TABLE users ADD COLUMN email TEXT;\n",
        encoding="utf-8",
    )
    return repository, config, patch


def _register_and_bind_project(
    shared: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """レビュー前に対象プロジェクトと外部リポジトリを明示登録する."""
    assert (
        main(
            [
                "governance",
                "project",
                "register",
                *shared,
                "--project",
                "project-a",
                "--name",
                "Project A",
                "--actor",
                "tanaka",
                "--reason",
                "M5 E2E setup",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "governance",
                "project",
                "bind-repository",
                *shared,
                "--project",
                "project-a",
                "--provider",
                "github",
                "--external-repository",
                "owner/repository",
                "--actor",
                "tanaka",
                "--reason",
                "M5 E2E binding",
            ]
        )
        == 0
    )
    capsys.readouterr()


def test_m5_review_cli_hides_cross_project_context(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """別プロジェクト指定を同じ安全な未検出エラーとして拒否する."""
    repository, config, patch = _prepare_m5_review_repository(tmp_path)
    shared = ["--repository", str(repository), "--config", str(config)]
    _register_and_bind_project(shared, capsys)
    config.write_text(
        config.read_text(encoding="utf-8").replace("enabled: false", "enabled: true"),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "review",
            *shared,
            "--project",
            "project-b",
            "--mode",
            "rules",
            "--diff-file",
            str(patch),
            "--output",
            str(repository / ".review" / "cross-project.json"),
        ]
    )

    error = json.loads(capsys.readouterr().err)
    assert exit_code == 1
    assert error["error"] == {
        "code": "InvalidInputError",
        "message": Messages.PROJECT_CONTEXT_NOT_FOUND,
    }
    assert "owner/repository" not in json.dumps(error)


def test_m5_review_cli_blocks_secret_before_llm_or_result_persistence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """機密情報検出時に全実行を遮断し、LLM 呼出しと指摘保存を行わない."""
    repository, config, patch = _prepare_m5_review_repository(tmp_path)
    shared = ["--repository", str(repository), "--config", str(config)]
    _register_and_bind_project(shared, capsys)
    config.write_text(
        config.read_text(encoding="utf-8").replace("enabled: false", "enabled: true"),
        encoding="utf-8",
    )
    patch.write_text(
        "diff --git a/migrations/0012_add_user.sql b/migrations/0012_add_user.sql\n"
        "--- a/migrations/0012_add_user.sql\n"
        "+++ b/migrations/0012_add_user.sql\n"
        "@@ -1 +1,2 @@\n"
        " CREATE TABLE users(id INTEGER PRIMARY KEY);\n"
        '+AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n',
        encoding="utf-8",
    )
    telemetry = _RecordingTelemetry()
    import self_improving_code_review.cli.review_commands as review_commands

    monkeypatch.setattr(review_commands, "_build_telemetry", lambda _config: telemetry)
    output = repository / ".review" / "blocked.json"

    exit_code = main(
        [
            "review",
            *shared,
            "--mode",
            "rules",
            "--diff-file",
            str(patch),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 4
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["run"]["status"] == "blocked"
    assert document["findings"] == []
    assert document["secret_findings"]
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(document)
    with closing(sqlite3.connect(repository / ".review" / "review.db")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
        assert connection.execute("SELECT status FROM review_runs").fetchone()[0] == "blocked"
    serialized_telemetry = json.dumps(
        [telemetry.spans, telemetry.events, telemetry.counters, telemetry.histograms],
        ensure_ascii=False,
    )
    assert "AKIAIOSFODNN7EXAMPLE" not in serialized_telemetry


def test_m5_memory_lifecycle_cli_completes_governed_flow(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """競合、置換、保管、復元および期限再確認を CLI と SQLite で完結させる."""
    config = _config(tmp_path)
    shared = ["--repository", str(tmp_path), "--config", str(config)]
    _register_and_bind_project(shared, capsys)
    config.write_text(
        config.read_text(encoding="utf-8").replace("enabled: false", "enabled: true")
        + "  memory_lifecycle_enabled: true\n",
        encoding="utf-8",
    )
    memory_ids = _seed_m5_memories(tmp_path / ".review" / "review.db")

    approve_code = main(
        [
            "memory",
            "approve",
            memory_ids["candidate"],
            *shared,
            "--actor",
            "tanaka",
            "--reason",
            "競合候補として確認",
            "--lock-version",
            "1",
        ]
    )
    approved = json.loads(capsys.readouterr().out)
    assert approve_code == 2
    assert approved["status"] == "conflict"

    assert (
        main(
            [
                "memory",
                "conflict",
                "list",
                *shared,
                "--project",
                "project-a",
            ]
        )
        == 0
    )
    conflict = json.loads(capsys.readouterr().out)["conflicts"][0]
    assert conflict["status"] == "open"
    assert (
        main(
            [
                "memory",
                "conflict",
                "resolve",
                conflict["conflict_id"],
                *shared,
                "--project",
                "project-a",
                "--resolution",
                "reject-candidate",
                "--lock-version",
                str(conflict["lock_version"]),
                "--actor",
                "tanaka",
                "--reason",
                "既存知識を維持",
                "--evidence",
                "確認済み仕様",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["memory"]["status"] == "rejected"

    assert (
        main(
            [
                "memory",
                "archive",
                memory_ids["active"],
                *shared,
                "--project",
                "project-a",
                "--lock-version",
                "1",
                "--actor",
                "tanaka",
                "--reason",
                "期限切れのため保管",
            ]
        )
        == 0
    )
    archived = json.loads(capsys.readouterr().out)
    assert archived["status"] == "archived"
    assert (
        main(
            [
                "memory",
                "restore",
                memory_ids["active"],
                *shared,
                "--project",
                "project-a",
                "--lock-version",
                str(archived["lock_version"]),
                "--actor",
                "tanaka",
                "--reason",
                "再評価のため復元",
            ]
        )
        == 0
    )
    restored = json.loads(capsys.readouterr().out)
    assert restored["status"] == "pending"
    assert restored["version"] == 2

    assert (
        main(
            [
                "memory",
                "supersede",
                memory_ids["superseded"],
                *shared,
                "--project",
                "project-a",
                "--replacement",
                memory_ids["replacement"],
                "--lock-version",
                "1",
                "--actor",
                "tanaka",
                "--reason",
                "新しい知識へ置換",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "superseded"

    assert (
        main(
            [
                "memory",
                "revalidation",
                "list",
                *shared,
                "--project",
                "project-a",
            ]
        )
        == 0
    )
    due = json.loads(capsys.readouterr().out)["revalidations"]
    assert [item["memory_id"] for item in due] == [memory_ids["due"]]
    assert (
        main(
            [
                "memory",
                "revalidation",
                "confirm",
                memory_ids["due"],
                *shared,
                "--project",
                "project-a",
                "--lock-version",
                "1",
                "--actor",
                "tanaka",
                "--reason",
                "現行仕様を再確認",
                "--evidence",
                "結合試験で確認",
            ]
        )
        == 0
    )
    confirmed = json.loads(capsys.readouterr().out)
    assert confirmed["status"] == "active"
    assert confirmed["version"] == 2

    with closing(sqlite3.connect(tmp_path / ".review" / "review.db")) as connection:
        assert connection.execute("SELECT status, resolution FROM memory_conflicts").fetchone() == (
            "resolved",
            "reject-candidate",
        )
        supersedes_count = connection.execute(
            "SELECT COUNT(*) FROM memory_relations WHERE relation = 'supersedes'"
        ).fetchone()[0]
        assert connection.execute("SELECT status FROM memory_revalidations").fetchone() == (
            "confirmed",
        )
        transition_count = connection.execute(
            "SELECT COUNT(*) FROM memory_transitions WHERE actor_id = 'tanaka'"
        ).fetchone()[0]
    assert transition_count >= 5
    assert supersedes_count == 1


def _seed_m5_memories(database: Path) -> dict[str, str]:
    """M5 生命周期 E2E 用の異なる状態と範囲を持つメモリを保存する."""
    from datetime import UTC, datetime, timedelta

    from self_improving_code_review.domain.common.enums import (
        MemoryCategory,
        MemoryPolarity,
        MemoryStatus,
    )
    from self_improving_code_review.domain.common.values import (
        MemoryId,
        MemoryVersion,
        ProjectId,
    )
    from self_improving_code_review.domain.memory.models import (
        MemoryContent,
        MemoryRecord,
        MemoryTransition,
    )
    from self_improving_code_review.domain.memory.scope import MemoryScope
    from self_improving_code_review.infrastructure.persistence.short_transaction import (
        ShortTransactionMemoryRepository,
    )
    from self_improving_code_review.infrastructure.persistence.sqlite import (
        SqliteUnitOfWorkFactory,
    )

    identifiers = {
        "active": "11111111-1111-4111-8111-111111111111",
        "candidate": "22222222-2222-4222-8222-222222222222",
        "due": "33333333-3333-4333-8333-333333333333",
        "superseded": "44444444-4444-4444-8444-444444444444",
        "replacement": "55555555-5555-4555-8555-555555555555",
    }
    now = datetime.now(UTC)
    specifications = (
        ("active", MemoryCategory.FRAMEWORK, MemoryStatus.ACTIVE, "strict", "src/**", False),
        ("candidate", MemoryCategory.FRAMEWORK, MemoryStatus.PENDING, "skip", "src/**", False),
        ("due", MemoryCategory.TESTING, MemoryStatus.ACTIVE, "test", "tests/**", True),
        (
            "superseded",
            MemoryCategory.ARCHITECTURE,
            MemoryStatus.ACTIVE,
            "legacy",
            "docs/legacy/**",
            False,
        ),
        (
            "replacement",
            MemoryCategory.ARCHITECTURE,
            MemoryStatus.ACTIVE,
            "current",
            "docs/current/**",
            False,
        ),
    )
    repository = ShortTransactionMemoryRepository(SqliteUnitOfWorkFactory(database))
    for key, category, status, fact, path, is_due in specifications:
        memory_id = MemoryId(identifiers[key])
        created_at = now - timedelta(days=60)
        valid_from = now - timedelta(days=30) if is_due else None
        review_after = now - timedelta(days=1) if is_due else None
        content = MemoryContent(
            memory_id=memory_id,
            version=MemoryVersion(1),
            category=category,
            scope=MemoryScope(paths=(path,)),
            fact=fact,
            review_instruction=f"Review {fact}",
            polarity=MemoryPolarity.REINFORCE,
            confidence=0.9,
            evidence_summary="M5 E2E fixture",
            created_by="fixture",
            created_at=created_at,
            valid_from=valid_from,
            review_after=review_after,
        )
        record = MemoryRecord(
            memory_id=memory_id,
            project_id=ProjectId("project-a"),
            current_version=MemoryVersion(1),
            current_status=status,
            lock_version=1,
            created_at=created_at,
            updated_at=created_at,
        )
        transition = MemoryTransition(
            memory_id=memory_id,
            memory_version=MemoryVersion(1),
            from_status=None,
            to_status=status,
            actor_id="fixture",
            reason="created",
            created_at=created_at,
        )
        repository.create(record, content, (), transition)
    return identifiers


def test_m5_lost_lease_prevents_finalize_and_next_owner_resumes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧所有者の確定を拒否し、次の実行者が固定入力から再開する."""
    repository, config, patch = _prepare_m5_review_repository(tmp_path)
    shared = ["--repository", str(repository), "--config", str(config)]
    _register_and_bind_project(shared, capsys)
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace("governance:\n  enabled: false", "governance:\n  enabled: true")
        .replace("execution_leases_enabled: false", "execution_leases_enabled: true"),
        encoding="utf-8",
    )
    import self_improving_code_review.cli.review_commands as review_commands

    original_builder = review_commands._build_run_lease_sessions
    lost_session = _LoseBeforeFinalizeLeaseSession()
    monkeypatch.setattr(
        review_commands,
        "_build_run_lease_sessions",
        lambda _config, _database, _clock, _ids: lambda: lost_session,
    )
    first_output = repository / ".review" / "lost-owner.json"
    first_code = main(
        [
            "review",
            *shared,
            "--mode",
            "rules",
            "--diff-file",
            str(patch),
            "--output",
            str(first_output),
        ]
    )

    error = json.loads(capsys.readouterr().err)
    assert first_code == 5
    assert error["error"]["code"] == "ConcurrencyConflictError"
    assert not first_output.exists()
    database = repository / ".review" / "review.db"
    with closing(sqlite3.connect(database)) as connection:
        first_run = connection.execute("SELECT run_id, status FROM review_runs").fetchone()
        assert first_run is not None
        assert first_run[1] == "running"
        assert connection.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1

    monkeypatch.setattr(review_commands, "_build_run_lease_sessions", original_builder)
    resumed_output = repository / ".review" / "resumed-owner.json"
    resumed_code = main(
        [
            "review",
            *shared,
            "--mode",
            "rules",
            "--diff-file",
            str(patch),
            "--output",
            str(resumed_output),
        ]
    )

    assert resumed_code == 0
    resumed = json.loads(resumed_output.read_text(encoding="utf-8"))
    assert resumed["run"]["run_id"] == first_run[0]
    assert resumed["run"]["status"] == "success"
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT status, resume_count FROM review_runs").fetchone() == (
            "success",
            1,
        )
        assert connection.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM run_leases").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 1
