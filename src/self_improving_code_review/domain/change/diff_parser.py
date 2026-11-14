"""統一差分解析処理.

解析処理はレビューパイプラインの決定的な基盤である. 指摘が参照可能な
すべての行番号をここで生成するため、不正入力は修復せず
拒否する.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from self_improving_code_review.domain.change.language import detect_language
from self_improving_code_review.domain.change.models import (
    ChangedFile,
    ChangedLine,
    Changeset,
    DiffHunk,
)
from self_improving_code_review.domain.common.enums import ChangeType, LineKind
from self_improving_code_review.domain.common.errors import InvalidDiffError, InvalidInputError
from self_improving_code_review.domain.common.values import NormalizedPath
from self_improving_code_review.messages import Messages

__all__ = ["UnifiedDiffParser"]

_FILE_HEADER = re.compile(r"^diff --git a/(?P<old>.+?) b/(?P<new>.+?)$")
_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)
_NO_NEWLINE_MARKER = "\\ No newline at end of file"


@dataclass
class _FileState:
    """解析中ファイルの可変な蓄積領域."""

    old_path: str
    new_path: str
    change_type: ChangeType | None = None
    is_binary: bool = False
    hunks: list[DiffHunk] = field(default_factory=list)


class UnifiedDiffParser:
    """統一差分テキストを不変の :class:`Changeset` に解析する."""

    def parse(self, diff_text: str) -> Changeset:
        """``diff_text`` を変更集合に解析する.

        Args:
            diff_text: ``git diff`` または ``git show`` が生成した統一差分.

        Returns:
            解析済みの変更集合. 空の差分からは空の変更集合を生成する.

        Raises:
            InvalidDiffError: ヘッダーが不正、差分ブロックの行数が
                ヘッダーと一致しない、またはパスがリポジトリルート外へ出る場合.
        """
        if "\x00" in diff_text:
            message = Messages.DIFF_CONTAINS_NUL
            raise InvalidDiffError(message)

        files: list[ChangedFile] = []
        state: _FileState | None = None
        lines = diff_text.splitlines()
        index = 0

        while index < len(lines):
            line = lines[index]
            header_match = _FILE_HEADER.match(line)
            if header_match is not None:
                if state is not None:
                    files.append(self._build_file(state))
                state = _FileState(
                    old_path=header_match.group("old"),
                    new_path=header_match.group("new"),
                )
                index += 1
                continue

            if state is None:
                index += 1
                continue

            if _HUNK_HEADER.match(line) is not None:
                hunk, index = self._parse_hunk(lines, index)
                state.hunks.append(hunk)
                continue

            self._apply_metadata(state, line)
            index += 1

        if state is not None:
            files.append(self._build_file(state))
        return Changeset(files=tuple(files))

    def _apply_metadata(self, state: _FileState, line: str) -> None:
        """最初の差分ブロックより前にあるファイル単位のメタデータ行を記録する."""
        if line.startswith("new file mode"):
            state.change_type = ChangeType.ADDED
        elif line.startswith("deleted file mode"):
            state.change_type = ChangeType.DELETED
        elif line.startswith("rename from "):
            state.old_path = line.removeprefix("rename from ")
            state.change_type = ChangeType.RENAMED
        elif line.startswith("rename to "):
            state.new_path = line.removeprefix("rename to ")
            state.change_type = ChangeType.RENAMED
        elif line.startswith("copy to "):
            state.new_path = line.removeprefix("copy to ")
            state.change_type = ChangeType.COPIED
        elif line.startswith(("GIT binary patch", "Binary files")):
            state.is_binary = True

    def _parse_hunk(self, lines: list[str], start_index: int) -> tuple[DiffHunk, int]:
        """``start_index`` から始まる1つの差分ブロックを解析する.

        Returns:
            解析済み差分ブロックと最初の未処理行の位置.

        Raises:
            InvalidDiffError: ヘッダーが不正、または実際の行数が
                ヘッダーの行数と異なる場合.
        """
        header = lines[start_index]
        match = _HUNK_HEADER.match(header)
        if match is None:
            message = f"{Messages.HUNK_HEADER_MALFORMED}: {header}"
            raise InvalidDiffError(message)

        old_start = int(match.group("old_start"))
        new_start = int(match.group("new_start"))
        old_count = int(match.group("old_count") or "1")
        new_count = int(match.group("new_count") or "1")

        old_cursor = old_start
        new_cursor = new_start
        observed_old = 0
        observed_new = 0
        changed_lines: list[ChangedLine] = []
        index = start_index + 1

        while index < len(lines):
            line = lines[index]
            if _HUNK_HEADER.match(line) is not None or _FILE_HEADER.match(line) is not None:
                break
            if line == _NO_NEWLINE_MARKER:
                index += 1
                continue
            if observed_old >= old_count and observed_new >= new_count:
                break

            marker = line[:1]
            text = line[1:]
            if marker == "+":
                changed_lines.append(
                    ChangedLine(kind=LineKind.ADDED, old_line=None, new_line=new_cursor, text=text)
                )
                new_cursor += 1
                observed_new += 1
            elif marker == "-":
                changed_lines.append(
                    ChangedLine(
                        kind=LineKind.DELETED, old_line=old_cursor, new_line=None, text=text
                    )
                )
                old_cursor += 1
                observed_old += 1
            elif marker in {" ", ""}:
                changed_lines.append(
                    ChangedLine(
                        kind=LineKind.CONTEXT,
                        old_line=old_cursor,
                        new_line=new_cursor,
                        text=text,
                    )
                )
                old_cursor += 1
                new_cursor += 1
                observed_old += 1
                observed_new += 1
            else:
                message = f"{Messages.DIFF_LINE_MARKER_UNEXPECTED}: {marker!r}: {header}"
                raise InvalidDiffError(message)
            index += 1

        if observed_old != old_count or observed_new != new_count:
            message = (
                f"{Messages.HUNK_LINE_COUNT_MISMATCH}: {header}: "
                f"期待値 -{old_count} +{new_count}、実測値 -{observed_old} +{observed_new}"
            )
            raise InvalidDiffError(message)

        hunk = DiffHunk(
            old_start=old_start,
            old_count=old_count,
            new_start=new_start,
            new_count=new_count,
            header=header,
            lines=tuple(changed_lines),
        )
        return hunk, index

    def _build_file(self, state: _FileState) -> ChangedFile:
        """蓄積した状態を不変の :class:`ChangedFile` へ変換する.

        Raises:
            InvalidDiffError: いずれかのパスがリポジトリルート外へ出る場合.
        """
        change_type = state.change_type or ChangeType.MODIFIED
        try:
            new_path = NormalizedPath(state.new_path)
            old_path = NormalizedPath(state.old_path)
        except InvalidInputError as exc:
            message = f"{Messages.DIFF_HEADER_PATH_INVALID}: {exc}"
            raise InvalidDiffError(message) from exc

        effective_new_path = old_path if change_type is ChangeType.DELETED else new_path

        return ChangedFile(
            new_path=effective_new_path,
            old_path=None if change_type is ChangeType.ADDED else old_path,
            change_type=change_type,
            language=detect_language(effective_new_path),
            hunks=tuple(state.hunks),
            is_binary=state.is_binary,
        )
