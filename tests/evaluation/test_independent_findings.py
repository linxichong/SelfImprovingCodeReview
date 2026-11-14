"""独立した問題の保持と空メモリ時の要求同一性を固定応答で検証する。"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from self_improving_code_review.application.ports.llm_client import LlmRequest, LlmResult
from self_improving_code_review.cli.main import main
from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.infrastructure.llm import DatasetLlmClient


@pytest.mark.smoke
@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, ("", "")), ({}, ("", "")), ({"name": "N", "title": "T"}, ("N", "T"))],
)
def test_safe_development_example_handles_optional_and_missing_keys(
    value: dict[str, str] | None, expected: tuple[str, str]
) -> None:
    """安全対照例が None・空辞書・値ありで例外を起こさないことを実行確認する。"""
    patch = Path("datasets/development/independent_findings_safe.patch").read_text(encoding="utf-8")
    changed_file = UnifiedDiffParser().parse(patch).files[0]
    source = "\n".join(
        line.text for hunk in changed_file.hunks for line in hunk.lines if line.new_line is not None
    )
    check = (
        f"\nassert primary({value!r}) == {expected[0]!r}\n"
        f"assert secondary({value!r}) == {expected[1]!r}\n"
    )
    subprocess.run([sys.executable, "-c", source + check], check=True, capture_output=True)


@pytest.mark.smoke
@pytest.mark.parametrize(
    ("prompt_root", "expected_version"),
    [
        ("prompts", 5),
        ("prompts/candidates", 6),
        ("prompts/candidates/v7", 7),
        ("prompts/candidates/v8", 8),
    ],
)
@pytest.mark.parametrize("outcome", ["findings", "safe", "context"])
def test_independent_findings_survive_empty_memory_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt_root: str,
    expected_version: int,
    outcome: str,
) -> None:
    """実 CLI・SQLite で独立指摘を保持し、空メモリによる要求差分を拒否する。"""
    is_safe = outcome == "safe"
    needs_context = outcome == "context"
    repository = tmp_path / "repository"
    repository.mkdir()
    _initialize_repository(repository)
    prompts = repository / "prompts"
    prompts.mkdir()
    shutil.copyfile(
        Path(prompt_root) / "review_general_v1.yaml", prompts / "review_general_v1.yaml"
    )
    suffix = "_safe" if is_safe else ""
    patch = repository / "input.patch"
    shutil.copyfile(Path(f"datasets/development/independent_findings{suffix}.patch"), patch)
    findings = (
        [] if is_safe else [{**finding, "needs_context": needs_context} for finding in _findings()]
    )
    payload = {"schema_version": 1, "findings": findings}
    (repository / "dataset.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model": "independent-findings-dataset-v1",
                "default_result": {"payload": payload},
            }
        ),
        encoding="utf-8",
    )
    config = repository / "review.yaml"
    config.write_text(
        "schema_version: 1\nproject_id: independent-findings\n"
        "database:\n  path: .review/test.db\n"
        "llm:\n  provider: dataset\n  dataset_path: dataset.json\n"
        "memory:\n  is_injection_enabled: true\n"
        "security:\n  allow_external_source: false\n",
        encoding="utf-8",
    )
    requests: list[LlmRequest] = []
    generate = DatasetLlmClient.generate_structured

    def capture_request(client: DatasetLlmClient, request: LlmRequest) -> LlmResult:
        """実データセットアダプターへ委譲し、比較用要求をメモリ内だけに保持する。"""
        requests.append(request)
        return generate(client, request)

    monkeypatch.setattr(DatasetLlmClient, "generate_structured", capture_request)
    expected_symbols = [] if is_safe or needs_context else ["primary", "secondary"]
    for mode in ("rules", "memory"):
        output = repository / f"{mode}.json"
        assert (
            main(
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
            == 0
        )
        document = json.loads(output.read_text(encoding="utf-8"))
        assert document["run"]["status"] == "success"
        assert document["run"]["versions"]["prompt_version"] == (
            f"review_general_v1@{expected_version}"
        )
        with closing(sqlite3.connect(repository / ".review/test.db")) as connection, connection:
            rows = connection.execute(
                "SELECT enclosing_symbol, fingerprint FROM findings "
                "WHERE run_id = ? AND is_main = 1 ORDER BY start_line",
                (document["run"]["run_id"],),
            ).fetchall()
            assert [row[0] for row in rows] == expected_symbols
            assert len({row[1] for row in rows}) == len(expected_symbols)
            references = connection.execute(
                "SELECT enclosing_symbol, reflection_status FROM findings "
                "WHERE run_id = ? AND is_main = 0 ORDER BY start_line",
                (document["run"]["run_id"],),
            ).fetchall()
            assert references == (
                [("primary", "needs-context"), ("secondary", "needs-context")]
                if needs_context
                else []
            )
            snapshot = document["run"]["versions"]["memory_snapshot_id"]
            if mode == "memory":
                assert snapshot is not None
                assert connection.execute(
                    "SELECT COUNT(*) FROM memory_snapshots WHERE memory_snapshot_id = ?",
                    (snapshot,),
                ).fetchone() == (1,)
                assert connection.execute(
                    "SELECT COUNT(*) FROM memory_snapshot_items WHERE memory_snapshot_id = ?",
                    (snapshot,),
                ).fetchone() == (0,)
            else:
                assert snapshot is None
    assert len(requests) == 2
    assert requests[0] == requests[1]


def _initialize_repository(repository: Path) -> None:
    """レビュー入力を取得できる最小のローカル Git 履歴を作成する。"""
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "initial",
        ],
        check=True,
    )


def _findings() -> list[dict[str, object]]:
    """評価用正解集合に依存しない開発例の二つの固定候補を返す。"""
    return [
        {
            "file": "src/labels.py",
            "start_line": line,
            "end_line": line,
            "category": "correctness",
            "issue_type": "null-dereference",
            "rule_id": "GENERAL.NULL_DEREFERENCE",
            "primary_entity": "value",
            "severity": "high",
            "title": "None のメソッド参照",
            "description": "None を受け取るとメソッド参照に失敗します。",
            "evidence": {
                "quote": f'return value.get("{key}", "")',
                "reason": "None 検証がありません。",
            },
            "suggestion": "None の場合を処理してください。",
            "needs_context": False,
        }
        for line, key in ((2, "name"), (6, "title"))
    ]
