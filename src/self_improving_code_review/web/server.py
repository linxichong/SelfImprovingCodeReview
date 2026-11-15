"""ローカル Web サーバーと単一管理者セッションの境界."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from threading import Lock
from time import monotonic
from urllib.parse import parse_qs, urlencode, urlsplit

from self_improving_code_review.infrastructure.persistence.web_read_repository import (
    PAGE_SIZE,
    WebReadRepository,
)
from self_improving_code_review.web import views
from self_improving_code_review.web.reports import ReportDirectory

SESSION_SECONDS = 8 * 60 * 60
COOKIE = "sicr_session"
MAX_FORM_BYTES = 16 * 1024
MAX_SESSIONS = 16
LOGIN_WINDOW = 30
LOGIN_LIMIT = 6
CSP = (
    "default-src 'none'; style-src 'self'; form-action 'self'; "
    "base-uri 'none'; frame-ancestors 'none'"
)


class WebServer(ThreadingHTTPServer):
    """ループバックでのみ待ち受け、認証と読み取り元を保持する."""

    def __init__(
        self,
        port: int,
        repository: WebReadRepository,
        reports: ReportDirectory,
        username: str,
        password: str,
    ) -> None:
        """起動時の管理者とデータソースを固定する."""
        self.repository = repository
        self.reports = reports
        self.username = username.encode("utf-8")
        self._salt = secrets.token_bytes(16)
        self._password_hash = self._hash(password)
        self._lock = Lock()
        self._sessions: dict[str, float] = {}
        self._failed_attempts: list[float] = []
        self.css = files("self_improving_code_review.web").joinpath("style.css").read_bytes()
        super().__init__(("127.0.0.1", port), WebHandler)
        self.hosts = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}

    def login(self, username: str, password: str) -> tuple[int, str | None]:
        """資格情報を検査し、短時間の失敗回数を制限する."""
        with self._lock:
            now = monotonic()
            self._failed_attempts = [t for t in self._failed_attempts if now - t < LOGIN_WINDOW]
            if len(self._failed_attempts) >= LOGIN_LIMIT:
                return 429, None
            name_ok = secrets.compare_digest(username.encode("utf-8"), self.username)
            password_ok = secrets.compare_digest(self._hash(password), self._password_hash)
            if not (name_ok and password_ok):
                self._failed_attempts.append(now)
                return 401, None
            self._failed_attempts.clear()
            self._sessions = {
                token: until for token, until in self._sessions.items() if until > now
            }
            if len(self._sessions) >= MAX_SESSIONS:
                del self._sessions[next(iter(self._sessions))]
            token = secrets.token_urlsafe(32)
            self._sessions[token] = now + SESSION_SECONDS
            return 303, token

    def authorized(self, token: str) -> bool:
        """セッションが期限内のときだけ許可する."""
        with self._lock:
            return self._sessions.get(token, 0) > monotonic()

    def logout(self, token: str) -> None:
        """該当セッションを無効にする."""
        with self._lock:
            self._sessions.pop(token, None)

    def _hash(self, password: str) -> bytes:
        """同一インスタンスのソルトで管理者パスワードを導出する."""
        return hashlib.scrypt(password.encode("utf-8"), salt=self._salt, n=16384, r=8, p=1)


class WebHandler(BaseHTTPRequestHandler):
    """ブラウザの要求を検証し、認証済み画面を返す."""

    server: WebServer
    server_version = "ReviewConsole"
    sys_version = ""

    def setup(self) -> None:
        """要求の読み取り時間に上限を設ける."""
        super().setup()
        self.connection.settimeout(5)

    def do_GET(self) -> None:
        """ログインまたは読み取り画面を表示する."""
        if not self._same_site():
            self._html(403, views.problem("拒绝访问", "请从本机环回地址打开管理台。"))
            return
        try:
            url = urlsplit(self.path)
            params = parse_qs(url.query, keep_blank_values=True, max_num_fields=10)
        except ValueError:
            self._html(400, views.problem("请求无效", "无法解析请求地址。"))
            return
        if url.path == "/style.css":
            self._respond(200, self.server.css, "text/css; charset=utf-8")
            return
        if not self.server.authorized(self._cookie()):
            if url.path == "/login":
                self._html(200, views.login())
            else:
                self._redirect("/login")
            return
        try:
            self._private_get(url.path, params)
        except sqlite3.Error, OSError, ValueError:
            self._html(503, views.problem("暂时无法读取数据", "请检查数据库或报告文件。", "/"))

    def do_POST(self) -> None:
        """ログインとログアウトだけを受け付ける."""
        if not self._same_site():
            self._html(403, views.problem("拒绝访问", "请求来源与管理台地址不一致。"))
            return
        if self.path == "/logout":
            self.server.logout(self._cookie())
            self._redirect("/login", cookie=_session_cookie("", 0))
            return
        if self.path != "/login":
            self._html(404, views.problem("页面不存在", "请返回管理台。"))
            return
        fields = self._form()
        if fields is None:
            self._html(400, views.login("登录请求无效。"))
            return
        code, token = self.server.login(_first(fields, "username"), _first(fields, "password"))
        if token is not None:
            self.server.logout(self._cookie())
            self._redirect("/", cookie=_session_cookie(token, SESSION_SECONDS))
        elif code == 429:
            self._html(code, views.login("尝试次数过多，请稍后再试。"))
        else:
            self._html(code, views.login("账户或密码不正确。"))

    def log_message(self, message_format: str, *args: object) -> None:
        """要求 URL や資格情報を標準 HTTP ログへ出さない."""

    def _same_site(self) -> bool:
        """指定されたループバックのホストとブラウザの要求元を検証する."""
        host = self.headers.get("Host", "").lower()
        origin = self.headers.get("Origin")
        site = self.headers.get("Sec-Fetch-Site", "")
        return (
            host in self.server.hosts
            and (origin is None or origin == f"http://{host}")
            and site not in {"cross-site", "same-site"}
        )

    def _private_get(self, path: str, params: dict[str, list[str]]) -> None:
        """認証後に許可される読み取り経路を振り分ける."""
        if path == "/login":
            self._redirect("/")
        elif path == "/":
            self._html(200, views.overview(self.server.repository.overview()))
        elif path == "/runs":
            self._runs(params)
        elif path == "/run":
            data = self.server.repository.detail(
                _first(params, "id")[:128], file_page=_page_number(_first(params, "file_page"))
            )
            if data is not None:
                self._html(200, views.run_detail(data))
            else:
                self._html(404, views.problem("运行不存在", "请从审查运行列表重新选择。", "/runs"))
        elif path == "/reports":
            self._html(200, views.reports(self.server.reports.entries()))
        elif path == "/report":
            name = _first(params, "name")
            report = self.server.reports.get(name)
            if report is not None:
                self._html(200, views.report_detail(name, report))
            else:
                self._html(
                    404, views.problem("报告不存在", "请从已生成报告列表重新选择。", "/reports")
                )
        else:
            self._html(404, views.problem("页面不存在", "请从侧栏选择一个页面。", "/"))

    def _runs(self, params: dict[str, list[str]]) -> None:
        """検索条件を制限し、ページ外の指定を正規化する."""
        query = _first(params, "q")[:120]
        status = _first(params, "status")[:32]
        page = _page_number(_first(params, "page"))
        items, total = self.server.repository.runs(query, status, page)
        last = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        if page > last:
            self._redirect("/runs?" + urlencode({"q": query, "status": status, "page": last}))
            return
        self._html(200, views.runs(items, total, query, status, page))

    def _form(self) -> dict[str, list[str]] | None:
        """長さと形式を制限したログインフォームを解析する."""
        if self.headers.get_content_type() != "application/x-www-form-urlencoded":
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_FORM_BYTES:
                return None
            return parse_qs(self.rfile.read(length).decode("utf-8"), max_num_fields=4)
        except ValueError, TimeoutError:
            return None

    def _cookie(self) -> str:
        """Cookie ヘッダーからセッション識別子を得る."""
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return ""
        return cookie[COOKIE].value if COOKIE in cookie else ""

    def _html(self, code: int, content: str) -> None:
        """文字列を UTF-8 の HTML として返す."""
        self._respond(code, content.encode("utf-8"), "text/html; charset=utf-8")

    def _redirect(self, path: str, *, cookie: str | None = None) -> None:
        """内部経路へ遷移し、必要に応じて Cookie を更新する."""
        self._respond(303, b"", "text/plain; charset=utf-8", location=path, cookie=cookie)

    def _respond(
        self,
        code: int,
        body: bytes,
        content_type: str,
        *,
        location: str | None = None,
        cookie: str | None = None,
    ) -> None:
        """共通の安全ヘッダーと応答本体を返す."""
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        if location is not None:
            self.send_header("Location", location)
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)


def _session_cookie(token: str, seconds: int) -> str:
    """セッション Cookie の属性を固定する."""
    return f"{COOKIE}={token}; Path=/; Max-Age={seconds}; HttpOnly; SameSite=Strict"


def _first(query: dict[str, list[str]], name: str) -> str:
    """単一値として受け付ける先頭の入力を返す."""
    return next(iter(query.get(name, [])), "")


def _page_number(value: str) -> int:
    """桁数を制限してページ番号を解釈する."""
    return max(1, int(value)) if value.isascii() and value.isdecimal() and len(value) <= 6 else 1
