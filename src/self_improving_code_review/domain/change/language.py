"""入力元ファイルの拡張子から言語識別子への対応付け."""

from __future__ import annotations

from self_improving_code_review.domain.common.values import NormalizedPath

__all__ = ["detect_language"]

_SUFFIX_LANGUAGES: dict[str, str] = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".md": "markdown",
    ".php": "php",
    ".py": "python",
    ".pyi": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".scala": "scala",
    ".sh": "shell",
    ".sql": "sql",
    ".swift": "swift",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".yaml": "yaml",
    ".yml": "yaml",
}

_FILENAME_LANGUAGES: dict[str, str] = {
    "dockerfile": "dockerfile",
    "makefile": "make",
}


def detect_language(path: NormalizedPath) -> str | None:
    """``path`` の言語識別子を返す. 不明な場合は None を返す.

    検出は拡張子に基づきファイル内容を読み取らないため、
    作業ツリーに存在しない削除済みファイルにも使用できる.
    """
    suffix_language = _SUFFIX_LANGUAGES.get(path.suffix)
    if suffix_language is not None:
        return suffix_language
    return _FILENAME_LANGUAGES.get(path.name.lower())
