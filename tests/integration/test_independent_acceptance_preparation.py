"""独立受入集合の規模・位置・秘密境界・未承認門禁・再現性を検証する。"""

from __future__ import annotations

import ast
import json
import re
import runpy
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from self_improving_code_review.cli import ground_truth_commands
from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.infrastructure.persistence import SqliteUnitOfWorkFactory
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.models import ScanProfile, SourceKind, SourcePayload
from self_improving_code_review.security.secret_scanner import DetectSecretsScanner


@pytest.mark.parametrize(
    ("version", "issue_count", "problem_case_count"), [(8, 30, 20), (9, 31, 21), (10, 32, 21)]
)
def test_independent_set_is_reproducible_and_cannot_publish_without_review(
    tmp_path: Path, version: int, issue_count: int, problem_case_count: int
) -> None:
    """新しい全ケースを検証し、承認の偽装や既存先の上書きを防止する。"""
    database = tmp_path / "memory.db"
    SqliteUnitOfWorkFactory(database)
    script = Path(
        "datasets/evaluation/prepare_independent_v8.py"
        if version == 8
        else f"datasets/evaluation/prepare_test_v{version}_candidate.py"
    ).resolve()
    documents = []
    for name in ("first", "second"):
        repository = tmp_path / name
        command = [
            sys.executable,
            "-W",
            "error::ResourceWarning",
            str(script),
            "--repository",
            str(repository),
            "--memory-database",
            str(database),
            "--project-id",
            "m2-formal-evaluation-v3",
        ]
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        assert "ResourceWarning" not in completed.stderr
        manifest = json.loads(completed.stdout)
        assert manifest["publication_ready"] is False
        assert manifest["dataset_version"] == version
        assert manifest.get("is_unseen_holdout") is (None if version == 8 else False)
        candidate = repository / f"datasets/evaluation/ground-truth-test-v{version}.candidate.json"
        document = json.loads(candidate.read_text(encoding="utf-8"))
        documents.append(document)
        assert document["schema_version"] == 2
        assert document["version"] == version
        assert len(document["cases"]) == 32
        assert len(document["issues"]) == issue_count
        assert [issue["sequence"] for issue in document["issues"]] == list(
            range(1, issue_count + 1)
        )
        if version == 10:
            assert document["issues"][30]["file"] == "src/money/conversion.py"
            assert document["issues"][31]["symbol"] == "expand_zip"
            assert document["issues"][31]["issue_type"] == "archive-extraction-failure"
        critical = [issue for issue in document["issues"] if issue["severity"] == "critical"]
        assert len(critical) == 8
        assert len({issue["change_ref"] for issue in critical}) == 4
        assert len({issue["change_ref"] for issue in document["issues"]}) == problem_case_count
        _check_positions_and_secrets(repository, document)
        assert (repository / "prompts/review_general_v1.yaml").read_bytes() == Path(
            "prompts/candidates/v8/review_general_v1.yaml"
        ).read_bytes()
        assert (repository / "review-packet.md").is_file()
        for item in (*document["cases"], *document["issues"]):
            assert item["created_by"] == item["verified_by"]
            assert item["approved_by"] == "REVIEW_REQUIRED"
        parsed = ground_truth_commands._GroundTruthDocument.model_validate(document)
        with pytest.raises(InvalidInputError):
            ground_truth_commands._validate_document(parsed)
        before = candidate.read_bytes()
        repeated = subprocess.run(command, check=False, capture_output=True, text=True)
        assert repeated.returncode == 1
        assert re.search(re.escape(Messages.EVALUATION_CANDIDATE_TARGET_EXISTS), repeated.stderr)
        assert candidate.read_bytes() == before
    assert documents[0] == documents[1]


