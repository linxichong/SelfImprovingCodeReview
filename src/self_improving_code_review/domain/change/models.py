"""統一差分解析処理が生成する不変の差分モデル."""

from __future__ import annotations

from dataclasses import dataclass, field

from self_improving_code_review.domain.common.enums import ChangeType, LineKind
from self_improving_code_review.domain.common.values import NormalizedPath

__all__ = ["ChangedFile", "ChangedLine", "Changeset", "DiffHunk"]


@dataclass(frozen=True, slots=True)
class ChangedLine:
    """差分ブロック内の単一行.

    Attributes:
        kind: 行がコンテキスト、追加、削除のいずれか.
        old_line: 変更前ファイルの 1 始まり行番号. 追加行は None.
        new_line: 変更後ファイルの 1 始まり行番号. 削除行は None.
        text: 先頭の差分記号と改行を除いた行内容.
    """

    kind: LineKind
    old_line: int | None
    new_line: int | None
    text: str

    @property
    def is_commentable(self) -> bool:
        """コメントを紐付けられる追加行の場合のみ True を返す."""
        return self.kind is LineKind.ADDED


@dataclass(frozen=True, slots=True)
class DiffHunk:
    """ファイル差分内の単一の ``@@`` セクション."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: tuple[ChangedLine, ...]

    @property
    def added_lines(self) -> tuple[ChangedLine, ...]:
        """この差分ブロックの追加行をファイル内の順序で返す."""
        return tuple(line for line in self.lines if line.kind is LineKind.ADDED)

    def to_diff_text(self) -> str:
        """差分ブロックを統一差分テキストへ再構築する.

        再構築結果はプロンプト送信内容と根拠検証に使用するため、
        レビュー処理に提示した記号を再現しなければならない.
        """
        rendered = [self.header]
        for line in self.lines:
            marker = _LINE_MARKERS[line.kind]
            rendered.append(f"{marker}{line.text}")
        return "\n".join(rendered)


_LINE_MARKERS: dict[LineKind, str] = {
    LineKind.CONTEXT: " ",
    LineKind.ADDED: "+",
    LineKind.DELETED: "-",
}


@dataclass(frozen=True, slots=True)
class ChangedFile:
    """単一ファイルの差分.

    Attributes:
        new_path: 新しいリビジョンのパス. 削除時は変更前パス.
        old_path: 変更前リビジョンのパス. 追加時は None.
        change_type: Git が報告した変更種別.
        language: 拡張子から導出した言語識別子. 不明時は None.
        hunks: ファイル順の解析済み差分ブロック. バイナリと名前変更のみの場合は空.
        is_binary: Git がバイナリファイルとして報告した場合は True.
    """

    new_path: NormalizedPath
    old_path: NormalizedPath | None
    change_type: ChangeType
    language: str | None
    hunks: tuple[DiffHunk, ...] = field(default_factory=tuple)
    is_binary: bool = False

    @property
    def added_line_numbers(self) -> frozenset[int]:
        """指摘を紐付けられる変更後ファイルの全行番号を返す."""
        return frozenset(
            line.new_line
            for hunk in self.hunks
            for line in hunk.added_lines
            if line.new_line is not None
        )

    @property
    def has_commentable_line(self) -> bool:
        """追加行が1行以上存在する場合は True を返す."""
        return bool(self.added_line_numbers)

    def to_diff_text(self) -> str:
        """合成した ``diff --git`` ヘッダーを含め、ファイル差分を再構築する."""
        old = self.old_path.value if self.old_path is not None else self.new_path.value
        blocks = [f"diff --git a/{old} b/{self.new_path.value}"]
        if self.is_binary:
            blocks.append(f"Binary files a/{old} and b/{self.new_path.value} differ")
        else:
            blocks.extend(hunk.to_diff_text() for hunk in self.hunks)
        return "\n".join(blocks)


@dataclass(frozen=True, slots=True)
class Changeset:
    """単一の差分入力から解析した全ファイル."""

    files: tuple[ChangedFile, ...]

    def find(self, path: NormalizedPath) -> ChangedFile | None:
        """``path`` が指定する変更ファイルを返す。存在しない場合は None を返す."""
        for changed_file in self.files:
            if changed_file.new_path == path:
                return changed_file
        return None
