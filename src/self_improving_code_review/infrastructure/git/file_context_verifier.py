"""外部パッチと作業ツリーの版が一致することを Git で検証する."""

from __future__ import annotations

import subprocess  # nosec B404
from pathlib import Path

from self_improving_code_review.domain.common.errors import GitCommandError
from self_improving_code_review.messages import Messages

__all__ = ["GitFileContextVerifier"]


class GitFileContextVerifier:
    """FILE 差分を逆適用できる場合だけ作業ツリーを同一版と判定する."""

    def __init__(self, *, timeout_seconds: int = 30) -> None:
        """Git 検査のタイムアウトを保持する."""
        self._timeout_seconds = timeout_seconds

    def __call__(self, repository: Path, diff_text: str) -> bool:
        """差分が現在の作業ツリーへ逆適用可能な場合は True を返す."""
        try:
            # Git は PATH 上の正式な実行ファイルを固定引数配列かつ shell=False で呼び出す。
            completed = subprocess.run(  # nosec B603, B607
                ["git", "apply", "--reverse", "--check", "--whitespace=nowarn", "-"],
                cwd=repository.resolve(),
                input=diff_text,
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitCommandError(Messages.FILE_DIFF_VERIFICATION_FAILED) from exc
        return completed.returncode == 0
