"""未見の問題あり・問題なし変更を持つ test-v3 評価候補を生成する."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_REPOSITORY = _PROJECT_ROOT / ".review/formal-evaluation-v3-repository"
_CREATOR = "benchmark-preparer"
_REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True, slots=True)
class IssueSeed:
    """既知問題の位置と分類を保持する."""

    marker: str
    issue_type: str
    severity: str
    evidence: str
    rule_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChangeSeed:
    """一つの評価変更と、その変更に含まれる既知問題を保持する."""

    path: str
    content: str
    issues: tuple[IssueSeed, ...]


def _issue(
    marker: str,
    issue_type: str,
    severity: str,
    evidence: str,
    rule_id: str | None = None,
) -> IssueSeed:
    """読みやすい引数順で既知問題を作成する."""
    return IssueSeed(marker, issue_type, severity, evidence, rule_id)


_ISSUE_CHANGES = (
    ChangeSeed(
        "src/web/runtime.py",
        """from flask import Flask


def start(app: Flask) -> None:
    app.run(debug=True)


def expose_errors(app: Flask) -> None:
    app.config["PROPAGATE_EXCEPTIONS"] = True
""",
        (
            _issue(
                "debug=True",
                "debug-mode-enabled",
                "critical",
                "本番相当の待受でデバッガを有効にしています。",
            ),
            _issue(
                'PROPAGATE_EXCEPTIONS"] = True',
                "internal-error-disclosure",
                "high",
                "内部例外を利用者へ露出する設定です。",
            ),
        ),
    ),
    ChangeSeed(
        "src/identity/tokens.py",
        """import jwt


def read_claims(token: str) -> dict[str, object]:
    return jwt.decode(token, options={"verify_signature": False})


def accept_algorithm(token: str, algorithm: str, key: str) -> object:
    return jwt.decode(token, key, algorithms=[algorithm])
""",
        (
            _issue(
                '"verify_signature": False',
                "jwt-signature-not-verified",
                "critical",
                "署名を検証せずトークンの要求を信頼しています。",
            ),
            _issue(
                "algorithms=[algorithm]",
                "jwt-algorithm-confusion",
                "critical",
                "利用者側で選べる署名方式を許可一覧なしで受理しています。",
            ),
        ),
    ),
    ChangeSeed(
        "src/search/patterns.py",
        """import re


def matches(value: str) -> bool:
    return re.match(r"^(a+)+$", value) is not None


def user_pattern(pattern: str, value: str) -> bool:
    return re.search(pattern, value) is not None
""",
        (
            _issue(
                'r"^(a+)+$"',
                "regular-expression-denial-of-service",
                "high",
                "入れ子量指定子により入力長に対して破局的バックトラックが発生します。",
            ),
            _issue(
                "re.search(pattern, value)",
                "untrusted-regular-expression",
                "high",
                "未信頼の正規表現を制限なしで評価しています。",
            ),
        ),
    ),
    ChangeSeed(
        "src/automation/formulas.py",
        """def calculate(expression: str) -> object:
    return eval(expression)


def run_fragment(source: str) -> dict[str, object]:
    namespace: dict[str, object] = {}
    exec(source, namespace)
    return namespace
""",
        (
            _issue(
                "eval(expression)",
                "code-injection",
                "critical",
                "未信頼式を eval で実行しています。",
            ),
            _issue(
                "exec(source, namespace)",
                "code-injection",
                "critical",
                "未信頼コードを exec で実行しています。",
            ),
        ),
    ),
    ChangeSeed(
        "src/imports/archive.py",
        """import zipfile
from pathlib import Path


def unpack(archive: zipfile.ZipFile, target: Path) -> None:
    archive.extractall(target)


def unpack_member(archive: zipfile.ZipFile, name: str, target: Path) -> None:
    archive.extract(name, target)
""",
        (
            _issue(
                "archive.extractall(target)",
                "archive-path-traversal",
                "critical",
                "格納パスを検証せず展開しています。",
            ),
            _issue(
                "archive.extract(name, target)",
                "archive-member-not-validated",
                "high",
                "指定メンバーの絶対パスや親移動を検証していません。",
            ),
        ),
    ),
    ChangeSeed(
        "src/integrations/xml_feed.py",
        """from lxml import etree


def parse_feed(document: bytes) -> object:
    parser = etree.XMLParser(resolve_entities=True)
    return etree.fromstring(document, parser)


