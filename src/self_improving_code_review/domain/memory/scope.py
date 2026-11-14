"""メモリの適用範囲.

適用範囲はメモリを注入する条件を決める. グループ間は論理積、
グループ内は論理和で結合し、完全に空の適用範囲を許可しない.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch

from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import NormalizedPath
from self_improving_code_review.messages import Messages

__all__ = ["MemoryScope"]

_MAX_PATTERNS_PER_GROUP = 20


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """メモリを注入する条件.

    Attributes:
        languages: 対象言語.
        frameworks: 対象フレームワーク.
        paths: 正規化相対パスへのグロブ.
        symbols: 対象シンボル名.
        keywords: 追加行への大文字・小文字を区別した一致語.
        risk_tags: リスク分類または観点タグ.
    """

    languages: tuple[str, ...] = ()
    frameworks: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    risk_tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """完全に空の適用範囲と過大なグループを拒否する.

        Raises:
            InvalidInputError: どのグループにも値がない、グループの要素数が
                上限を超える、または空文字列を含む場合.
        """
        if not any(self._groups().values()):
            message = Messages.MEMORY_SCOPE_GROUP_REQUIRED
            raise InvalidInputError(message)
        for name, values in self._groups().items():
            if len(values) > _MAX_PATTERNS_PER_GROUP:
                message = (
                    f"{Messages.MEMORY_SCOPE_TOO_MANY_ITEMS}: {name}: "
                    f"最大 {_MAX_PATTERNS_PER_GROUP} 件"
                )
                raise InvalidInputError(message)
            if any(not value.strip() for value in values):
                message = f"{Messages.MEMORY_SCOPE_EMPTY_VALUE}: {name}"
                raise InvalidInputError(message)

    @property
    def is_unbounded_path_scope(self) -> bool:
        """パス条件が実質的に全ファイルへ一致する場合は True を返す.

        適用範囲の広いメモリを無審査で有効化しないよう、
        統制処理がこの判定を用いて注意喚起する.
        """
        return any(pattern.strip() in {"*", "**", "**/*"} for pattern in self.paths)

    def matches_path(self, path: NormalizedPath) -> bool:
        """パス条件に一致する場合は True を返す. 条件なしは一致とみなす."""
        if not self.paths:
            return True
        return any(fnmatch(path.value, pattern) for pattern in self.paths)

    def intersects(self, other: MemoryScope) -> bool:
        """``other`` と適用範囲が交差する可能性がある場合は True を返す.

        競合候補の抽出に用いるため、判定は保守的とする. 双方が値を持つ
        グループが 1 つでも共通要素を欠く場合だけ交差しないと判断する.
        """
        for name, left in self._groups().items():
            right = other._groups()[name]
            if left and right and not _groups_may_intersect(name, left, right):
                return False
        return True

    def _groups(self) -> dict[str, tuple[str, ...]]:
        """グループ名から値への対応表を安定順で返す."""
        return {
            "languages": self.languages,
            "frameworks": self.frameworks,
            "paths": self.paths,
            "symbols": self.symbols,
            "keywords": self.keywords,
            "risk_tags": self.risk_tags,
        }


def _groups_may_intersect(
    name: str,
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> bool:
    """二つの適用範囲グループが交差しうる場合は True を返す."""
    if frozenset(left) & frozenset(right):
        return True
    if name != "paths":
        return False
    for left_pattern in left:
        for right_pattern in right:
            if fnmatch(left_pattern, right_pattern) or fnmatch(right_pattern, left_pattern):
                return True
            if any(marker in left_pattern + right_pattern for marker in "*?["):
                # グロブ同士の完全な非交差証明は行わず、競合検出では保守的に扱う。
                return True
    return False
