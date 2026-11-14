"""ドメイン層で共有する値オブジェクト."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.messages import Messages

__all__ = [
    "BundleId",
    "EvaluationId",
    "ExtractionId",
    "FeedbackId",
    "FindingId",
    "GroundTruthIssueId",
    "GroundTruthSetId",
    "LineRange",
    "MemoryId",
    "MemoryVersion",
    "NormalizedPath",
    "ProjectId",
    "RunId",
]

_PROJECT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_UUID4_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_PROJECT_ID_MAX_LENGTH = 64
_GROUND_TRUTH_SPLITS: frozenset[str] = frozenset({"development", "test"})


@dataclass(frozen=True, slots=True)
class ProjectId:
    """プロジェクト間でルール、メモリ、指摘を分離する識別子."""

    value: str

    def __post_init__(self) -> None:
        """定義された文字種と長さの範囲外にある識別子を拒否する.

        Raises:
            InvalidInputError: 値が空、長すぎる、または形式不正の場合.
        """
        if not 1 <= len(self.value) <= _PROJECT_ID_MAX_LENGTH:
            message = f"{Messages.PROJECT_ID_LENGTH_INVALID}: {_PROJECT_ID_MAX_LENGTH}"
            raise InvalidInputError(message)
        if _PROJECT_ID_PATTERN.match(self.value) is None:
            message = Messages.PROJECT_ID_FORMAT_INVALID
            raise InvalidInputError(message)

    def __str__(self) -> str:
        """未加工の識別子を返す."""
        return self.value


@dataclass(frozen=True, slots=True)
class RunId:
    """単一レビュー実行の UUIDv4 識別子."""

    value: str

    def __post_init__(self) -> None:
        """小文字の UUIDv4 文字列ではない値を拒否する.

        Raises:
            InvalidInputError: 値が UUIDv4 文字列ではない場合.
        """
        if _UUID4_PATTERN.match(self.value) is None:
            message = Messages.RUN_ID_INVALID
            raise InvalidInputError(message)

    def __str__(self) -> str:
        """未加工の識別子を返す."""
        return self.value


@dataclass(frozen=True, slots=True)
class BundleId:
    """単調増加する連番から生成する実行内レビュー単位識別子."""

    run_id: RunId
    sequence: int

    def __post_init__(self) -> None:
        """正でない連番を拒否する.

        Raises:
            InvalidInputError: ``sequence`` が 1 未満の場合.
        """
        if self.sequence < 1:
            message = Messages.BUNDLE_SEQUENCE_INVALID
            raise InvalidInputError(message)

    def __str__(self) -> str:
        """安定した外部表現 ``B<sequence>`` を返す."""
        return f"B{self.sequence:03d}"


@dataclass(frozen=True, slots=True)
class FindingId:
    """レビュー単位と連番から生成する実行内指摘識別子."""

    bundle_id: BundleId
    sequence: int

    def __post_init__(self) -> None:
        """正でない連番を拒否する.

        Raises:
            InvalidInputError: ``sequence`` が 1 未満の場合.
        """
        if self.sequence < 1:
            message = Messages.FINDING_SEQUENCE_INVALID
            raise InvalidInputError(message)

    def __str__(self) -> str:
        """安定した外部表現 ``<bundle>-F<sequence>`` を返す."""
        return f"{self.bundle_id}-F{self.sequence:03d}"


@dataclass(frozen=True, slots=True)
class NormalizedPath:
    """リポジトリ相対の POSIX パス.

    絶対パス、親ディレクトリへの移動、NUL バイト、Windows 区切り文字を
    拒否し、差分がリポジトリ外のファイルを指せないようにする.
    """

    value: str

    def __post_init__(self) -> None:
        """パスを検証して正規化する.

        Raises:
            InvalidInputError: パスがリポジトリルート外へ出る、または
                後続のファイルアクセスに安全でない文字を含む場合.
        """
        raw = self.value
        if not raw:
            message = Messages.PATH_EMPTY
            raise InvalidInputError(message)
        if "\x00" in raw:
            message = Messages.PATH_CONTAINS_NUL
            raise InvalidInputError(message)
        if "\\" in raw:
            message = f"{Messages.PATH_NOT_POSIX}: {raw}"
            raise InvalidInputError(message)
        if raw.startswith("/"):
            message = f"{Messages.PATH_NOT_RELATIVE}: {raw}"
            raise InvalidInputError(message)

        parts: list[str] = []
        for part in PurePosixPath(raw).parts:
            if part == "..":
                message = f"{Messages.PATH_TRAVERSES_OUTSIDE}: {raw}"
                raise InvalidInputError(message)
            parts.append(part)
        if not parts:
            message = f"{Messages.PATH_NOT_FILE}: {raw}"
            raise InvalidInputError(message)
        object.__setattr__(self, "value", "/".join(parts))

    @property
    def suffix(self) -> str:
        """先頭のドットを含む、末尾の拡張子を小文字で返す."""
        return PurePosixPath(self.value).suffix.lower()

    @property
    def name(self) -> str:
        """パスの最後の要素を返す."""
        return PurePosixPath(self.value).name

    @property
    def stem(self) -> str:
        """拡張子を除いたパスの最後の要素を返す."""
        return PurePosixPath(self.value).stem

    @property
    def parent(self) -> str:
        """親ディレクトリを POSIX 文字列で返す. ルートの場合は ``""`` を返す."""
        parent = PurePosixPath(self.value).parent
        return "" if str(parent) == "." else str(parent)

    def __str__(self) -> str:
        """正規化済みのパス文字列を返す."""
        return self.value


@dataclass(frozen=True, slots=True)
class LineRange:
    """新しいファイル内の両端を含む 1 始まり行範囲."""

    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        """1始まりでない、または順序が不正な範囲を拒否する.

        Raises:
            InvalidInputError: 境界値が 1 未満、または範囲が逆転している場合.
        """
        if self.start_line < 1:
            message = Messages.START_LINE_INVALID
            raise InvalidInputError(message)
        if self.end_line < self.start_line:
            message = Messages.END_LINE_INVALID
            raise InvalidInputError(message)

    def contains(self, line: int) -> bool:
        """``line`` が範囲内にある場合は True を返す."""
        return self.start_line <= line <= self.end_line

    def lines(self) -> range:
        """対象となる行番号の範囲を返す."""
        return range(self.start_line, self.end_line + 1)

    def overlaps(self, other: LineRange) -> bool:
        """``other`` と 1 行以上重なる場合は True を返す."""
        return self.start_line <= other.end_line and other.start_line <= self.end_line


@dataclass(frozen=True, slots=True)
class FeedbackId:
    """追記のみで管理するフィードバックレコードの UUIDv4 識別子."""

    value: str

    def __post_init__(self) -> None:
        """小文字の UUIDv4 文字列ではない値を拒否する.

        Raises:
            InvalidInputError: 値が UUIDv4 文字列ではない場合.
        """
        if _UUID4_PATTERN.match(self.value) is None:
            message = Messages.FEEDBACK_ID_INVALID
            raise InvalidInputError(message)

    def __str__(self) -> str:
        """未加工の識別子を返す."""
        return self.value


@dataclass(frozen=True, slots=True)
class ExtractionId:
    """メモリ候補抽出実行の UUIDv4 識別子."""

    value: str

    def __post_init__(self) -> None:
        """小文字の UUIDv4 文字列ではない値を拒否する.

        Raises:
            InvalidInputError: 値が UUIDv4 文字列ではない場合.
        """
        if _UUID4_PATTERN.match(self.value) is None:
            message = Messages.EXTRACTION_ID_INVALID
            raise InvalidInputError(message)

    def __str__(self) -> str:
        """未加工の識別子を返す."""
        return self.value


@dataclass(frozen=True, slots=True)
class MemoryId:
    """論理的なメモリを識別する UUIDv4 識別子.

    内容は ``(memory_id, version)`` で不変化するため、この識別子は
    バージョンをまたいで同じ知識を指す.
    """

    value: str

    def __post_init__(self) -> None:
        """小文字の UUIDv4 文字列ではない値を拒否する.

        Raises:
            InvalidInputError: 値が UUIDv4 文字列ではない場合.
        """
        if _UUID4_PATTERN.match(self.value) is None:
            message = Messages.MEMORY_ID_INVALID
            raise InvalidInputError(message)

    def __str__(self) -> str:
        """未加工の識別子を返す."""
        return self.value


@dataclass(frozen=True, slots=True)
class MemoryVersion:
    """メモリ内で 1 から単調増加する内容バージョン.

    欠番は許容するが、同じ番号を再利用してはならない.
    """

    value: int

    def __post_init__(self) -> None:
        """1 未満のバージョンを拒否する.

        Raises:
            InvalidInputError: 値が 1 未満の場合.
        """
        if self.value < 1:
            message = Messages.MEMORY_VERSION_INVALID
            raise InvalidInputError(message)

    def next(self) -> MemoryVersion:
        """次のバージョンを返す."""
        return MemoryVersion(self.value + 1)

    def __str__(self) -> str:
        """バージョン番号を十進表記で返す."""
        return str(self.value)


@dataclass(frozen=True, slots=True)
class GroundTruthSetId:
    """発行済み正解データ集合の識別子.

    集合は ``split`` と ``version`` で不変化するため、識別子は
    ``<split>-v<version>`` 形式の再現可能な値とする.
    """

    split: str
    version: int

    def __post_init__(self) -> None:
        """未知の分割名と 1 未満のバージョンを拒否する.

        Raises:
            InvalidInputError: 分割名が規約外、またはバージョンが 1 未満の場合.
        """
        if self.split not in _GROUND_TRUTH_SPLITS:
            message = f"{Messages.GROUND_TRUTH_SPLIT_INVALID}: {sorted(_GROUND_TRUTH_SPLITS)}"
            raise InvalidInputError(message)
        if self.version < 1:
            message = Messages.GROUND_TRUTH_SET_VERSION_INVALID
            raise InvalidInputError(message)

    def __str__(self) -> str:
        """安定した外部表現 ``<split>-v<version>`` を返す."""
        return f"{self.split}-v{self.version}"


@dataclass(frozen=True, slots=True)
class GroundTruthIssueId:
    """正解データ集合内で一意な問題識別子."""

    set_id: GroundTruthSetId
    sequence: int

    def __post_init__(self) -> None:
        """正でない連番を拒否する.

        Raises:
            InvalidInputError: ``sequence`` が 1 未満の場合.
        """
        if self.sequence < 1:
            message = Messages.GROUND_TRUTH_ISSUE_SEQUENCE_INVALID
            raise InvalidInputError(message)

    def __str__(self) -> str:
        """安定した外部表現 ``<set>-I<sequence>`` を返す."""
        return f"{self.set_id}-I{self.sequence:04d}"


@dataclass(frozen=True, slots=True)
class EvaluationId:
    """単一の比較群実行を識別する UUIDv4 識別子."""

    value: str

    def __post_init__(self) -> None:
        """小文字の UUIDv4 文字列ではない値を拒否する.

        Raises:
            InvalidInputError: 値が UUIDv4 文字列ではない場合.
        """
        if _UUID4_PATTERN.match(self.value) is None:
            message = Messages.EVALUATION_ID_INVALID
            raise InvalidInputError(message)

    def __str__(self) -> str:
        """未加工の識別子を返す."""
        return self.value
