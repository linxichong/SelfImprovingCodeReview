"""本機管理台の HTTP 透過テスト."""

from __future__ import annotations

import hashlib
import json
import secrets
import socket
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from urllib.parse import urlencode

import pytest

from self_improving_code_review.cli.main import main
from self_improving_code_review.infrastructure.persistence.web_read_repository import (
    WebReadRepository,
)
from self_improving_code_review.web.reports import ReportDirectory
from self_improving_code_review.web.server import WebServer

LOGIN_VALUE = secrets.token_urlsafe()
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "review.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript((MIGRATIONS / "0001_m1.sql").read_text())
        connection.executescript((MIGRATIONS / "0008_m4_scm.sql").read_text())
        connection.executescript("""
            INSERT INTO rule_snapshots VALUES ('rules', NULL, 'hash', '2026-10-04');
            INSERT INTO review_runs VALUES (
                'run-001', 'example-project', 'rules', 'success', 'deadbeef', 'test-model',
                '1', 'hash', 'review_general_v1@5', 'hash', 'rules', 'hash', '1',
                'unique', '2026-10-04T11:12:00Z', '2026-10-04T11:12:02Z',
                '{"input_tokens":250,"output_tokens":135,"main_finding_count":1,"cost_usd":"0.0016500549999999999"}'
            );
            INSERT INTO bundles VALUES (
                'run-001','B001',1,'high','success',10,250,135,150,'0.01',NULL,NULL
            );
            INSERT INTO bundle_files VALUES ('run-001','B001','order.py','review',NULL);
            INSERT INTO findings VALUES (
                'run-001','finding-1','B001','order.py',13,13,'security','SQL_INJECTION',
                'SECURITY.SQL_INJECTION','query','critical','Review <script>alert(1)</script>',
                'Interpolation issue','code sample','user value','Use query parameters',
                'fingerprint','query','valid','valid','reflection-skipped','[]',1
            );
            INSERT INTO filtered_files VALUES (
                'run-001','README.md','filter','document'
            );
            INSERT INTO scm_publications (
                publication_id, run_id, provider, repository_external_id, change_external_id,
                head_sha, fingerprint_hash, kind, request_hash, status, created_at, updated_at
            ) VALUES (
                'post-1','run-001','github','owner/repo','123','deadbeef','fingerprint',
                'summary','hash','published','2026-10-04','2026-10-04'
            );
        """)
    return path


@pytest.fixture
def service(database: Path, tmp_path: Path) -> Iterator[WebServer]:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "stored-review.json").write_text(
        json.dumps(
            {
                "run": {"run_id": "export-id", "project_id": "archive"},
                "findings": [],
                "filtered_files": [
                    {"file": "archived-lock.txt", "decision": "filter", "reason": "lock-file"},
                ],
            }
        )
    )
    (reports / "eval.json").write_text(
        json.dumps(
            {
                "set_id": "set-12",
                "reports": [{"arm": "baseline", "recall": 0.25}],
            }
        )
    )
    (reports / "benchmark.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "summary": {
                            "tool": "codex",
                            "instance": "trial",
                            "repeat": 1,
                            "reference_n": 3,
                            "hit_n": 1,
                            "status": "complete",
                            "input_tokens": 150,
                            "output_tokens": 25,
                        }
                    }
                ],
            }
        )
    )
    (reports / "unrelated.json").write_text("{}")
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"run": {"run_id": "outside"}, "findings": []}))
    (reports / "symlink.json").symlink_to(outside)
    with WebServer(
        0, WebReadRepository(database), ReportDirectory(reports), "admin", LOGIN_VALUE
    ) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield server
        finally:
            server.shutdown()
            worker.join(timeout=5)


def request(
    server: WebServer,
    path: str,
    *,
    method: str = "GET",
    form: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], str]:
    headers = dict(headers or {})
    body = None
    if form is not None:
        body = urlencode(form)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    with closing(HTTPConnection("127.0.0.1", server.server_port, timeout=5)) as client:
        client.request(method, path, body=body, headers=headers)
        response = client.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode()


