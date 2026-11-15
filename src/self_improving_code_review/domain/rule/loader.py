"""ルール YAML 読み込み処理.

読み込み失敗は入力エラーとする. 検証できないルールセットで
実行を開始してはならない.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from self_improving_code_review.domain.common.enums import Severity
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.rule.models import (
    ReviewRule,
    RuleScope,
    RuleSnapshot,
    RuleSnapshotItem,
    compute_rule_set_hash,
)
from self_improving_code_review.messages import Messages

__all__ = ["RULE_GLOB", "RuleLoader"]

RULE_GLOB = "*.yaml"
_REQUIRED_FIELDS: frozenset[str] = frozenset({"id", "category", "severity", "scope", "instruction"})
_SCOPE_FIELDS: frozenset[str] = frozenset({"languages", "paths", "keywords"})


class RuleLoader:
    """``config/rules/**/*.yaml`` を読み込み、検証してルールスナップショットにする."""

    def load(self, rules_root: Path, *, git_sha: str | None) -> RuleSnapshot:
        """``rules_root`` 配下の全ルールファイルを読み込む.

        ディレクトリが存在しない場合も空の場合も有効な設定として扱い、
        空のスナップショットを生成する.

        Args:
            rules_root: ルール YAML ファイルを格納するディレクトリ.
            git_sha: ルール本文を復元できるリビジョン.

        Returns:
            実行に固定されたスナップショット.

        Raises:
            InvalidInputError: ファイルを読み取れない、スキーマに違反する、
                または重複するルール ID を宣言している場合.
        """
        rules: list[ReviewRule] = []
        seen_ids: dict[str, str] = {}

        for rule_path in self._discover(rules_root):
            relative_path = rule_path.relative_to(rules_root).as_posix()
            rule = self._load_file(rule_path, relative_path)
            previous_path = seen_ids.get(rule.id)
            if previous_path is not None:
                message = (
                    f"{Messages.RULE_ID_DUPLICATED}: {rule.id}: {relative_path}: "
                    f"{Messages.RULE_PREVIOUS_DEFINITION_LABEL} {previous_path}"
                )
                raise InvalidInputError(message)
            seen_ids[rule.id] = relative_path
            rules.append(rule)

        rules.sort(key=lambda rule: (-rule.severity.rank, rule.id))
        items = tuple(
            RuleSnapshotItem(rule_id=rule.id, path=rule.source_path, content_hash=rule.content_hash)
            for rule in rules
        )
        rule_set_hash = compute_rule_set_hash(items)
        return RuleSnapshot(
            snapshot_id=rule_set_hash[:32],
            git_sha=git_sha,
            rule_set_hash=rule_set_hash,
            items=items,
            rules=tuple(rules),
        )

    def _discover(self, rules_root: Path) -> list[Path]:
        """ルールファイルをパスの昇順で返す."""
        if not rules_root.is_dir():
            return []
        return sorted(
            (path for path in rules_root.rglob(RULE_GLOB) if path.is_file()),
            key=lambda path: path.as_posix(),
        )

    def _load_file(self, rule_path: Path, relative_path: str) -> ReviewRule:
        """1つのルールファイルを解析して検証する.

        Raises:
            InvalidInputError: ファイルが UTF-8 ではない、YAML の対応表ではない、
                またはルールスキーマに違反する場合.
        """
        try:
            raw_text = rule_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            message = f"{Messages.RULE_FILE_READ_FAILED}: {relative_path}: {exc}"
            raise InvalidInputError(message) from exc

        try:
            document = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            message = f"{Messages.RULE_FILE_YAML_INVALID}: {relative_path}"
            raise InvalidInputError(message) from exc

        if not isinstance(document, dict):
            message = f"{Messages.RULE_FILE_NOT_MAPPING}: {relative_path}"
            raise InvalidInputError(message)

        self._reject_unknown_fields(document, relative_path)
        content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        return ReviewRule(
            id=self._require_str(document, "id", relative_path),
            category=self._require_str(document, "category", relative_path),
            severity=self._parse_severity(document["severity"], relative_path),
            scope=self._parse_scope(document["scope"], relative_path),
            instruction=self._require_str(document, "instruction", relative_path),
            source_path=relative_path,
            content_hash=content_hash,
        )

    def _reject_unknown_fields(self, document: dict[str, Any], relative_path: str) -> None:
        """必須フィールドの欠落と未知の最上位フィールドを拒否する.

        Raises:
            InvalidInputError: 必須フィールドがない、または未知のフィールドが
                存在する場合.
        """
        keys = frozenset(document)
        missing = _REQUIRED_FIELDS - keys
        if missing:
            message = f"{Messages.RULE_FILE_FIELDS_MISSING}: {relative_path}: {sorted(missing)}"
            raise InvalidInputError(message)
        unknown = keys - _REQUIRED_FIELDS
        if unknown:
            message = f"{Messages.RULE_FILE_FIELDS_UNKNOWN}: {relative_path}: {sorted(unknown)}"
            raise InvalidInputError(message)

    def _require_str(self, document: dict[str, Any], field_name: str, relative_path: str) -> str:
        """空でない文字列フィールドを返す.

        Raises:
            InvalidInputError: フィールドが空でない文字列ではない場合.
        """
        value = document[field_name]
        if not isinstance(value, str) or not value.strip():
            message = f"{Messages.RULE_FILE_FIELD_NOT_STRING}: {relative_path}: {field_name}"
            raise InvalidInputError(message)
        return value.strip()

    def _parse_severity(self, value: object, relative_path: str) -> Severity:
        """外部の重大度値を内部の列挙型へ変換する.

        Raises:
            InvalidInputError: 値が 4 種類の重大度のいずれでもない場合.
        """
        if not isinstance(value, str):
            message = f"{Messages.RULE_FILE_SEVERITY_NOT_STRING}: {relative_path}"
            raise InvalidInputError(message)
        try:
            return Severity(value)
        except ValueError as exc:
            allowed = sorted(item.value for item in Severity)
            message = f"{Messages.RULE_FILE_SEVERITY_INVALID}: {relative_path}: 許可値 {allowed}"
            raise InvalidInputError(message) from exc

    def _parse_scope(self, value: object, relative_path: str) -> RuleScope:
        """``scope`` マッピングを :class:`RuleScope` へ変換する.

        Raises:
            InvalidInputError: 適用範囲が文字列配列の対応表ではない、または
                未知のグループを含む場合.
        """
        if not isinstance(value, dict):
            message = f"{Messages.RULE_FILE_SCOPE_NOT_MAPPING}: {relative_path}"
            raise InvalidInputError(message)
        unknown = frozenset(value) - _SCOPE_FIELDS
        if unknown:
            message = (
                f"{Messages.RULE_FILE_SCOPE_FIELDS_UNKNOWN}: {relative_path}: {sorted(unknown)}"
            )
            raise InvalidInputError(message)
        try:
            return RuleScope(
                languages=self._parse_str_array(value, "languages", relative_path),
                paths=self._parse_str_array(value, "paths", relative_path),
                keywords=self._parse_str_array(value, "keywords", relative_path),
            )
        except InvalidInputError as exc:
            message = f"{Messages.RULE_FILE_INVALID}: {relative_path}: {exc}"
            raise InvalidInputError(message) from exc

    def _parse_str_array(
        self, scope: dict[str, Any], field_name: str, relative_path: str
    ) -> tuple[str, ...]:
        """適用範囲グループを空でない文字列のタプルとして返す.

        Raises:
            InvalidInputError: グループが空でない文字列の一覧ではない場合.
        """
        if field_name not in scope:
            return ()
        raw = scope[field_name]
        if not isinstance(raw, list):
            message = f"{Messages.RULE_FILE_SCOPE_NOT_ARRAY}: {relative_path}: scope.{field_name}"
            raise InvalidInputError(message)
        values: list[str] = []
        for element in raw:
            if not isinstance(element, str) or not element.strip():
                message = (
                    f"{Messages.RULE_FILE_SCOPE_EMPTY_STRING}: {relative_path}: scope.{field_name}"
                )
                raise InvalidInputError(message)
            values.append(element.strip())
        return tuple(values)
