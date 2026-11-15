from __future__ import annotations

import json
import runpy
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from self_improving_code_review.application.scm_metrics import ScmApiMetrics
from self_improving_code_review.cli.main import main
from self_improving_code_review.domain.scm import CommentEvent, EventPage


def test_module_entrypoint_exposes_review_command() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "self_improving_code_review.cli.main", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "解析 Git 差异" in completed.stdout
    assert "命令:" in completed.stdout
    assert "选项:" in completed.stdout
    assert (
        "{review,rules,feedback,memory,ground-truth,eval,adjudication,scm,governance,dashboard,benchmark}"
        in completed.stdout
    )


@pytest.mark.parametrize(
    ("command", "expected"),
    [("review", "差异来源"), ("memory", "记忆候选"), ("eval", "比较组")],
)
def test_subcommand_help_uses_simplified_chinese(command: str, expected: str) -> None:
    """下位コマンドのヘルプに簡体字中国語の説明を表示する。"""
    completed = subprocess.run(
        [sys.executable, "-m", "self_improving_code_review.cli.main", command, "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert expected in completed.stdout
    assert "オプション" not in completed.stdout


def test_cli_error_writes_readable_chinese_json(tmp_path: Path) -> None:
    """標準エラーに読み取れる中国語と安定したエラーコードを出力する。"""
    missing_config = tmp_path / "missing.yaml"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "self_improving_code_review.cli.main",
            "review",
            "--repository",
            str(tmp_path),
            "--config",
            str(missing_config),
            "--git-diff",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )
    assert completed.returncode == 1
    assert "无法读取配置文件" in completed.stderr
    assert json.loads(completed.stderr)["error"]["code"] == "InvalidInputError"


@pytest.mark.parametrize("action", ["import", "serve", "migrate", "check"])
def test_retired_web_commands_are_rejected(action: str, tmp_path: Path) -> None:
    """廃止した Web 操作を DB 作成前に拒否する。"""
    assert main(["web", action, "--repository", str(tmp_path), "--help"]) == 1
    assert not (tmp_path / ".review").exists()


@pytest.mark.case("IT-M1-015")
@pytest.mark.parametrize(
    ("payload", "added_line", "expected_code", "expected_status"),
    [
        (
            {"schema_version": 1, "findings": []},
            "+safe = 2",
            0,
            "success",
        ),
        (
            {"schema_version": 1, "findings": [], "unknown": True},
            "+safe = 2",
            2,
            "failed",
        ),
        (
            {"schema_version": 1, "findings": []},
            '+key = "AKIAIOSFODNN7EXAMPLE"',
            4,
            "blocked",
        ),
    ],
)
def test_review_command_runs_offline_end_to_end(
    tmp_path: Path,
    payload: dict[str, object],
    added_line: str,
    expected_code: int,
    expected_status: str,
) -> None:
    repository, config, patch = _prepare_repository(tmp_path, payload, added_line)
    output = tmp_path / "result.json"

    code = main(
        [
            "review",
            "--repository",
            str(repository),
            "--config",
            str(config),
            "--diff-file",
            str(patch),
            "--output",
            str(output),
        ]
    )

    assert code == expected_code
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["run"]["status"] == expected_status


def test_module_guard_converts_main_result_to_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_path = Path(__file__).parents[2] / "src/self_improving_code_review/cli/main.py"
    monkeypatch.setattr(sys, "argv", [str(module_path)])

    with pytest.raises(SystemExit) as captured:
        runpy.run_path(str(module_path), run_name="__main__")

    assert captured.value.code == 1


@pytest.mark.case("IT-M2-001", "IT-M2-009", "IT-M2-012", "IT-M2-014")
def test_m2_feedback_approval_and_memory_review_end_to_end(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """フィードバックから承認済みメモリを次回レビューへ注入する。"""
    repository, config, patch = _prepare_m2_repository(tmp_path)
    first_output = tmp_path / "first-review.json"
    assert _run_review(repository, config, patch, first_output, "rules") == 0
    first = json.loads(first_output.read_text(encoding="utf-8"))

    feedback = _record_feedback(repository, config, first["run"]["run_id"], capsys)
    memory_id = feedback["candidate_memory_id"]
    assert feedback["candidate_status"] == "success"
    assert _approve_memory(repository, config, memory_id, capsys) == "active"

    memory_output = tmp_path / "memory-review.json"
    assert _run_review(repository, config, patch, memory_output, "memory") == 0
    memory_review = json.loads(memory_output.read_text(encoding="utf-8"))
    snapshot_id = memory_review["run"]["versions"]["memory_snapshot_id"]
    assert snapshot_id is not None

    replay_output = tmp_path / "memory-review-replay.json"
    assert _run_review(repository, config, patch, replay_output, "memory") == 0
    replay = json.loads(replay_output.read_text(encoding="utf-8"))
    assert replay["run"]["run_id"] == memory_review["run"]["run_id"]
    assert replay["run"]["versions"]["memory_snapshot_id"] == snapshot_id
    _assert_m2_database(repository / ".review/test.db")


def test_scm_feedback_sync_records_feedback_and_extracts_memory_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """公開済み GitHub 行コメントへの返信を M2 一連処理へ接続する。"""
    repository, config, patch = _prepare_m2_repository(tmp_path)
    review_output = tmp_path / "review.json"
    assert _run_review(repository, config, patch, review_output, "rules") == 0
    run_id = json.loads(review_output.read_text(encoding="utf-8"))["run"]["run_id"]
    database = repository / ".review/test.db"
    _insert_scm_publication(database, run_id)
    config.write_text(
        config.read_text(encoding="utf-8") + "scm:\n"
        "  enabled: true\n"
        "  repository_external_id: owner/repository\n"
        "  bot_actor_external_id: review-bot\n"
        "  feedback_sync_enabled: true\n"
        "  feedback_actor_mapping: {github-reviewer: maintainer}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("REVIEW_SCM_TOKEN", "test-token")

    class FakeGitHubClient:
        """ネットワークを使用しない SCM 契約模擬。"""

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            """構成引数を受け取り、外部状態を持たない。"""

        def get_authenticated_actor(self) -> str:
            """設定済みボット名を返す。"""
            return "review-bot"

        def get_metrics(self) -> ScmApiMetrics:
            """模擬 API の固定計測値を返す。"""
            return ScmApiMetrics(call_count=2)

        def list_comment_events(
            self,
            _ref: object,
            *,
            cursor: str | None,
        ) -> EventPage:
            """一件の明示フィードバック返信を返す。"""
            assert cursor is None
            return EventPage(
                events=(
                    CommentEvent(
                        event_external_id="review-comment:20",
                        comment_external_id="20",
                        target_comment_external_id="10",
                        actor_external_id="github-reviewer",
                        body='/review-feedback true-positive --reason "実際に発生します"',
                        updated_at=datetime(2026, 9, 25, tzinfo=UTC),
                    ),
                ),
                next_cursor="2026-09-25T00:00:00+00:00",
            )

    import self_improving_code_review.cli.scm_commands as scm_commands

    monkeypatch.setattr(scm_commands, "GitHubScmClient", FakeGitHubClient)
    output = tmp_path / "sync.json"

    code = main(
        [
            "scm",
            "sync-feedback",
            "--repository",
            str(repository),
            "--config",
            str(config),
            "--change",
            "42",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    sync = json.loads(output.read_text(encoding="utf-8"))["scm_feedback_sync"]
    assert sync["processed_count"] == 1
    assert sync["api_metrics"] == {
        "call_count": 2,
        "retry_count": 0,
        "retry_wait_ms": 0,
    }
    with closing(sqlite3.connect(database)) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("feedback", "memory_extraction_runs", "memories", "scm_feedback_events")
        }
        event = connection.execute(
            "SELECT mapped_feedback_id, status, payload_hash FROM scm_feedback_events"
        ).fetchone()
    assert counts == {
        "feedback": 1,
        "memory_extraction_runs": 1,
        "memories": 1,
        "scm_feedback_events": 1,
    }
    assert event is not None
    assert event[0] is not None
    assert event[1] == "processed"
    assert len(event[2]) == 64


def _insert_scm_publication(database: Path, run_id: str) -> None:
    """E2E 対象の公開済み行コメントを作成する。"""
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO scm_publications("
            "publication_id, run_id, finding_id, provider, repository_external_id, "
            "change_external_id, head_sha, fingerprint_hash, kind, external_comment_id, "
            "request_hash, status, created_at, updated_at"
            ") VALUES('publication-1', ?, 'B001-F001', 'github', 'owner/repository', "
            "'42', ?, 'fingerprint', 'inline', '10', 'request', 'published', ?, ?)",
            (
                run_id,
                "2" * 40,
                "2026-09-25T00:00:00+00:00",
                "2026-09-25T00:00:00+00:00",
            ),
        )
        connection.commit()


def _prepare_repository(
    tmp_path: Path,
    payload: dict[str, object],
    added_line: str,
) -> tuple[Path, Path, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "src/app.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "add", "src/app.py"], cwd=repository, check=True)
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

    prompt_source = Path("prompts/review_general_v1.yaml")
    prompt_target = repository / "prompts/review_general_v1.yaml"
    prompt_target.parent.mkdir()
    prompt_target.write_text(prompt_source.read_text(encoding="utf-8"), encoding="utf-8")

    dataset = repository / "datasets/test/review_cases.json"
    dataset.parent.mkdir(parents=True)
    dataset.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model": "local-e2e-dataset-v1",
                "default_result": {
                    "payload": payload,
                    "usage": {
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "latency_ms": 1,
                        "cost_usd": "0",
                    },
                },
                "cases": [],
            }
        ),
        encoding="utf-8",
    )
    config = repository / "test.yaml"
    config.write_text(
        """schema_version: 1
project_id: e2e-test
database:
  path: .review/test.db
llm:
  provider: dataset
  dataset_path: datasets/test/review_cases.json
security:
  allow_external_source: false
""",
        encoding="utf-8",
    )
    patch = repository / "change.patch"
    patch.write_text(
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1 +1,2 @@\n"
        " value = 1\n"
        f"{added_line}\n",
        encoding="utf-8",
    )
    return repository, config, patch


def _prepare_m2_repository(tmp_path: Path) -> tuple[Path, Path, Path]:
    """M2 一連処理用の Git、Prompt、Dataset、設定を作成する。"""
    review_payload = {
        "schema_version": 1,
        "findings": [
            {
                "file": "src/app.py",
                "start_line": 2,
                "end_line": 2,
                "category": "correctness",
                "issue_type": "null-dereference",
                "rule_id": "GENERAL.NULL_DEREFERENCE",
                "primary_entity": "user",
                "severity": "high",
                "title": "None の参照",
                "description": "None の可能性がある値を参照します。",
                "evidence": {"quote": "unsafe = user.name", "reason": "追加行です。"},
                "suggestion": "参照前に検証してください。",
                "needs_context": False,
            }
        ],
    }
    repository, config, patch = _prepare_repository(
        tmp_path,
        review_payload,
        "+unsafe = user.name",
    )
    memory_prompt = Path("prompts/memory_extraction_v1.yaml")
    (repository / "prompts/memory_extraction_v1.yaml").write_text(
        memory_prompt.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _write_m2_dataset(repository, review_payload)
    config.write_text(
        config.read_text(encoding="utf-8")
        + "memory:\n  is_injection_enabled: true\n  maintainers: [maintainer]\n",
        encoding="utf-8",
    )
    return repository, config, patch


def _write_m2_dataset(repository: Path, review_payload: dict[str, object]) -> None:
    """レビューとメモリ抽出を切り替える固定応答を保存する。"""
    memory_payload = {
        "schema_version": 1,
        "category": "architecture",
        "scope": {
            "languages": ["python"],
            "frameworks": [],
            "paths": ["src/**"],
            "symbols": [],
            "keywords": ["user"],
            "risk_tags": [],
        },
        "fact": "user は None の可能性があります。",
        "review_instruction": "属性参照前の None 検証を確認してください。",
        "polarity": "reinforce",
        "confidence": 0.9,
        "evidence_summary": "確認済み指摘から抽出しました。",
    }
    dataset = repository / "datasets/test/review_cases.json"
    document = json.loads(dataset.read_text(encoding="utf-8"))
    document["default_result"]["payload"] = review_payload
    document["cases"] = [
        {
            "case_id": "memory-extraction",
            "match_all": ["コードレビューのフィードバックから再利用可能な知識"],
            "result": {
                "payload": memory_payload,
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 8,
                    "latency_ms": 1,
                    "cost_usd": "0",
                },
            },
        }
    ]
    dataset.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def _run_review(
    repository: Path,
    config: Path,
    patch: Path,
    output: Path,
    mode: str,
) -> int:
    """指定モードのレビュー CLI を実行する。"""
    return main(
        [
            "review",
            "--repository",
            str(repository),
            "--config",
            str(config),
            "--mode",
            mode,
            "--diff-file",
            str(patch),
            "--output",
            str(output),
        ]
    )


def _record_feedback(
    repository: Path,
    config: Path,
    run_id: str,
    capsys: pytest.CaptureFixture[str],
) -> dict[str, object]:
    """既存指摘を評価し、候補抽出結果を返す。"""
    code = main(
        [
            "feedback",
            "finding",
            "--repository",
            str(repository),
            "--config",
            str(config),
            "--run",
            run_id,
            "--finding",
            "B001-F001",
            "--label",
            "true-positive",
            "--reason",
            "実際に発生する問題です。",
            "--actor",
            "maintainer",
        ]
    )
    assert code == 0
    return json.loads(capsys.readouterr().out)


def _approve_memory(
    repository: Path,
    config: Path,
    memory_id: object,
    capsys: pytest.CaptureFixture[str],
) -> str:
    """抽出候補を承認し、遷移後状態を返す。"""
    code = main(
        [
            "memory",
            "approve",
            str(memory_id),
            "--repository",
            str(repository),
            "--config",
            str(config),
            "--actor",
            "maintainer",
            "--reason",
            "再利用可能な知識として確認しました。",
            "--lock-version",
            "1",
        ]
    )
    assert code == 0
    return str(json.loads(capsys.readouterr().out)["status"])


def _assert_m2_database(database: Path) -> None:
    """M2 一連処理の主要な永続化結果を検証する。"""
    with closing(sqlite3.connect(database)) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "feedback",
                "memory_extraction_runs",
                "memories",
                "memory_versions",
                "memory_transitions",
                "memory_snapshots",
                "memory_snapshot_items",
                "finding_memories",
            )
        }
    assert counts == {
        "feedback": 1,
        "memory_extraction_runs": 1,
        "memories": 1,
        "memory_versions": 1,
        "memory_transitions": 2,
        "memory_snapshots": 1,
        "memory_snapshot_items": 1,
        "finding_memories": 1,
    }
