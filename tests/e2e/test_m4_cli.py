"""M4 SCM 連携を CLI から保存結果まで検証する一連処理テスト."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from self_improving_code_review.application.config.settings import ScmConfig
from self_improving_code_review.application.scm_metrics import ScmApiMetrics
from self_improving_code_review.cli.main import main
from self_improving_code_review.domain.common.errors import ScmClientError
from self_improving_code_review.domain.scm import (
    ChangeRef,
    CommentEvent,
    CreateComment,
    EventPage,
    PublishedComment,
    ScmChange,
    ScmChangeState,
    ScmProvider,
    UpdateComment,
)

_TOKEN = "m4-e2e-token-do-not-store"


def test_m4_review_publish_reuse_and_feedback_sync_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """固定 SHA のレビュー、冪等投稿、M2 同期を一つの CLI 経路で完了する."""
    repository, config, change = _prepare_repository(tmp_path)
    client = _FakeGitHubClient(change)
    _install_client(monkeypatch, client)

    dry_output = repository / ".review/dry-run.json"
    assert _review(repository, config, dry_output, should_publish=False) == 0
    dry_run = _read_json(dry_output)
    assert dry_run["scm"]["dry_run"] is True
    assert dry_run["scm"]["plans"][0]["kind"] == "inline"
    assert dry_run["scm"]["publications"] == []
    assert client.created_requests == []

    publish_output = repository / ".review/published.json"
    assert _review(repository, config, publish_output, should_publish=True) == 0
    published = _read_json(publish_output)
    assert published["scm"]["publications"][0]["operation"] == "created"
    assert published["scm"]["metrics"]["created_count"] == 1
    assert len(client.created_requests) == 1

    replay_output = repository / ".review/reused.json"
    assert _review(repository, config, replay_output, should_publish=True) == 0
    replayed = _read_json(replay_output)
    assert replayed["scm"]["publications"][0]["operation"] == "reused"
    assert replayed["scm"]["metrics"]["reused_count"] == 1
    assert len(client.created_requests) == 1

    client.feedback_target_id = published["scm"]["publications"][0]["external_comment_id"]
    sync_output = repository / ".review/sync.json"
    assert _sync_feedback(repository, config, sync_output) == 0
    synchronized = _read_json(sync_output)["scm_feedback_sync"]
    assert synchronized["processed_count"] == 1
    assert synchronized["duplicate_count"] == 0

    database = repository / ".review/m4.db"
    with closing(sqlite3.connect(database)) as connection:
        publication = connection.execute(
            "SELECT status, external_comment_id FROM scm_publications"
        ).fetchone()
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("feedback", "memories", "scm_feedback_events")
        }
        publication_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(scm_publications)")
        }
    assert publication == ("published", client.feedback_target_id)
    assert counts == {"feedback": 1, "memories": 1, "scm_feedback_events": 1}
    assert "body" not in publication_columns
    rendered = "".join(
        path.read_text(encoding="utf-8")
        for path in (dry_output, publish_output, replay_output, sync_output)
    )
    assert _TOKEN not in rendered
    assert _TOKEN.encode() not in database.read_bytes()


def test_m4_publish_stops_when_head_changes_after_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """レビュー後の先端 SHA 変更を検出し、コメントを一件も作成しない."""
    repository, config, change = _prepare_repository(tmp_path)
    changed = replace(change, head_sha="3" * 40)
    client = _FakeGitHubClient(change, change_sequence=[change, changed])
    _install_client(monkeypatch, client)

    code = _review(
        repository,
        config,
        repository / ".review/head-changed.json",
        should_publish=True,
    )

    assert code == 1
    assert client.created_requests == []


@pytest.mark.parametrize("status_code", [403, 422])
def test_m4_publication_exposes_safe_http_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    """投稿失敗時に外部メッセージを伏せて HTTP 状態を出力・保存する."""
    repository, config, change = _prepare_repository(tmp_path)
    client = _FakeGitHubClient(change, create_status_code=status_code)
    _install_client(monkeypatch, client)
    output = repository / ".review/publication-failed.json"

    assert _review(repository, config, output, should_publish=True) == 2
    result = _read_json(output)
    assert result["run"]["status"] == "success"
    publication = result["scm"]["publications"][0]
    assert publication["status"] == "failed"
    assert publication["external_comment_id"] is None
    assert publication["safe_error_code"] == f"HTTP_{status_code}"
    assert _TOKEN not in output.read_text(encoding="utf-8")
    with closing(sqlite3.connect(repository / ".review/m4.db")) as connection:
        stored = connection.execute("SELECT safe_error_code FROM scm_publications").fetchone()
    assert stored == (f"HTTP_{status_code}",)


@dataclass
class _FakeGitHubClient:
    """外部通信を行わず GitHub の状態とコメントを保持するテスト代替."""

    change: ScmChange
    change_sequence: list[ScmChange] | None = None
    create_status_code: int | None = None
    created_requests: list[CreateComment] = field(default_factory=list)
    updated_requests: list[UpdateComment] = field(default_factory=list)
    feedback_target_id: str | None = None
    api_calls: int = 0
    change_index: int = 0

    def begin_session(self) -> None:
        """CLI 一回分の計測値と変更取得順だけを初期化する."""
        self.api_calls = 0
        self.change_index = 0

    def get_authenticated_actor(self) -> str:
        """設定済みボットの外部 ID を返す."""
        self.api_calls += 1
        return "review-bot"

    def get_change(self, ref: ChangeRef) -> ScmChange:
        """呼出順に固定済み変更要求を返す."""
        assert ref == self.change.ref
        self.api_calls += 1
        values = self.change_sequence or [self.change]
        index = min(self.change_index, len(values) - 1)
        self.change_index += 1
        return values[index]

    def create_comment(self, ref: ChangeRef, request: CreateComment) -> PublishedComment:
        """作成要求を記録して安定した外部 ID を返す."""
        assert ref == self.change.ref
        self.api_calls += 1
        self.created_requests.append(request)
        if self.create_status_code is not None:
            raise ScmClientError(_TOKEN, status_code=self.create_status_code)
        identifier = str(100 + len(self.created_requests))
        return PublishedComment(identifier, f"https://github.test/comments/{identifier}")

    def find_created_comment(
        self,
        ref: ChangeRef,
        request: CreateComment,
    ) -> PublishedComment | None:
        """作成応答消失の照合対象がないことを返す."""
        del request
        assert ref == self.change.ref
        self.api_calls += 1
        return None

    def update_comment(self, ref: ChangeRef, request: UpdateComment) -> PublishedComment:
        """更新要求を記録して同じ外部 ID を返す."""
        assert ref == self.change.ref
        self.api_calls += 1
        self.updated_requests.append(request)
        return PublishedComment(
            request.comment_external_id,
            f"https://github.test/comments/{request.comment_external_id}",
        )

    def list_comment_events(
        self,
        ref: ChangeRef,
        *,
        cursor: str | None,
    ) -> EventPage:
        """公開済み行コメントへの明示評価を一件返す."""
        assert ref == self.change.ref
        assert cursor is None
        assert self.feedback_target_id is not None
        self.api_calls += 1
        updated_at = datetime(2026, 9, 25, tzinfo=UTC)
        return EventPage(
            events=(
                CommentEvent(
                    event_external_id="review-comment:201",
                    comment_external_id="201",
                    target_comment_external_id=self.feedback_target_id,
                    actor_external_id="github-reviewer",
                    body='/review-feedback true-positive --reason "実際に発生します"',
                    updated_at=updated_at,
                ),
            ),
            next_cursor=updated_at.isoformat(),
        )

    def get_metrics(self) -> ScmApiMetrics:
        """現在の模擬 API 呼出回数を返す."""
        return ScmApiMetrics(call_count=self.api_calls)


def _install_client(
    monkeypatch: pytest.MonkeyPatch,
    client: _FakeGitHubClient,
) -> None:
    """CLI が生成する GitHub クライアントを状態共有する模擬へ置換する."""
    import self_improving_code_review.cli.scm_commands as scm_commands

    def factory(config: ScmConfig, token: str) -> _FakeGitHubClient:
        """認証値を検証し、一回分の計測を初期化して模擬を返す."""
        assert config.repository_external_id == "owner/repository"
        assert token == _TOKEN
        client.begin_session()
        return client

    monkeypatch.setenv("REVIEW_SCM_TOKEN", _TOKEN)
    monkeypatch.setattr(scm_commands, "GitHubScmClient", factory)


def _prepare_repository(tmp_path: Path) -> tuple[Path, Path, ScmChange]:
    """基準と先端コミット、固定応答、M4 設定を持つ一時リポジトリを作成する."""
    repository = tmp_path / "repository"
    (repository / "src").mkdir(parents=True)
    (repository / "src/app.py").write_text("value = 1\n", encoding="utf-8")
    prompt_directory = repository / "prompts"
    prompt_directory.mkdir()
    for name in ("review_general_v1.yaml", "memory_extraction_v1.yaml"):
        (prompt_directory / name).write_text(
            (Path("prompts") / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    _write_dataset(repository / "datasets/m4.json")
    config = _write_config(repository)
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    _commit(repository, "base")
    base_sha = _revision(repository)
    (repository / "src/app.py").write_text("value = 1\nunsafe = user.name\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/app.py"], cwd=repository, check=True)
    _commit(repository, "head")
    head_sha = _revision(repository)
    subprocess.run(["git", "checkout", "--detach", "-q", base_sha], cwd=repository, check=True)
    ref = ChangeRef(ScmProvider.GITHUB, "owner/repository", "42")
    return (
        repository,
        config,
        ScmChange(
            ref=ref,
            base_sha=base_sha,
            head_sha=head_sha,
            state=ScmChangeState.OPEN,
            is_draft=False,
            source_repository_external_id="owner/repository",
            updated_at=datetime(2026, 9, 25, tzinfo=UTC),
        ),
    )


def _write_dataset(path: Path) -> None:
    """レビューとメモリ抽出用の固定応答を保存する."""
    path.parent.mkdir(parents=True)
    usage = {"input_tokens": 2, "output_tokens": 1, "latency_ms": 1, "cost_usd": "0"}
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
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model": "local-m4-e2e-v1",
                "default_result": {"payload": review_payload, "usage": usage},
                "cases": [
                    {
                        "case_id": "memory-extraction",
                        "match_all": ["コードレビューのフィードバックから再利用可能な知識"],
                        "result": {"payload": memory_payload, "usage": usage},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_config(repository: Path) -> Path:
    """外部 LLM を禁止し、模擬 GitHub だけを有効にする設定を保存する."""
    config = repository / "m4.yaml"
    config.write_text(
        "schema_version: 1\n"
        "project_id: m4-e2e\n"
        "database:\n  path: .review/m4.db\n"
        "llm:\n  provider: dataset\n  dataset_path: datasets/m4.json\n"
        "review:\n  mode: baseline\n"
        "memory:\n  maintainers: [maintainer]\n"
        "security:\n  allow_external_source: false\n"
        "scm:\n"
        "  enabled: true\n"
        "  repository_external_id: owner/repository\n"
        "  bot_actor_external_id: review-bot\n"
        "  publish_enabled: true\n"
        "  feedback_sync_enabled: true\n"
        "  feedback_actor_mapping: {github-reviewer: maintainer}\n",
        encoding="utf-8",
    )
    return config


def _review(repository: Path, config: Path, output: Path, *, should_publish: bool) -> int:
    """M4 レビューを CLI 境界から実行する."""
    arguments = [
        "scm",
        "review",
        "--repository",
        str(repository),
        "--config",
        str(config),
        "--change",
        "42",
        "--mode",
        "baseline",
        "--output",
        str(output),
    ]
    if should_publish:
        arguments.append("--publish")
    return main(arguments)


def _sync_feedback(repository: Path, config: Path, output: Path) -> int:
    """M4 フィードバック同期を CLI 境界から実行する."""
    return main(
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


def _commit(repository: Path, message: str) -> None:
    """テスト用の固定 Git 利用者でコミットする."""
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            message,
        ],
        cwd=repository,
        check=True,
    )


def _revision(repository: Path) -> str:
    """現在の完全な Git SHA を返す."""
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _read_json(path: Path) -> dict[str, object]:
    """UTF-8 の JSON オブジェクトを返す."""
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value
