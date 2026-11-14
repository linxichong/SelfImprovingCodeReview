"""Git リモートまたはローカルルートから安全なリポジトリ識別子を決定する."""

from __future__ import annotations

import re
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from self_improving_code_review.domain.common.hashing import hash_text

__all__ = ["RepositoryIdentity", "RepositoryIdentityResolver"]

_SCP_REMOTE = re.compile(r"^git@(?P<host>[^:]+):(?P<path>.+)$")


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    """プロジェクト対応に使用する提供元と外部識別子."""

    provider: str
    external_id: str


class RepositoryIdentityResolver:
    """Git の origin を優先し、存在しない場合はパスのハッシュを使用する."""

    def __init__(self, *, timeout_seconds: int = 10) -> None:
        """Git 取得処理のタイムアウトを保持する."""
        self._timeout_seconds = timeout_seconds

    def resolve(self, repository: Path) -> RepositoryIdentity:
        """生のローカルパスを公開せず安定したリポジトリ識別子を返す."""
        root = repository.resolve()
        remote = self._origin_url(root)
        if remote is None:
            return RepositoryIdentity("local", f"local:{hash_text(root.as_posix())}")
        host, path = self._parse_remote(remote)
        normalized_path = path.removesuffix(".git").strip("/")
        if host.lower() == "github.com":
            return RepositoryIdentity("github", normalized_path)
        return RepositoryIdentity("git", f"{host.lower()}/{normalized_path}")

    def _origin_url(self, repository: Path) -> str | None:
        """Origin URL を返し、未設定または取得失敗時は None を返す."""
        try:
            # Git は PATH 上の正式な実行ファイルを固定引数配列かつ shell=False で呼び出す。
            completed = subprocess.run(  # nosec B603, B607
                ["git", "config", "--get", "remote.origin.url"],
                cwd=repository,
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout_seconds,
            )
        except OSError, subprocess.TimeoutExpired:
            return None
        value = completed.stdout.strip()
        return value if completed.returncode == 0 and value else None

    @staticmethod
    def _parse_remote(remote: str) -> tuple[str, str]:
        """HTTPS/SSH URL または SCP 形式のリモートをホストとパスへ分解する."""
        scp_match = _SCP_REMOTE.fullmatch(remote)
        if scp_match is not None:
            return scp_match.group("host"), scp_match.group("path")
        parsed = urlsplit(remote)
        if parsed.hostname and parsed.path.strip("/"):
            return parsed.hostname, parsed.path
        return "unknown", hash_text(remote)
