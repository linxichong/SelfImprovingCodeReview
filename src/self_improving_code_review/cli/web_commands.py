"""ローカル Web 管理台を CLI から起動する."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from getpass import getpass
from pathlib import Path

from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.infrastructure.persistence.web_read_repository import (
    WebReadRepository,
)
from self_improving_code_review.messages import Messages
from self_improving_code_review.web.reports import ReportDirectory
from self_improving_code_review.web.server import WebServer


def add_web_options(parser: argparse.ArgumentParser) -> None:
    """Web 管理台のローカル起動オプションを登録する."""
    options = parser.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    options.add_argument("-h", "--help", action="help", help=Messages.CLI_HELP_HELP)
    options.add_argument(
        "--db", type=Path, default=Path(".review/review.db"), help=Messages.WEB_DB_HELP
    )
    options.add_argument(
        "--reports", type=Path, default=Path(".review/reports"), help=Messages.WEB_REPORTS_HELP
    )
    options.add_argument("--port", type=int, default=8765, help=Messages.WEB_PORT_HELP)


def execute_web_command(arguments: argparse.Namespace) -> int:
    """既存 SQLite を検証し、明示された管理者でループバックに待ち受ける."""
    if not 1 <= arguments.port <= 65535:
        raise InvalidInputError(Messages.WEB_INVALID_PORT)
    db_path: Path = arguments.db
    if not db_path.is_file():
        raise InvalidInputError(Messages.WEB_DATABASE_MISSING)
    repository = WebReadRepository(db_path)
    try:
        repository.validate()
    except sqlite3.Error as exc:
        raise InvalidInputError(Messages.WEB_DATABASE_INVALID) from exc
    username = os.getenv("REVIEW_WEB_ADMIN_USER", "admin")
    password = os.getenv("REVIEW_WEB_ADMIN_PASSWORD")
    if password is None:
        if not sys.stdin.isatty():
            raise InvalidInputError(Messages.WEB_LOGIN_REQUIRED)
        password = getpass(Messages.WEB_LOGIN_PROMPT)
    if not username.strip() or len(username) > 128 or not password or len(password) > 1024:
        raise InvalidInputError(Messages.WEB_INVALID_CREDENTIALS)
    reports = ReportDirectory(arguments.reports)
    try:
        with WebServer(arguments.port, repository, reports, username, password) as server:
            sys.stderr.write(f"{Messages.WEB_LISTENING} http://127.0.0.1:{server.server_port}\n")
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except OSError as exc:
        raise InvalidInputError(Messages.WEB_LISTEN_ERROR) from exc
    return 0
