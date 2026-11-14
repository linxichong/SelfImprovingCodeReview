"""Git の固定リビジョンから検証用ファイルを読み取るアダプター."""

from __future__ import annotations

import re
import subprocess  # nosec B404
from pathlib import Path

from self_improving_code_review.domain.common.errors import GitCommandError, InvalidInputError
from self_improving_code_review.domain.common.values import NormalizedPath
from self_improving_code_review.messages import Messages

__all__ = ["GitRevisionContentReader"]

_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
_MAX_CONTEXT_BYTES = 2 * 1024 * 1024


class GitRevisionContentReader:
    """Git オブジェクトをシェルを介さず上限付きで読み取る."""

    def __init__(self, repository: Path, *, timeout_seconds: int = 30) -> None:
        """検証済みリポジトリルートとタイムアウトを保持する."""
        self._repository = repository.resolve()
        self._timeout_seconds = timeout_seconds

    def read_text(self, revision: str, path: NormalizedPath) -> str | None:
        """固定コミット上の UTF-8 ファイルを返す."""
        if _COMMIT_SHA.fullmatch(revision) is None:
            raise InvalidInputError(Messages.FEEDBACK_REVISION_INVALID)
        try:
            # Git は PATH 上の正式な実行ファイルを固定引数配列かつ shell=False で呼び出す。
            completed = subprocess.run(  # nosec B603, B607
                ["git", "show", f"{revision}:{path.value}"],
                cwd=self._repository,
                capture_output=True,
                check=False,
                timeout=self._timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitCommandError(Messages.FEEDBACK_REVISION_READ_FAILED) from exc
        if completed.returncode != 0:
            return None
        if len(completed.stdout) > _MAX_CONTEXT_BYTES:
            raise InvalidInputError(Messages.FEEDBACK_SOURCE_TOO_LARGE)
        try:
            return completed.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidInputError(Messages.FEEDBACK_SOURCE_NOT_UTF8) from exc
