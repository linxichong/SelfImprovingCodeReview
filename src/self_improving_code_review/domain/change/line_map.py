"""指摘位置の検証に使用する新しいファイルの行番号対応表."""

from __future__ import annotations

from dataclasses import dataclass

from self_improving_code_review.domain.change.models import ChangedFile
from self_improving_code_review.domain.common.values import LineRange

__all__ = ["LineMap", "build_new_line_map"]


@dataclass(frozen=True, slots=True)
class LineMap:
    """新しいリビジョンから見たファイルの行番号.

    Attributes:
        commentable_lines: 有効なコメント位置となる追加行.
        present_lines: 新しいファイルに存在する追加行とコンテキスト行.
    """

    commentable_lines: frozenset[int]
    present_lines: frozenset[int]

    def is_commentable(self, line: int) -> bool:
        """``line`` にコメントを紐付けられる場合は True を返す."""
        return line in self.commentable_lines

    def intersects_commentable(self, line_range: LineRange) -> bool:
        """``line_range`` が1行以上の追加行を含む場合は True を返す."""
        return any(line in self.commentable_lines for line in line_range.lines())

    def covers(self, line_range: LineRange) -> bool:
        """``line_range`` の全行が変更後ファイルに存在する場合は True を返す."""
        return all(line in self.present_lines for line in line_range.lines())


def build_new_line_map(changed_file: ChangedFile) -> LineMap:
    """解析済みの差分ブロックから ``changed_file`` の行対応表を構築する.

    結果ファイルに存在しない行にはコメントを付けられないため、
    削除行を除外する.
    """
    commentable: set[int] = set()
    present: set[int] = set()
    for hunk in changed_file.hunks:
        for line in hunk.lines:
            if line.new_line is None:
                continue
            present.add(line.new_line)
            if line.is_commentable:
                commentable.add(line.new_line)
    return LineMap(
        commentable_lines=frozenset(commentable),
        present_lines=frozenset(present),
    )
