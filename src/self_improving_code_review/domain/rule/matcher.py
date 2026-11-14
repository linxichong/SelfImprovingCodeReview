"""決定的なルール適用範囲照合.

適用範囲照合は LLM 呼び出し前に実行する. 言語、パスグロブ、キーワードを
ここで評価し、適用可能なルールだけをプロンプトへ含める.
"""

from __future__ import annotations

from fnmatch import fnmatch

from self_improving_code_review.domain.change.models import ChangedFile
from self_improving_code_review.domain.common.enums import LineKind
from self_improving_code_review.domain.rule.models import (
    FileRuleBinding,
    ReviewRule,
    RuleMatchExplanation,
    RuleResolution,
)

__all__ = ["RuleMatcher"]


class RuleMatcher:
    """変更ファイル集合に適用するルールを選択する."""

    def select(
        self, rules: tuple[ReviewRule, ...], changed_files: tuple[ChangedFile, ...]
    ) -> tuple[ReviewRule, ...]:
        """少なくとも 1 ファイルに一致するルールを安定した順序で返す.

        読み込み処理が生成した入力順(重大度の降順、ID の昇順)を維持し、
        プロンプト内容の再現性を保つ.
        """
        return self.resolve(rules, changed_files).rules

    def resolve(
        self, rules: tuple[ReviewRule, ...], changed_files: tuple[ChangedFile, ...]
    ) -> RuleResolution:
        """適用ルールの和集合とファイル単位の対応を決定的に返す."""
        bindings = tuple(
            FileRuleBinding(
                path=changed_file.new_path,
                rule_ids=tuple(rule.id for rule in rules if self.matches(rule, changed_file)),
            )
            for changed_file in changed_files
        )
        selected_ids = {rule_id for binding in bindings for rule_id in binding.rule_ids}
        selected_rules = tuple(rule for rule in rules if rule.id in selected_ids)
        return RuleResolution(rules=selected_rules, file_bindings=bindings)

    def matches(self, rule: ReviewRule, changed_file: ChangedFile) -> bool:
        """``rule`` の空でない適用範囲グループがすべて一致する場合は True を返す.

        グループ間は論理積、グループ内の値は論理和で結合する.
        """
        return self.explain(rule, changed_file).is_match

    def explain(self, rule: ReviewRule, changed_file: ChangedFile) -> RuleMatchExplanation:
        """実際の照合と同じ判定から、命中値および不一致グループを返す."""
        scope = rule.scope
        matched_languages = (
            tuple(language for language in scope.languages if language == changed_file.language)
            if scope.languages
            else ()
        )
        matched_paths = (
            tuple(
                pattern for pattern in scope.paths if fnmatch(changed_file.new_path.value, pattern)
            )
            if scope.paths
            else ()
        )
        added_text = tuple(
            line.text
            for hunk in changed_file.hunks
            for line in hunk.lines
            if line.kind is LineKind.ADDED
        )
        matched_keywords = (
            tuple(
                keyword for keyword in scope.keywords if any(keyword in text for text in added_text)
            )
            if scope.keywords
            else ()
        )
        failed_groups: list[str] = []
        if scope.languages and not matched_languages:
            failed_groups.append("languages")
        if scope.paths and not matched_paths:
            failed_groups.append("paths")
        if scope.keywords and not matched_keywords:
            failed_groups.append("keywords")
        return RuleMatchExplanation(
            matched_languages=matched_languages,
            matched_paths=matched_paths,
            matched_keywords=matched_keywords,
            failed_scope_groups=tuple(failed_groups),
        )