def test_v9_catalog_only_changes_the_two_adjudicated_cases() -> None:
    """旧版の問題を保持し、金額漏標と明示プラットフォームだけを訂正する。"""
    old = json.loads(Path("datasets/evaluation/independent_v8/catalog.json").read_text())
    new = json.loads(Path("datasets/evaluation/test_v9/catalog.json").read_text())
    assert old["schema_version"] == new["schema_version"] == 1
    assert len(old["cases"]) == len(new["cases"]) == 32
    old_cases = {case["path"]: case for case in old["cases"]}
    new_cases = {case["path"]: case for case in new["cases"]}
    assert old_cases.keys() == new_cases.keys()
    changed = {path for path in old_cases if old_cases[path] != new_cases[path]}
    assert changed == {"src/money/conversion.py", "src/archive_store/member.py"}
    for path in changed:
        old_tree = ast.parse(old_cases[path]["source"])
        new_tree = ast.parse(new_cases[path]["source"])
        old_function = next(node for node in old_tree.body if isinstance(node, ast.FunctionDef))
        new_function = next(node for node in new_tree.body if isinstance(node, ast.FunctionDef))
        assert ast.dump(ast.Module(body=old_function.body[1:], type_ignores=[])) == ast.dump(
            ast.Module(body=new_function.body[1:], type_ignores=[])
        )
    assert "POSIX" in new_cases["src/archive_store/member.py"]["source"]
    assert new_cases["src/archive_store/member.py"]["issues"] == []
    money = new_cases["src/money/conversion.py"]
    assert old_cases["src/money/conversion.py"]["issues"] == []
    assert len(money["issues"]) == 1
    assert money["issues"][0]["symbol"] == "cents"
    assert money["issues"][0]["issue_type"] == "numeric-precision-error"
    assert money["issues"][0]["severity"] == "medium"


def test_v10_catalog_preserves_prior_issue_numbers_and_archive_behavior() -> None:
    """31問題の番号を固定したまま、ZIP 展開失敗32番と契約説明だけを追加する。"""
    old = json.loads(Path("datasets/evaluation/test_v9/catalog.json").read_text())
    new = json.loads(Path("datasets/evaluation/test_v10/catalog.json").read_text())
    assert len(old["cases"]) == len(new["cases"]) == 32
    old_issues = [(case["path"], issue) for case in old["cases"] for issue in case["issues"]]
    new_issues = {
        issue["sequence"]: (
            case["path"],
            {key: value for key, value in issue.items() if key != "sequence"},
        )
        for case in new["cases"]
        for issue in case["issues"]
    }
    assert sorted(new_issues) == list(range(1, 33))
    for sequence, original in enumerate(old_issues, 1):
        assert new_issues[sequence] == original
    assert new_issues[32][0] == "src/uploads/archives.py"
    for old_case, new_case in zip(old["cases"], new["cases"], strict=True):
        assert old_case["path"] == new_case["path"]
        if old_case["path"] != "src/uploads/archives.py":
            assert old_case["source"] == new_case["source"]
            continue
        trees = [ast.parse(case["source"]) for case in (old_case, new_case)]
        for tree in trees:
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    node.body = node.body[1:]
        assert ast.dump(trees[0]) == ast.dump(trees[1])
        assert "入れ子の親ディレクトリも作成する" in new_case["source"]


