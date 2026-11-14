"""プロンプトへ送るルールの決定的な予算選択と描画."""

from __future__ import annotations

from dataclasses import dataclass

from self_improving_code_review.domain.common.tokens import estimate_tokens
from self_improving_code_review.domain.rule.models import ReviewRule

__all__ = ["RuleBudgetSelection", "render_rule_selection", "select_rules_for_prompt"]


@dataclass(frozen=True, slots=True)
class RuleBudgetSelection:
    """ルール Token 予算による採用結果."""

    selected: tuple[ReviewRule, ...]
    excluded: tuple[ReviewRule, ...]
    estimated_tokens: int


def select_rules_for_prompt(
    rules: tuple[ReviewRule, ...], rule_token_budget: int
) -> RuleBudgetSelection:
    """重大度順のルールを実際の描画量で予算内に選択する.

    最上位ルールは予算値が小さくても必ず保持し、最初に収まらないルール以降を
    すべて除外する。これは正式レビューと診断コマンドで共有する契約である。
    """
    selected_count = 0
    used_tokens = 0
    for index, rule in enumerate(rules):
        block_tokens = estimate_tokens(_render_rule(rule))
        if index > 0 and used_tokens + block_tokens > rule_token_budget:
            break
        selected_count += 1
        used_tokens += block_tokens
    return RuleBudgetSelection(
        selected=rules[:selected_count],
        excluded=rules[selected_count:],
        estimated_tokens=used_tokens,
    )


def render_rule_selection(selection: RuleBudgetSelection) -> str:
    """選択済みルールをプロンプト用テキストへ変換する."""
    if not selection.selected:
        return (
            "このレビュー単位に適用できるプロジェクトルールはありません。"
            "rule_id にプロジェクトルール ID を使用してはいけません。"
            "一般的なレビュー観点による指摘では、必ず "
            "GENERAL.<ISSUE_TYPE> を使用してください。"
        )
    return "\n".join(_render_rule(rule) for rule in selection.selected)


def _render_rule(rule: ReviewRule) -> str:
    """単一ルールの安定したプロンプト表現を返す."""
    return (
        f"- id: {rule.id}\n"
        f"  category: {rule.category}\n"
        f"  severity: {rule.severity.value}\n"
        f"  instruction: {rule.instruction.strip()}"
    )
