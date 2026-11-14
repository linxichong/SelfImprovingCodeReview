"""ルールモデルおよび実行に固定されたルールスナップショット参照."""

from __future__ import annotations

import re
from dataclasses import dataclass

from self_improving_code_review.domain.common.enums import Severity
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_lines
from self_improving_code_review.domain.common.values import NormalizedPath
from self_improving_code_review.messages import Messages

__all__ = [
    "RULE_ID_PATTERN",
    "FileRuleBinding",
    "ReviewRule",
    "RuleMatchExplanation",
    "RuleResolution",
    "RuleScope",
    "RuleSnapshot",
    "RuleSnapshotItem",
    "is_valid_rule_id",
]

RULE_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:\.[A-Z][A-Z0-9_]*)+$")


def is_valid_rule_id(rule_id: str) -> bool:
    """``rule_id`` が ``<DOMAIN>.<ISSUE_TYPE>`` 規約に従う場合は True を返す."""
    return RULE_ID_PATTERN.match(rule_id) is not None


@dataclass(frozen=True, slots=True)
class RuleScope:
    """ルールの適用範囲.

    グループ間は論理積、グループ内の値は論理和で結合する. ルールが
    暗黙的に全体へ適用されないよう、少なくとも 1 グループを空でなくする.
    """

    languages: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """完全に空の適用範囲を拒否する.

        Raises:
            InvalidInputError: どの適用範囲グループにも値がない場合.
        """
        if not (self.languages or self.paths or self.keywords):
            message = Messages.RULE_SCOPE_GROUP_REQUIRED
            raise InvalidInputError(message)


@dataclass(frozen=True, slots=True)
class ReviewRule:
    """YAML から読み込んだ単一のレビュールール."""

    id: str
    category: str
    severity: Severity
    scope: RuleScope
    instruction: str
    source_path: str
    content_hash: str

    def __post_init__(self) -> None:
        """不正な識別子と空の指示を拒否する.

        Raises:
            InvalidInputError: ID が命名規約に違反する、または
                分類か命令が空の場合.
        """
        if not is_valid_rule_id(self.id):
            message = f"{Messages.RULE_ID_FORMAT_INVALID}: {self.id}"
            raise InvalidInputError(message)
        if self.id.startswith("GENERAL."):
            message = Messages.RULE_ID_GENERAL_RESERVED
            raise InvalidInputError(message)
        if not self.category.strip():
            message = f"{Messages.RULE_CATEGORY_REQUIRED}: {self.id}"
            raise InvalidInputError(message)
        if not self.instruction.strip():
            message = f"{Messages.RULE_INSTRUCTION_REQUIRED}: {self.id}"
            raise InvalidInputError(message)


@dataclass(frozen=True, slots=True)
class RuleMatchExplanation:
    """単一ファイルに対するルール適用範囲の決定根拠."""

    matched_languages: tuple[str, ...] = ()
    matched_paths: tuple[str, ...] = ()
    matched_keywords: tuple[str, ...] = ()
    failed_scope_groups: tuple[str, ...] = ()

    @property
    def is_match(self) -> bool:
        """すべての設定済み適用範囲グループが一致した場合は True を返す."""
        return not self.failed_scope_groups


@dataclass(frozen=True, slots=True)
class FileRuleBinding:
    """1 ファイルと、そのファイルに適用可能なルール ID の対応."""

    path: NormalizedPath
    rule_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuleResolution:
    """レビュー単位で選択したルールと、ファイル単位の適用関係."""

    rules: tuple[ReviewRule, ...]
    file_bindings: tuple[FileRuleBinding, ...]


@dataclass(frozen=True, slots=True)
class RuleSnapshotItem:
    """スナップショット参照に記録された単一ルール."""

    rule_id: str
    path: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class RuleSnapshot:
    """実行が使用したルールセットの再現用参照.

    スナップショットにルール本文は保存しない. 実行は ``snapshot_id`` を固定し、
    本文は ``git_sha`` から復元する. 空のルールセットも有効な
    設定であり、スナップショットを必ず 1 件生成する.
    """

    snapshot_id: str
    git_sha: str | None
    rule_set_hash: str
    items: tuple[RuleSnapshotItem, ...]
    rules: tuple[ReviewRule, ...]

    @property
    def is_empty(self) -> bool:
        """固定済みルールセットが空の場合は True を返す."""
        return not self.items

    def contains_rule(self, rule_id: str) -> bool:
        """``rule_id`` が固定済みルールセットに属する場合は True を返す."""
        return any(item.rule_id == rule_id for item in self.items)


def compute_rule_set_hash(items: tuple[RuleSnapshotItem, ...]) -> str:
    """``items`` の SHA-256 ルールセットハッシュを返す.

    項目をパス順に ``path + content_hash`` として連結するため、
    空のルールセットでも空の並びに対する安定したダイジェストを生成する.
    """
    ordered = sorted(items, key=lambda item: item.path)
    return hash_lines(f"{item.path}:{item.content_hash}" for item in ordered)
