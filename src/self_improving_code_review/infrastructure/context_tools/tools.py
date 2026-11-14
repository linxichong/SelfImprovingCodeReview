"""シェルを使用しない限定ファイル・シンボル・コード検索."""

from __future__ import annotations

import ast

from self_improving_code_review.application.ports.context_source import ContextSource
from self_improving_code_review.application.review.context_tools import (
    ContextFragment,
    ContextToolExecutor,
    ToolResult,
    ValidatedToolRequest,
)
from self_improving_code_review.domain.change.models import ChangedFile
from self_improving_code_review.domain.common.enums import ToolStatus
from self_improving_code_review.domain.common.values import NormalizedPath
from self_improving_code_review.security.path_policy import PathPolicy

__all__ = ["RepositoryContextToolExecutor"]


class RepositoryContextToolExecutor(ContextToolExecutor):
    """リポジトリ用の 4 種類の限定ツールを実行する."""

    def __init__(
        self,
        changed_files: tuple[ChangedFile, ...],
        *,
        path_policy: PathPolicy,
        max_results: int,
        max_scan_files: int = 10_000,
    ) -> None:
        """変更ファイル集合、禁止パス、検索上限を保持する."""
        self._changed_files = {
            changed_file.new_path.value: changed_file for changed_file in changed_files
        }
        self._path_policy = path_policy
        self._max_results = max_results
        self._max_scan_files = max_scan_files

    def execute(
        self,
        request: ValidatedToolRequest,
        source: ContextSource,
    ) -> ToolResult:
        """要求された限定ツールを実行する."""
        if request.tool_name == "get_changed_file":
            return self._get_changed_file(request)
        if request.tool_name == "read_file":
            return self._read_file(request, source)
        if request.tool_name == "find_symbol":
            return self._find_symbol(request, source)
        return self._search_code(request, source)

    def _get_changed_file(self, request: ValidatedToolRequest) -> ToolResult:
        """当該レビュー単位の変更ファイル差分を返す."""
        if request.path is None:
            return ToolResult(ToolStatus.INVALID_REQUEST, reason_code="PATH_REQUIRED")
        changed_file = self._changed_files.get(request.path.value)
        if changed_file is None:
            return ToolResult(ToolStatus.NOT_FOUND, reason_code="CHANGED_FILE_NOT_FOUND")
        text = changed_file.to_diff_text()
        line_count = max(1, len(text.splitlines()))
        return ToolResult(
            ToolStatus.SUCCESS,
            (ContextFragment(request.path, 1, line_count, text),),
        )

    def _read_file(
        self,
        request: ValidatedToolRequest,
        source: ContextSource,
    ) -> ToolResult:
        """指定した連続行を返す."""
        if request.path is None or request.start_line is None or request.end_line is None:
            return ToolResult(ToolStatus.INVALID_REQUEST, reason_code="RANGE_REQUIRED")
        text = source.read_text(request.path)
        if text is None:
            return ToolResult(ToolStatus.NOT_FOUND, reason_code="FILE_NOT_FOUND")
        lines = text.splitlines()
        if request.start_line > len(lines):
            return ToolResult(ToolStatus.NOT_FOUND, reason_code="LINE_NOT_FOUND")
        end_line = min(request.end_line, len(lines))
        fragment = "\n".join(lines[request.start_line - 1 : end_line])
        return ToolResult(
            ToolStatus.SUCCESS,
            (ContextFragment(request.path, request.start_line, end_line, fragment),),
        )

    def _find_symbol(
        self,
        request: ValidatedToolRequest,
        source: ContextSource,
    ) -> ToolResult:
        """Python 定義を抽象構文木で検索する."""
        if request.query is None:
            return ToolResult(ToolStatus.INVALID_REQUEST, reason_code="QUERY_REQUIRED")
        paths = self._candidate_paths(request, source, suffix=".py")
        fragments: list[ContextFragment] = []
        for path in paths:
            text = source.read_text(path)
            if text is None:
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            lines = text.splitlines()
            nodes = (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == request.query
            )
            for node in nodes:
                start_line = node.lineno
                end_line = min(node.end_lineno or node.lineno, start_line + 199)
                fragments.append(
                    ContextFragment(
                        path,
                        start_line,
                        end_line,
                        "\n".join(lines[start_line - 1 : end_line]),
                    )
                )
        if not fragments:
            return ToolResult(ToolStatus.NOT_FOUND, reason_code="SYMBOL_NOT_FOUND")
        return ToolResult(ToolStatus.SUCCESS, tuple(fragments[: self._max_results]))

    def _search_code(
        self,
        request: ValidatedToolRequest,
        source: ContextSource,
    ) -> ToolResult:
        """大文字小文字を区別するリテラル検索を行う."""
        if request.query is None:
            return ToolResult(ToolStatus.INVALID_REQUEST, reason_code="QUERY_REQUIRED")
        fragments: list[ContextFragment] = []
        for path in self._candidate_paths(request, source):
            text = source.read_text(path)
            if text is None:
                continue
            lines = text.splitlines()
            for line_number, line in enumerate(lines, start=1):
                if request.query not in line:
                    continue
                start_line = max(1, line_number - 5)
                end_line = min(len(lines), line_number + 5)
                fragments.append(
                    ContextFragment(
                        path,
                        start_line,
                        end_line,
                        "\n".join(lines[start_line - 1 : end_line]),
                    )
                )
                if len(fragments) >= self._max_results:
                    return ToolResult(ToolStatus.SUCCESS, tuple(fragments))
        if not fragments:
            return ToolResult(ToolStatus.NOT_FOUND, reason_code="CODE_NOT_FOUND")
        return ToolResult(ToolStatus.SUCCESS, tuple(fragments))

    def _candidate_paths(
        self,
        request: ValidatedToolRequest,
        source: ContextSource,
        *,
        suffix: str | None = None,
    ) -> tuple[NormalizedPath, ...]:
        """検索可能なパスを除外方針適用後の安定順で返す."""
        return tuple(
            path
            for path in source.list_paths(
                request.path_scope,
                max_files=self._max_scan_files,
            )
            if not self._path_policy.is_blocked(path) and (suffix is None or path.suffix == suffix)
        )