def load_schema(path: str) -> object:
    return etree.parse(path)
""",
        (
            _issue(
                "resolve_entities=True",
                "xml-external-entity",
                "critical",
                "未信頼 XML の外部実体解決を有効にしています。",
            ),
            _issue(
                "etree.parse(path)",
                "untrusted-xml-resource",
                "high",
                "未信頼パスを XML パーサーへ渡しています。",
            ),
        ),
    ),
    ChangeSeed(
        "src/observability/events.py",
        """import logging


def login_failed(user: str, credential: str) -> None:
    logging.warning("login failed user=%s credential=%s", user, credential)


def outbound(headers: dict[str, str]) -> None:
    logging.info("request headers=%r", headers)
""",
        (
            _issue(
                "credential=%s", "sensitive-data-in-log", "high", "認証情報をログへ記録しています。"
            ),
            _issue(
                "headers=%r",
                "sensitive-data-in-log",
                "high",
                "認証ヘッダーを含み得る辞書全体を記録しています。",
            ),
        ),
    ),
    ChangeSeed(
        "src/storage/permissions.py",
        """import os
import tempfile
from pathlib import Path


def shared_directory(path: Path) -> None:
    os.chmod(path, 0o777)


def reserve_name() -> str:
    return tempfile.mktemp(prefix="upload-")
""",
        (
            _issue(
                "0o777",
                "overly-permissive-file-mode",
                "high",
                "全利用者へ書込権限を付与しています。",
            ),
            _issue(
                "tempfile.mktemp",
                "insecure-temporary-file",
                "medium",
                "競合可能な一時名を確保しています。",
            ),
        ),
    ),
    ChangeSeed(
        "src/connectors/client.py",
        """import requests


def fetch(url: str) -> str:
    return requests.get(url, verify=False, timeout=5).text


def post(url: str, payload: dict[str, object]) -> None:
    requests.post(url, json=payload)
""",
        (
            _issue(
                "verify=False",
                "tls-verification-disabled",
                "high",
                "TLS 証明書検証を無効にしています。",
            ),
            _issue(
                "requests.post(url, json=payload)",
                "network-timeout-missing",
                "medium",
                "外部通信に期限がありません。",
            ),
        ),
    ),
    ChangeSeed(
        "src/web/rendering.py",
        """from markupsafe import Markup


def greeting(display_name: str) -> Markup:
    return Markup("<p>Hello " + display_name + "</p>")


def announcement(body: str) -> Markup:
    return Markup(body)
""",
        (
            _issue(
                "display_name +",
                "cross-site-scripting",
                "high",
                "未信頼表示名を HTML として扱います。",
            ),
            _issue(
                "Markup(body)",
                "cross-site-scripting",
                "critical",
                "未信頼本文を安全済み HTML として扱います。",
            ),
        ),
    ),
    ChangeSeed(
        "src/catalog/filters.py",
        """def add_filter(filters: list[str] = []) -> list[str]:
    filters.append("active")
    return filters
""",
        (
            _issue(
                "filters: list[str] = []",
                "mutable-default-argument",
                "medium",
                "呼出し間で可変リストを共有します。",
            ),
        ),
    ),
    ChangeSeed(
        "src/jobs/dispatch.py",
        """import asyncio
from collections.abc import Coroutine


async def dispatch(task: Coroutine[object, object, object]) -> None:
    asyncio.create_task(task)
""",
        (
            _issue(
                "asyncio.create_task(task)",
                "untracked-background-task",
                "medium",
                "タスク参照と失敗結果を保持しません。",
            ),
        ),
    ),
    ChangeSeed(
        "src/orders/transaction.py",
        """import sqlite3