@pytest.fixture
def session(service: WebServer) -> dict[str, str]:
    code, headers, _ = request(
        service,
        "/login",
        method="POST",
        form={"username": "admin", "password": LOGIN_VALUE},
        headers={
            "Origin": f"http://127.0.0.1:{service.server_port}",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert code == 303
    assert headers["Location"] == "/"
    assert "HttpOnly" in headers["Set-Cookie"]
    assert "SameSite=Strict" in headers["Set-Cookie"]
    return {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}


def test_login_protects_views_and_logout_revokes_cookie(service: WebServer) -> None:
    code, headers, _ = request(service, "/run?id=run-001")
    assert code == 303
    assert headers["Location"] == "/login"
    assert request(service, "/login")[0] == 200
    code, _, body = request(
        service,
        "/login",
        method="POST",
        form={"username": "admin", "password": LOGIN_VALUE + "-invalid"},
    )
    assert code == 401
    assert "账户或密码不正确" in body
    code, headers, _ = request(
        service, "/login", method="POST", form={"username": "admin", "password": LOGIN_VALUE}
    )
    assert code == 303
    auth = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
    assert request(service, "/", headers=auth)[0] == 200
    assert request(service, "/logout", method="POST", headers=auth)[0] == 303
    assert request(service, "/", headers=auth)[0] == 303


@pytest.mark.parametrize(
    "bad_headers",
    [
        {"Host": "external.example"},
        {"Origin": "https://external.example"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_cross_origin_login_is_rejected(service: WebServer, bad_headers: dict[str, str]) -> None:
    assert request(service, "/login", method="POST", headers=bad_headers)[0] == 403


def test_overview_filters_findings_and_sqlite_is_read_only(
    service: WebServer, session: dict[str, str], database: Path
) -> None:
    before = hashlib.sha256(database.read_bytes()).digest()
    code, headers, dashboard = request(service, "/", headers=session)
    assert code == 200
    assert "385" in dashboard
    assert "example-project" in dashboard
    assert "frame-ancestors" in headers["Content-Security-Policy"]
    assert headers["Referrer-Policy"] == "same-origin"
    assert request(service, "/style.css")[0] == 200
    code, _, matches = request(service, "/runs?q=TEST-MODEL&status=success", headers=session)
    assert code == 200
    assert "example-project" in matches
    assert "example-project" not in request(service, "/runs?status=failed", headers=session)[2]
    assert request(service, "/runs?page=999", headers=session)[0] == 303
    code, _, detail = request(service, "/run?id=run-001", headers=session)
    assert code == 200
    assert "order.py:13" in detail
    assert "<strong>0.001650</strong>" in detail
    assert "0.0016500549999999999" not in detail
    assert "Use query parameters" in detail
    assert "&lt;script&gt;" in detail
    assert "<script>" not in detail
    assert "owner/repo" in detail
    assert "README.md" in detail
    assert request(service, "/run?id=missing", headers=session)[0] == 404
    assert hashlib.sha256(database.read_bytes()).digest() == before
    with service.repository.connection() as connection, pytest.raises(sqlite3.OperationalError):
        connection.execute("DELETE FROM review_runs")


def test_reports_render_known_exports_and_block_path_escape(
    service: WebServer, session: dict[str, str]
) -> None:
    code, _, body = request(service, "/reports", headers=session)
    assert code == 200
    assert "stored-review.json" in body
    assert "benchmark.json" in body
    assert "unrelated.json" not in body
    assert "symlink.json" not in body
    stored = request(service, "/report?name=stored-review.json", headers=session)[2]
    assert "archive" in stored
    assert "archived-lock.txt" in stored
    assert "baseline" in request(service, "/report?name=eval.json", headers=session)[2]
    benchmark = request(service, "/report?name=benchmark.json", headers=session)[2]
    assert "codex" in benchmark
    assert "1/3 (33.3%)" in benchmark
    assert "175" in benchmark
    assert request(service, "/report?name=%2e%2e%2foutside.json", headers=session)[0] == 404
    assert request(service, "/report?name=symlink.json", headers=session)[0] == 404


def test_cli_refuses_missing_database(tmp_path: Path) -> None:
    path = tmp_path / "nonexistent.db"
    assert main(["dashboard", "--db", str(path)]) == 1
    assert not path.exists()


def test_cli_initializes_local_dashboard(database: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def verify_server(server: WebServer) -> None:
        assert server.server_address == ("127.0.0.1", port)
        code, token = server.login("operator", LOGIN_VALUE)
        assert code == 303
        assert token is not None
        assert server.authorized(token)

    with closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("REVIEW_WEB_ADMIN_USER", "operator")
    monkeypatch.setenv("REVIEW_WEB_ADMIN_PASSWORD", LOGIN_VALUE)
    monkeypatch.setattr(WebServer, "serve_forever", verify_server)
    assert main(["dashboard", "--db", str(database), "--port", str(port)]) == 0


def test_cli_rejects_an_invalid_database_and_port(tmp_path: Path) -> None:
    path = tmp_path / "invalid.db"
    path.write_text("not an sqlite database")
    assert main(["dashboard", "--db", str(path)]) == 1
    assert main(["dashboard", "--db", str(path), "--port", "0"]) == 1


def test_malformed_login_and_repeated_failures_are_rejected(service: WebServer) -> None:
    assert request(service, "/login", method="POST")[0] == 400
    for _ in range(6):
        code, _, _ = request(
            service,
            "/login",
            method="POST",
            form={"username": "admin", "password": LOGIN_VALUE + "-invalid"},
        )
        assert code == 401
    code, _, _ = request(
        service, "/login", method="POST", form={"username": "admin", "password": LOGIN_VALUE}
    )
    assert code == 429


def test_file_completion_distinguishes_units_and_no_findings(
    service: WebServer, database: Path, session: dict[str, str]
) -> None:
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript("""
            UPDATE review_runs SET status = 'partial-success' WHERE run_id = 'run-001';
            INSERT INTO bundles VALUES
                ('run-001','B002',2,'high','failed',10,0,0,0,'0','TimeoutError',NULL),
                ('run-001','B003',3,'low','skipped',10,0,0,0,'0','SECRET_DETECTED',NULL),
                ('run-001','B004',4,'low','skipped',10,0,0,0,'0','RUN_BLOCKED',NULL),
                ('run-001','B005',5,'medium','partial-success',10,0,0,0,'0','PROVIDER_ERROR',NULL),
                ('run-001','B006',6,'low','pending',10,0,0,0,'0',NULL,NULL);
            INSERT INTO bundle_files VALUES
                ('run-001','B001','clean.py','review',NULL),
                ('run-001','B001','mixed.py','review',NULL),
                ('run-001','B002','mixed.py','review',NULL),
                ('run-001','B002','failed.py','review',NULL),
                ('run-001','B003','blocked.py','review',NULL),
                ('run-001','B004','skipped.py','review',NULL),
                ('run-001','B005','light.md','lightweight','document'),
                ('run-001','B006','pending.py','review',NULL);
        """)
    code, _, content = request(service, "/run?id=run-001", headers=session)
    assert code == 200
    completion = content.split('id="file-completion"', 1)[1].split("</section>", 1)[0]
    rows = completion.split("<tr>")
    expected = {
        "clean.py": ("已完成", "1/1", "<td>0</td>"),
        "order.py": ("已完成", "1/1", "<td>1</td>"),
        "mixed.py": ("部分完成", "1/2", "TimeoutError"),
        "failed.py": ("失败", "0/1", "TimeoutError"),
        "blocked.py": ("已拦截", "SECRET_DETECTED"),
        "skipped.py": ("已跳过", "RUN_BLOCKED"),
        "light.md": ("轻量审查", "部分完成", "PROVIDER_ERROR"),
        "pending.py": ("待处理", "0/1"),
        "README.md": ("已过滤", "文档"),
    }
    for name, values in expected.items():
        row = next(item for item in rows if f"<code>{name}</code>" in item)
        assert all(value in row for value in values), (name, row)


def test_blocked_run_explains_unrecorded_files(
    service: WebServer, database: Path, session: dict[str, str]
) -> None:
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("UPDATE review_runs SET status = 'blocked' WHERE run_id = 'run-001'")
    code, _, content = request(service, "/run?id=run-001", headers=session)
    assert code == 200
    assert "可能有尚未记录的文件" in content


def test_file_completion_paginates_without_changing_totals(
    service: WebServer, database: Path, session: dict[str, str]
) -> None:
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executemany(
            "INSERT INTO filtered_files VALUES (?, ?, ?, ?)",
            [("run-001", f"asset-{i:02d}.bin", "filter", "binary") for i in range(42)],
        )
    first = request(service, "/run?id=run-001", headers=session)[2]
    last = request(service, "/run?id=run-001&file_page=999", headers=session)[2]
    assert "已记录 44 个文件" in first
    assert "已记录 44 个文件" in last
    assert "<code>asset-00.bin</code>" in first
    assert "<code>asset-00.bin</code>" not in last
    assert "<code>asset-41.bin</code>" not in first
    assert "<code>asset-41.bin</code>" in last
    assert "第 1 / 2 页" in first
    assert "第 2 / 2 页" in last