@pytest.mark.parametrize("invalid_sequence", [0, 1, 33])
def test_invalid_explicit_issue_number_fails_before_creating_repository(
    tmp_path: Path, invalid_sequence: int
) -> None:
    """非正数・重複・欠番を持つカタログで候補先が作成されないことを確認する。"""
    catalog = json.loads(Path("datasets/evaluation/test_v10/catalog.json").read_text())
    catalog["cases"][0]["issues"][1]["sequence"] = invalid_sequence
    catalog_path = tmp_path / "invalid-catalog.json"
    catalog_path.write_text(json.dumps(catalog))
    repository = tmp_path / "candidate"
    database = tmp_path / "memory.db"
    SqliteUnitOfWorkFactory(database)
    bootstrap = (
        "from pathlib import Path\nfrom prepare_independent_v8 import main\n"
        f"raise SystemExit(main(version=10, catalog_path=Path({str(catalog_path)!r})))"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            bootstrap,
            "--repository",
            str(repository),
            "--memory-database",
            str(database),
            "--project-id",
            "m2-formal-evaluation-v3",
        ],
        cwd=Path("datasets/evaluation").resolve(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    if invalid_sequence != 0:
        expected = (
            Messages.DUPLICATE_GROUND_TRUTH_SEQUENCE
            if invalid_sequence == 1
            else Messages.GROUND_TRUTH_ISSUE_SEQUENCE_INVALID
        )
        assert re.search(re.escape(expected), completed.stderr)
    assert not repository.exists()


@pytest.mark.parametrize("is_backup_failed", [False, True])
def test_preparation_closes_both_database_connections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, is_backup_failed: bool
) -> None:
    """候補生成とバックアップ失敗の両方で実 SQLite 接続が閉じることを確認する。"""
    database = tmp_path / "memory.db"
    SqliteUnitOfWorkFactory(database)
    repository = tmp_path / "candidate"
    script_directory = Path("datasets/evaluation").resolve()
    monkeypatch.syspath_prepend(str(script_directory))
    namespace = runpy.run_path(str(script_directory / "prepare_independent_v8.py"))
    prepare = namespace["main"]
    assert callable(prepare)
    original_connect = sqlite3.connect
    connections: list[sqlite3.Connection] = []

    class TrackedConnection(sqlite3.Connection):
        """障害注入を許可し、実接続の解放確認に使用する。"""

    def tracked_connect(database: str | Path, *, uri: bool = False) -> sqlite3.Connection:
        """取得した実接続を保持して GC による偶然の解放を防ぐ。"""
        connection = original_connect(database, uri=uri, factory=TrackedConnection)
        connections.append(connection)
        return connection

    def fail_backup(connection: sqlite3.Connection, destination: sqlite3.Connection) -> None:
        """接続取得後のバックアップ失敗を決定的に発生させる。"""
        del connection, destination
        raise sqlite3.OperationalError

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    if is_backup_failed:
        monkeypatch.setattr(TrackedConnection, "backup", fail_backup)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_independent_v8.py",
            "--repository",
            str(repository),
            "--memory-database",
            str(database),
            "--project-id",
            "m2-formal-evaluation-v3",
        ],
    )
    try:
        if is_backup_failed:
            with pytest.raises(sqlite3.OperationalError):
                prepare()
        else:
            assert prepare() == 0
        assert len(connections) == 2
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
    finally:
        for connection in connections:
            connection.close()


def _check_positions_and_secrets(repository: Path, document: dict[str, object]) -> None:
    """実 Git 差分の追加行と AST 所属を検査し、送信前の秘密検査を通す。"""
    scanner = DetectSecretsScanner()
    cases = document["cases"]
    issues = document["issues"]
    assert isinstance(cases, list)
    assert isinstance(issues, list)
    for case in cases:
        revision = case["change_ref"]
        diff = subprocess.check_output(
            ["git", "-C", str(repository), "show", "--format=", revision], text=True
        )
        parsed = UnifiedDiffParser().parse(diff)
        assert len(parsed.files) == 1
        changed_file = parsed.files[0]
        source = subprocess.check_output(
            ["git", "-C", str(repository), "show", f"{revision}:{changed_file.new_path.value}"],
            text=True,
        )
        tree = ast.parse(source)
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        for issue in issues:
            if issue["change_ref"] != revision:
                continue
            assert issue["file"] == changed_file.new_path.value
            assert issue["start_line"] in changed_file.added_line_numbers
            function = functions[issue["symbol"]]
            assert function.end_lineno is not None
            assert function.lineno <= issue["start_line"] <= function.end_lineno
        result = scanner.scan(SourcePayload(SourceKind.DIFF, revision, diff), ScanProfile.STRICT)
        assert result.is_passed


def test_independent_catalog_does_not_reuse_development_sources() -> None:
    """調整に使った四分野のコードと新しい受入コードの重複を拒否する。"""
    catalog = json.loads(Path("datasets/evaluation/independent_v8/catalog.json").read_text())
    candidates = {ast.dump(ast.parse(case["source"])) for case in catalog["cases"]}
    assert len(candidates) == 32
    for patch in Path("datasets/development/security_boundaries/patches").glob("*.patch"):
        changed_file = UnifiedDiffParser().parse(patch.read_text()).files[0]
        source = "\n".join(
            line.text
            for hunk in changed_file.hunks
            for line in hunk.lines
            if line.new_line is not None
        )
        assert ast.dump(ast.parse(source)) not in candidates