def replace(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM orders")
    connection.commit()
    connection.execute("INSERT INTO orders DEFAULT VALUES")
""",
        (
            _issue(
                "connection.commit()",
                "partial-transaction-commit",
                "high",
                "一連更新の途中で確定し、後段失敗時に部分状態を残します。",
            ),
        ),
    ),
    ChangeSeed(
        "src/graph/walker.py",
        """from typing import TypedDict


class Node(TypedDict):
    children: list["Node"]


def flatten(node: Node) -> list[object]:
    children = node.get("children", [])
    return [node, *[item for child in children for item in flatten(child)]]
""",
        (_issue("flatten(child)", "cycle-not-detected", "medium", "循環グラフで停止しません。"),),
    ),
    ChangeSeed(
        "src/uploads/reservation.py",
        """from pathlib import Path


def create(path: Path, content: bytes) -> None:
    if not path.exists():
        path.write_bytes(content)
""",
        (
            _issue(
                "if not path.exists()",
                "time-of-check-time-of-use",
                "high",
                "確認と作成の間に対象を差し替えられます。",
            ),
        ),
    ),
    ChangeSeed(
        "src/delivery/notifier.py",
        """def notify(client: object) -> bool:
    try:
        client.send()
    except Exception:
        return True
    return True
""",
        (
            _issue(
                "except Exception",
                "exception-reported-as-success",
                "high",
                "送信失敗を成功として返します。",
            ),
        ),
    ),
    ChangeSeed(
        "src/billing/money.py",
        """from decimal import Decimal


def total(unit_price: float, quantity: int) -> Decimal:
    return Decimal(unit_price) * quantity
""",
        (
            _issue(
                "Decimal(unit_price)",
                "binary-float-for-money",
                "medium",
                "二進浮動小数の誤差を金額へ持ち込みます。",
            ),
        ),
    ),
    ChangeSeed(
        "src/reporting/range.py",
        """from datetime import datetime


def started_today(created_at: datetime) -> bool:
    return created_at >= datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
""",
        (
            _issue(
                "datetime.now()",
                "timezone-naive-comparison",
                "medium",
                "タイムゾーンなし時刻で期間境界を比較します。",
            ),
        ),
    ),
    ChangeSeed(
        "src/audit/export.py",
        """import sqlite3


def export_all(connection: sqlite3.Connection) -> list[object]:
    return connection.execute("SELECT * FROM audit_events").fetchall()
""",
        (
            _issue(
                "fetchall()",
                "unbounded-query-result",
                "medium",
                "件数上限なしで監査履歴をメモリへ読み込みます。",
            ),
        ),
    ),
    ChangeSeed(
        "src/counters/views.py",
        """VIEWS: dict[str, int] = {}


def increment(page: str) -> None:
    VIEWS[page] = VIEWS.get(page, 0) + 1
""",
        (
            _issue(
                "VIEWS[page] =",
                "non-atomic-shared-update",
                "medium",
                "並行更新を失う読取更新書込です。",
            ),
        ),
    ),
)

_CLEAN_CHANGES = (
    (
        "src/safe/query.py",
        """def find(connection: object, email: str) -> object:
    return connection.execute(
        "SELECT id FROM users WHERE email = ?", (email,)
    ).fetchone()
""",
    ),
    (
        "src/safe/process.py",
        """import re
import subprocess

_FULL_SHA = re.compile(r"[0-9a-f]{40}")


def show(revision: str) -> str:
    if _FULL_SHA.fullmatch(revision) is None:
        raise ValueError("revision must be a full commit SHA")
    return subprocess.check_output(["git", "show", "--format=fuller", revision], text=True)
""",
    ),
    (
        "src/safe/path.py",
        """from pathlib import Path


def read(root: Path, name: str) -> str:
    target = (root / name).resolve()
    target.relative_to(root.resolve())
    return target.read_text()
""",
    ),
    (
        "src/safe/url.py",
        """from urllib.parse import urlsplit


def allowed(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and parsed.hostname == "api.example.invalid"
""",
    ),
    (
        "src/safe/signature.py",
        """import hmac


def valid(actual: bytes, expected: bytes) -> bool:
    return hmac.compare_digest(actual, expected)
""",
    ),
    (
        "src/safe/randomness.py",
        "import secrets\n\ndef new_reference() -> str:\n    return secrets.token_urlsafe(32)\n",
    ),
    (
        "src/safe/yaml_document.py",
        "import yaml\n\ndef load(document: str) -> object:\n    return yaml.safe_load(document)\n",
    ),
    (
        "src/safe/async_io.py",
        """import asyncio
from pathlib import Path


async def read(path: Path) -> str:
    return await asyncio.to_thread(path.read_text, encoding="utf-8")
""",
    ),
)


def main() -> int:
    """独立リポジトリと未承認の v3 候補 JSON を生成する."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=_DEFAULT_REPOSITORY)
    arguments = parser.parse_args()
    repository = arguments.repository.resolve()
    if repository.exists():
        sys.stderr.write(f"生成先がすでに存在します: {repository}\n")
        return 1
    _prepare_repository(repository)
    document = _build_document(repository)
    output = repository / "datasets/evaluation/ground-truth-test-v3.candidate.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(
        json.dumps(
            {
                "repository": str(repository),
                "ground_truth_candidate": str(output),
                "case_count": len(document["cases"]),
                "clean_case_count": len(_CLEAN_CHANGES),
                "issue_count": len(document["issues"]),
                "critical_issue_count": sum(
                    issue["severity"] == "critical" for issue in document["issues"]
                ),
                "publication_ready": False,
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return 0


def _prepare_repository(repository: Path) -> None:
    """規則とプロンプトを持つ独立 Git リポジトリを初期化する."""
    repository.mkdir(parents=True)
    _git(repository, "init", "--quiet", "--initial-branch=main")
    _git(repository, "config", "user.name", "M2 Benchmark Generator")
    _git(repository, "config", "user.email", "benchmark@example.invalid")
    shutil.copytree(_PROJECT_ROOT / "config/rules", repository / "config/rules")
    shutil.copytree(_PROJECT_ROOT / "prompts", repository / "prompts")
    (repository / "evaluation.yaml").write_text(
        """schema_version: 1
project_id: m2-formal-evaluation-v3
database:
  path: .review/evaluation.db
llm:
  provider: ai_gateway
  base_url: https://aigw.ldjp-ai.link
  model: gpt-5.6-luna
  timeout_seconds: 60
  max_retries: 2
review:
  mode: rules
  rules_directory: config/rules
memory:
  maintainers:
    - reviewer-1
  is_production: false
security:
  allow_external_source: true
""",
        encoding="utf-8",
    )
    (repository / "README.md").write_text(
        "# M2 test-v3 評価候補\n\n人間による検証と承認前は発行しないでください。\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".")
    _commit(repository, "test-v3 評価基盤を初期化", 0)


def _build_document(repository: Path) -> dict[str, object]:
    """変更を一件ずつ固定 Commit 化して候補文書を返す."""
    cases: list[dict[str, object]] = []
    issues: list[dict[str, object]] = []
    changes = (
        *_ISSUE_CHANGES,
        *(ChangeSeed(path, content, ()) for path, content in _CLEAN_CHANGES),
    )
    issue_sequence = 1
    for case_sequence, change in enumerate(changes, start=1):
        path = repository / change.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(change.content, encoding="utf-8")
        _git(repository, "add", change.path)
        _commit(repository, f"評価変更 {case_sequence:02d} を追加", case_sequence)
        change_ref = _git(repository, "rev-parse", "HEAD")
        cases.append(
            {
                "sequence": case_sequence,
                "change_ref": change_ref,
                "created_by": _CREATOR,
                "verified_by": _CREATOR,
                "approved_by": _REVIEW_REQUIRED,
            }
        )
        lines = change.content.splitlines()
        for issue in change.issues:
            locations = [
                line_number
                for line_number, line in enumerate(lines, start=1)
                if issue.marker in line
            ]
            if len(locations) != 1:
                raise RuntimeError("問題行マーカーが一意ではありません。")
            issues.append(
                {
                    "sequence": issue_sequence,
                    "change_ref": change_ref,
                    "file": change.path,
                    "start_line": locations[0],
                    "issue_type": issue.issue_type,
                    "rule_id": issue.rule_id,
                    "severity": issue.severity,
                    "evidence": issue.evidence,
                    "origin": "seeded-independent-benchmark-candidate",
                    "created_by": _CREATOR,
                    "verified_by": _CREATOR,
                    "approved_by": _REVIEW_REQUIRED,
                }
            )
            issue_sequence += 1
    return {
        "schema_version": 2,
        "split": "test",
        "version": 3,
        "cases": cases,
        "issues": issues,
    }


def _commit(repository: Path, message: str, offset_minutes: int) -> None:
    """再生成時も同じ SHA となる固定時刻で Commit する."""
    timestamp = datetime(2026, 6, 1, tzinfo=UTC) + timedelta(minutes=offset_minutes)
    environment = {
        **os.environ,
        "GIT_AUTHOR_DATE": timestamp.isoformat(),
        "GIT_COMMITTER_DATE": timestamp.isoformat(),
    }
    _git(repository, "commit", "--quiet", "--message", message, environment=environment)


def _git(
    repository: Path,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> str:
    """Git コマンドを実行して標準出力を返す."""
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    return completed.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
