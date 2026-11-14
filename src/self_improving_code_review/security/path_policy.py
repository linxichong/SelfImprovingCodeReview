"""アクセス禁止パスの方針.

この方針は既知の認証情報ファイルに対する多層防御であり、
パスに関係なく全送信内容に実行する内容ベースの機密情報検査を代替しない.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch

from self_improving_code_review.domain.common.values import NormalizedPath

__all__ = ["DEFAULT_BLOCKED_PATTERNS", "PathPolicy"]

DEFAULT_BLOCKED_PATTERNS: tuple[str, ...] = (
    ".git",
    ".git/**",
    ".review",
    ".review/**",
    "**/.venv/**",
    "**/node_modules/**",
    "**/vendor/**",
    "**/*.jks",
    "**/*.key",
    "**/*.p12",
    "**/*.pem",
    "**/*.pfx",
    "**/.aws/credentials",
    "**/credentials.json",
    "**/secrets.json",
    "**/secrets.yaml",
    "**/secrets.yml",
    "**/.env",
    "**/.env.*",
    "**/.netrc",
    "**/.npmrc",
    "**/.pypirc",
    "**/id_dsa",
    "**/id_ecdsa",
    "**/id_rsa",
    "**/*.7z",
    "**/*.dll",
    "**/*.dylib",
    "**/*.exe",
    "**/*.gif",
    "**/*.ico",
    "**/*.jar",
    "**/*.jpeg",
    "**/*.jpg",
    "**/*.pdf",
    "**/*.png",
    "**/*.so",
    "**/*.ttf",
    "**/*.webp",
    "**/*.woff",
    "**/*.woff2",
    "**/*.zip",
    ".env",
    ".env.*",
    ".secrets.baseline",
)


@dataclass(frozen=True, slots=True)
class PathPolicy:
    """リポジトリ内のパスを読み取り可能か判定する."""

    extra_patterns: tuple[str, ...] = field(default_factory=tuple)

    def is_blocked(self, path: NormalizedPath) -> bool:
        """``path`` がアクセス禁止パターンに一致する場合は True を返す.

        認証情報ファイルでは大文字と小文字が混在することが多いため、
        大文字と小文字を区別せず照合する.
        """
        lowered = path.value.lower()
        patterns = DEFAULT_BLOCKED_PATTERNS + self.extra_patterns
        return any(fnmatch(lowered, pattern.lower()) for pattern in patterns)
