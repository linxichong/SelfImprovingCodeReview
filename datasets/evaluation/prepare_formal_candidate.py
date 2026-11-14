"""正式規模の M2 評価候補リポジトリを決定的に生成する."""

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
_DEFAULT_REPOSITORY = _PROJECT_ROOT / ".review/formal-evaluation-repository"
_GROUND_TRUTH_PATH = Path("datasets/evaluation/ground-truth-test-v2.candidate.json")
_CREATOR = "benchmark-preparer"
_REVIEW_REQUIRED = "REVIEW_REQUIRED"
_INVALID_MARKER = "問題行マーカーが一意ではありません。"


@dataclass(frozen=True, slots=True)
class IssueSeed:
    """一つの既知問題と問題行を特定するマーカー."""

    marker: str
    issue_type: str
    rule_id: str | None
    severity: str
    evidence: str


@dataclass(frozen=True, slots=True)
class ChangeSeed:
    """一つの Git 変更へ追加する Python ファイルと既知問題."""

    case_id: str
    relative_path: str
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
    return IssueSeed(marker, issue_type, rule_id, severity, evidence)


_CHANGES = (
    ChangeSeed(
        "PY-EVAL-001",
        "src/case_01_command.py",
        """import os
import subprocess


def show_revision(revision: str) -> str:
    return subprocess.check_output("git show " + revision, shell=True, text=True)


def unpack_archive(archive: str) -> int:
    return os.system("tar -xf " + archive)
""",
        (
            _issue(
                'subprocess.check_output("git show " + revision, shell=True, text=True)',
                "command-injection",
                "critical",
                "未信頼の revision を shell=True のコマンド文字列へ連結しています。",
                "SECURITY.COMMAND_INJECTION",
            ),
            _issue(
                'os.system("tar -xf " + archive)',
                "command-injection",
                "critical",
                "未信頼の archive を os.system のコマンドへ連結しています。",
                "SECURITY.COMMAND_INJECTION",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-002",
        "src/case_02_sql.py",
        """import sqlite3


def find_user(connection: sqlite3.Connection, email: str) -> object:
    return connection.execute(f"SELECT * FROM users WHERE email = '{email}'").fetchone()


def list_users(connection: sqlite3.Connection, order_by: str) -> list[object]:
    return connection.execute("SELECT * FROM users ORDER BY " + order_by).fetchall()
""",
        (
            _issue(
                'connection.execute(f"SELECT * FROM users WHERE email =',
                "sql-injection",
                "critical",
                "未信頼の email を SQL の f-string へ直接埋め込んでいます。",
                "SECURITY.SQL_INJECTION",
            ),
            _issue(
                'connection.execute("SELECT * FROM users ORDER BY " + order_by)',
                "sql-injection",
                "high",
                "未信頼の order_by を許可一覧なしで SQL 識別子へ連結しています。",
                "SECURITY.SQL_INJECTION",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-003",
        "src/case_03_paths.py",
        """import tarfile
from pathlib import Path


def read_upload(root: Path, filename: str) -> str:
    return (root / filename).read_text(encoding="utf-8")


def extract_upload(archive: tarfile.TarFile, destination: Path) -> None:
    archive.extractall(destination)
""",
        (
            _issue(
                '(root / filename).read_text(encoding="utf-8")',
                "path-traversal",
                "critical",
                "未信頼の filename を解決後の所属確認なしで読み込んでいます。",
                "SECURITY.PATH_TRAVERSAL",
            ),
            _issue(
                "archive.extractall(destination)",
                "archive-path-traversal",
                "critical",
                "アーカイブ内のパスを検証せずに一括展開しています。",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-004",
        "src/case_04_network.py",
        """from urllib.request import urlopen

import requests


def fetch_preview(url: str) -> str:
    return requests.get(url, timeout=5).text


def notify_callback(callback_url: str) -> bytes:
    return urlopen(callback_url, timeout=5).read()
""",
        (
            _issue(
                "requests.get(url, timeout=5)",
                "server-side-request-forgery",
                "critical",
                "利用者指定 URL を接続先制限なしでサーバーから取得しています。",
            ),
            _issue(
                "urlopen(callback_url, timeout=5)",
                "server-side-request-forgery",
                "critical",
                "未信頼の callback_url へ内部ネットワークから接続できます。",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-005",
        "src/case_05_deserialization.py",
        """import pickle

import yaml


def restore_session(payload: bytes) -> object:
    return pickle.loads(payload)


def load_settings(document: str) -> object:
    return yaml.load(document, Loader=yaml.Loader)
""",
        (
            _issue(
                "pickle.loads(payload)",
                "unsafe-deserialization",
                "critical",
                "未信頼のセッションデータを pickle で復元しています。",
                "SECURITY.UNSAFE_DESERIALIZATION",
            ),
            _issue(
                "yaml.load(document, Loader=yaml.Loader)",
                "unsafe-yaml-deserialization",
                "high",
                "安全でない YAML Loader で未信頼文書を復元しています。",
                "SECURITY.UNSAFE_DESERIALIZATION",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-006",
        "src/case_06_tokens.py",
        """import random
import string


def password_matches(actual: str, expected: str) -> bool:
    return actual == expected


def new_reset_token() -> str:
    return "".join(random.choice(string.ascii_letters) for _ in range(32))
""",
        (
            _issue(
                "return actual == expected",
                "timing-sensitive-comparison",
                "medium",
                "秘密値を一定時間比較ではなく通常の文字列比較で照合しています。",
            ),
            _issue(
                "random.choice(string.ascii_letters)",
                "insecure-randomness",
                "high",
                "パスワード再設定 Token に暗号学的でない乱数を使用しています。",
                "SECURITY.WEAK_CRYPTOGRAPHY",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-007",
        "src/case_07_crypto.py",
        """import hashlib

from Crypto.Cipher import AES


def password_digest(password: str) -> str:
    return hashlib.md5(password.encode()).hexdigest()


def encrypt_record(key: bytes, payload: bytes) -> bytes:
    return AES.new(key, AES.MODE_ECB).encrypt(payload)
""",
        (
            _issue(
                "hashlib.md5(password.encode())",
                "weak-password-hash",
                "high",
                "パスワード保存に高速かつ Salt なしの MD5 を使用し、"
                "オフライン解析を容易にしています。",
                "SECURITY.WEAK_CRYPTOGRAPHY",
            ),
            _issue(
                "AES.new(key, AES.MODE_ECB)",
                "insecure-cipher-mode",
                "high",
                "同一平文パターンを隠せない ECB モードで機密レコードを暗号化しています。",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-008",
        "src/case_08_async.py",
        """import time

import requests


async def load_profile(url: str) -> str:
    return requests.get(url, timeout=10).text


async def wait_for_retry(seconds: int) -> None:
    time.sleep(seconds)
""",
        (
            _issue(
                "requests.get(url, timeout=10)",
                "blocking-io-in-async",
                "medium",
                "async 関数内で同期 HTTP 呼び出しを直接実行しています。",
                "PYTHON.ASYNC_BLOCKING_IO",
            ),
            _issue(
                "time.sleep(seconds)",
                "blocking-sleep-in-async",
                "medium",
                "async 関数内で time.sleep を呼びイベントループを停止させます。",
                "PYTHON.ASYNC_BLOCKING_IO",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-009",
        "src/case_09_errors.py",
        """import json
from pathlib import Path


def load_document(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def save_document(path: Path, payload: str) -> bool:
    try:
        path.write_text(payload, encoding="utf-8")
    except OSError:
        return True
    return True
""",
        (
            _issue(
                "except Exception:",
                "swallowed-exception",
                "medium",
                "すべての例外を記録も変換もせずに握りつぶしています。",
                "PYTHON.SWALLOWED_EXCEPTION",
            ),
            _issue(
                "except OSError:",
                "incorrect-success-on-failure",
                "high",
                "保存失敗時にも成功を返すため呼び出し側が障害を検出できません。",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-010",
        "src/case_10_resources.py",
        """import sqlite3
from pathlib import Path


def first_line(path: Path) -> str:
    handle = open(path, encoding="utf-8")
    return handle.readline()


def count_rows(database: Path) -> int:
    connection = sqlite3.connect(database)
    return int(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0])
""",
        (
            _issue(
                'handle = open(path, encoding="utf-8")',
                "file-resource-leak",
                "medium",
                "例外経路を含めてファイルを閉じる処理がありません。",
                "PYTHON.RESOURCE_LEAK",
            ),
            _issue(
                "connection = sqlite3.connect(database)",
                "database-resource-leak",
                "medium",
                "SQLite 接続を閉じずに関数から戻っています。",
                "PYTHON.RESOURCE_LEAK",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-011",
        "src/case_11_regex.py",
        """import re


def matches_user_pattern(pattern: str, value: str) -> bool:
    return re.compile(pattern).search(value) is not None
""",
        (
            _issue(
                "re.compile(pattern)",
                "regular-expression-denial-of-service",
                "high",
                "未信頼の正規表現を制限なくコンパイルして長い入力へ適用しています。",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-012",
        "src/case_12_zip.py",
        """import zipfile
from pathlib import Path


def install_package(archive: zipfile.ZipFile, root: Path) -> None:
    archive.extractall(root)
""",
        (
            _issue(
                "archive.extractall(root)",
                "unbounded-archive-extraction",
                "high",
                "ZIP 内のファイル数や展開後の合計サイズを制限せず、"
                "資源枯渇を招く可能性があります。",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-013",
        "src/case_13_redirect.py",
        """from flask import redirect


def complete_login(next_url: str) -> object:
    return redirect(next_url)
""",
        (
            _issue(
                "redirect(next_url)",
                "open-redirect",
                "high",
                "利用者指定 URL を許可一覧なしでリダイレクト先にしています。",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-014",
        "src/case_14_access.py",
        """from typing import Protocol


class Documents(Protocol):
    def get(self, document_id: str) -> object: ...


def download_document(store: Documents, document_id: str, actor_id: str) -> object:
    return store.get(document_id)
""",
        (
            _issue(
                "return store.get(document_id)",
                "missing-object-authorization",
                "high",
                "actor_id と文書所有者の認可を確認せず任意 ID の文書を返しています。",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-015",
        "src/case_15_query.py",
        """import sqlite3


def export_events(connection: sqlite3.Connection) -> list[object]:
    return connection.execute("SELECT * FROM events").fetchall()
""",
        (
            _issue(
                'connection.execute("SELECT * FROM events").fetchall()',
                "unbounded-query-result",
                "medium",
                "件数制限なしで全イベントをメモリへ読み込んでいます。",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-016",
        "src/case_16_xml.py",
        """from lxml import etree


def parse_invoice(document: bytes) -> object:
    return etree.fromstring(document)
""",
        (
            _issue(
                "etree.fromstring(document)",
                "xml-external-entity",
                "critical",
                "外部実体を無効化した Parser を指定せず未信頼 XML を解析しています。",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-017",
        "src/case_17_temp.py",
        """import tempfile
from pathlib import Path


def create_export() -> Path:
    path = Path(tempfile.mktemp())
    path.write_text("export", encoding="utf-8")
    return path
""",
        (
            _issue(
                "Path(tempfile.mktemp())",
                "insecure-temporary-file",
                "medium",
                "競合可能な一時パスを作成後に別操作でファイルを開いています。",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-018",
        "src/case_18_logging.py",
        """import logging


def audit_login(username: str) -> None:
    logging.info("login user=%s" % username)
""",
        (
            _issue(
                'logging.info("login user=%s" % username)',
                "log-injection",
                "medium",
                "改行を含み得る username を正規化せず監査ログへ出力しています。",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-019",
        "src/case_19_cache.py",
        """CACHE: dict[str, bytes] = {}


def remember(key: str, payload: bytes) -> None:
    CACHE[key] = payload
""",
        (
            _issue(
                "CACHE[key] = payload",
                "unbounded-memory-cache",
                "medium",
                "利用者ごとに増えるデータを上限や失効なしで大域辞書へ保持しています。",
                "PYTHON.SHARED_MUTABLE_STATE",
            ),
        ),
    ),
    ChangeSeed(
        "PY-EVAL-020",
        "src/case_20_signature.py",
        """import hmac


def valid_webhook(body: bytes, signature: str, key: bytes) -> bool:
    expected = hmac.digest(key, body, "sha256").hex()
    return signature == expected
""",
        (
            _issue(
                "return signature == expected",
                "timing-sensitive-signature-comparison",
                "medium",
                "Webhook 署名を hmac.compare_digest ではなく通常比較しています。",
            ),
        ),
    ),
)


def main() -> int:
    """引数を解析して独立評価リポジトリと候補 JSON を生成する."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        type=Path,
        default=_DEFAULT_REPOSITORY,
        help="生成先の空ディレクトリです。",
    )
    arguments = parser.parse_args()
    repository = arguments.repository.resolve()
    if repository.exists():
        sys.stderr.write(f"生成先がすでに存在します: {repository}\n")
        return 1
    _prepare_repository(repository)
    document = _build_changes(repository)
    output = repository / _GROUND_TRUTH_PATH
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    sys.stdout.write(
        json.dumps(
            {
                "repository": str(repository),
                "ground_truth_candidate": str(output),
                "change_ref_count": len(_CHANGES),
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
    """評価に必要な規則、Prompt、設定を持つ Git リポジトリを初期化する."""
    repository.mkdir(parents=True)
    _git(repository, "init", "--quiet", "--initial-branch=main")
    _git(repository, "config", "user.name", "M2 Benchmark Generator")
    _git(repository, "config", "user.email", "benchmark@example.invalid")
    shutil.copytree(_PROJECT_ROOT / "config/rules", repository / "config/rules")
    shutil.copytree(_PROJECT_ROOT / "prompts", repository / "prompts")
    config = """schema_version: 1
project_id: m2-formal-evaluation
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
"""
    (repository / "evaluation.yaml").write_text(config, encoding="utf-8")
    (repository / "README.md").write_text(
        "# M2 正式規模評価候補\n\n"
        "自動生成した既知問題候補です。人間による検証と承認前は発行しないでください。\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".")
    _commit(repository, "評価基盤を初期化", 0)


def _build_changes(repository: Path) -> dict[str, object]:
    """各既知問題ファイルを一 Commit ずつ追加して正解候補を返す."""
    issues: list[dict[str, object]] = []
    sequence = 1
    for index, change in enumerate(_CHANGES, start=1):
        path = repository / change.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(change.content, encoding="utf-8")
        _git(repository, "add", change.relative_path)
        _commit(repository, f"{change.case_id} の既知問題を追加", index)
        change_ref = _git(repository, "rev-parse", "HEAD")
        lines = change.content.splitlines()
        for issue in change.issues:
            matching_lines = [
                line_number
                for line_number, line in enumerate(lines, start=1)
                if issue.marker in line
            ]
            if len(matching_lines) != 1:
                raise RuntimeError(_INVALID_MARKER)
            issues.append(
                {
                    "sequence": sequence,
                    "change_ref": change_ref,
                    "file": change.relative_path,
                    "start_line": matching_lines[0],
                    "issue_type": issue.issue_type,
                    "rule_id": issue.rule_id,
                    "severity": issue.severity,
                    "evidence": issue.evidence,
                    "origin": "seeded-benchmark-candidate",
                    "created_by": _CREATOR,
                    "verified_by": _CREATOR,
                    "approved_by": _REVIEW_REQUIRED,
                }
            )
            sequence += 1
    return {
        "schema_version": 1,
        "split": "test",
        "version": 2,
        "issues": issues,
    }


def _commit(repository: Path, message: str, offset_minutes: int) -> None:
    """再実行しても同じ SHA になる固定時刻で Commit する."""
    timestamp = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=offset_minutes)
    formatted = timestamp.isoformat()
    environment = {
        **os.environ,
        "GIT_AUTHOR_DATE": formatted,
        "GIT_COMMITTER_DATE": formatted,
    }
    _git(repository, "commit", "--quiet", "--message", message, environment=environment)


def _git(
    repository: Path,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> str:
    """Git コマンドを実行し、標準出力を空白除去して返す."""
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
