"""プロンプト構築.

プロンプトは信頼済み命令、出力スキーマ、信頼済みルール、準信頼メモリ、
未信頼メタデータ、未信頼差分の固定順で構成し、未信頼部分は
常にエスケープ済み境界ブロックで囲む.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from self_improving_code_review.application.ports.llm_client import LlmRequest
from self_improving_code_review.application.review.agent_turn_schema import (
    build_agent_turn_schema,
)
from self_improving_code_review.application.review.models import ReviewBundle, ReviewPlan
from self_improving_code_review.application.review.output_schema import build_review_output_schema
from self_improving_code_review.application.review.prompt_loader import PromptTemplate
from self_improving_code_review.application.review.rule_budget import (
    render_rule_selection,
    select_rules_for_prompt,
)
from self_improving_code_review.domain.common.errors import PolicyViolationError
from self_improving_code_review.domain.common.tokens import estimate_tokens
from self_improving_code_review.domain.common.values import MemoryId, MemoryVersion
from self_improving_code_review.domain.memory.models import MemoryContent
from self_improving_code_review.domain.memory.retrieval import render_memory_block
from self_improving_code_review.domain.rule.models import ReviewRule
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.models import ScannedPayload
from self_improving_code_review.security.prompt_boundary import encode_untrusted_block

__all__ = ["BuiltPrompt", "ReviewContextBuilder"]


@dataclass(frozen=True, slots=True)
class BuiltPrompt:
    """構築済み要求と実行に記録する測定値."""

    request: LlmRequest
    prompt_version: str
    prompt_hash: str
    estimated_input_tokens: int
    rendered_rule_ids: tuple[str, ...]
    rendered_memory_ids: tuple[tuple[MemoryId, MemoryVersion], ...] = ()


class ReviewContextBuilder:
    """1 つのレビュー単位に対するレビュー要求を構築する."""

    def __init__(
        self,
        template: PromptTemplate,
        *,
        max_findings: int,
        temperature: float | None = None,
        agent_template: PromptTemplate | None = None,
        specialist_template: PromptTemplate | None = None,
    ) -> None:
        """プロンプトテンプレートと応答サイズ上限を保持する.

        Args:
            template: バージョン管理されたプロンプトテンプレート.
            max_findings: 1 つのレビュー単位から受け入れる指摘の最大数.
            temperature: 明示する場合のサンプリング温度。None はモデル既定値を使用する.
            agent_template: 動的コンテキスト取得時のレビュー担当ループ用テンプレート.
            specialist_template: M5 の補助担当専用プロンプトテンプレート.
        """
        self._template = template
        self._max_findings = max_findings
        self._temperature = temperature
        self._agent_template = agent_template
        self._specialist_template = specialist_template

    @property
    def prompt_version(self) -> str:
        """レビュー単位の実行前に記録するバージョンラベルを返す."""
        return self._template.version_label

    @property
    def prompt_hash(self) -> str:
        """テンプレート内容のハッシュを返す."""
        return self._template.content_hash

    @property
    def agent_prompt_version(self) -> str | None:
        """設定済みのレビュー担当ループ用プロンプト版を返す."""
        return self._agent_template.version_label if self._agent_template is not None else None

    @property
    def agent_prompt_hash(self) -> str | None:
        """設定済みのレビュー担当ループ用プロンプトハッシュを返す."""
        return self._agent_template.content_hash if self._agent_template is not None else None

    @property
    def specialist_prompt_version(self) -> str | None:
        """設定済みの補助担当プロンプト版を返す."""
        return self._specialist_template.version_label if self._specialist_template else None

    @property
    def specialist_prompt_hash(self) -> str | None:
        """設定済みの補助担当プロンプトハッシュを返す."""
        return self._specialist_template.content_hash if self._specialist_template else None

    def build(
        self,
        bundle: ReviewBundle,
        plan: ReviewPlan,
        rules: tuple[ReviewRule, ...],
        scanned_diff: ScannedPayload,
        memories: tuple[MemoryContent, ...] = (),
    ) -> BuiltPrompt:
        """``bundle`` の要求を構築する.

        Args:
            bundle: レビュー対象のレビュー単位.
            plan: 計画処理が作成した計画.
            rules: レビュー単位に適用可能なルール.
            scanned_diff: 機密情報検査で封印された差分入力.
            memories: 検索で選んだ準信頼のメモリ. 空の場合は注入しない.

        Returns:
            構築済みプロンプトと記録対象の測定値.

        Raises:
            PolicyViolationError: ``scanned_diff`` が送信予定のレビュー単位差分を
                封印していない場合.
        """
        if not scanned_diff.matches(bundle.diff_text):
            message = f"{Messages.BUNDLE_DIFF_MODIFIED_AFTER_SCAN}: {bundle.bundle_id}"
            raise PolicyViolationError(message)

        if plan.is_specialist:
            if self._specialist_template is None:
                message = Messages.SPECIALIST_TEMPLATE_NOT_CONFIGURED
                raise PolicyViolationError(message)
            template = self._specialist_template
        elif plan.is_context_tool_enabled and self._agent_template is not None:
            template = self._agent_template
        else:
            template = self._template
        output_schema = (
            build_agent_turn_schema(
                max_findings=self._max_findings,
                allow_tool_request=True,
            )
            if plan.is_context_tool_enabled
            else build_review_output_schema(max_findings=self._max_findings)
        )
        rule_selection = select_rules_for_prompt(rules, plan.budget.rule_tokens)
        rendered_rules = render_rule_selection(rule_selection)
        rendered_rule_ids = tuple(rule.id for rule in rule_selection.selected)
        reviewer_profile = template.profile_for(plan.reviewer_type) or plan.perspective
        user_content = template.user_template.format(
            bundle_id=str(bundle.bundle_id),
            perspective=plan.perspective,
            reviewer_profile=reviewer_profile,
            risk=bundle.risk.value,
            file_list="\n".join(f"- {path}" for path in bundle.paths),
            output_schema=json.dumps(output_schema, sort_keys=True, separators=(",", ":")),
            rules_block=rendered_rules,
            memories_block=_render_memories(memories),
            metadata_block=encode_untrusted_block(
                "change-metadata",
                self._render_metadata(bundle, plan, rendered_rule_ids),
            ),
            diff_block=encode_untrusted_block("bundle-diff", scanned_diff.text),
        )
        request = LlmRequest(
            system_instruction=template.system_instruction,
            user_content=user_content,
            output_schema=output_schema,
            max_output_tokens=plan.budget.max_output_tokens,
            temperature=self._temperature,
            request_label=self._request_label(bundle, plan),
        )
        return BuiltPrompt(
            request=request,
            prompt_version=template.version_label,
            prompt_hash=template.content_hash,
            estimated_input_tokens=estimate_tokens(template.system_instruction + user_content),
            rendered_rule_ids=rendered_rule_ids,
            rendered_memory_ids=tuple((content.memory_id, content.version) for content in memories),
        )

    @staticmethod
    def _request_label(bundle: ReviewBundle, plan: ReviewPlan) -> str:
        """データセット応答を担当単位で一意に選択できる呼出キーを返す."""
        if plan.is_specialist:
            return f"review-specialist-{plan.reviewer_type.value}:{bundle.bundle_id}"
        if plan.is_context_tool_enabled:
            return f"review-agent:{bundle.bundle_id}"
        return f"review:{bundle.bundle_id}"

    def _render_metadata(
        self,
        bundle: ReviewBundle,
        plan: ReviewPlan,
        rendered_rule_ids: tuple[str, ...],
    ) -> str:
        """ファイルごとのメタデータと実際に送信したルール ID を構築する."""
        rendered_ids = frozenset(rendered_rule_ids)
        rule_ids_by_path = {
            binding.path.value: tuple(
                rule_id for rule_id in binding.rule_ids if rule_id in rendered_ids
            )
            for binding in plan.file_rule_bindings
        }
        lines: list[str] = []
        for changed_file in bundle.files:
            language = changed_file.language or "unknown"
            applicable_rule_ids = rule_ids_by_path.get(changed_file.new_path.value, ())
            lines.append(
                json.dumps(
                    {
                        "added_lines": len(changed_file.added_line_numbers),
                        "applicable_rule_ids": applicable_rule_ids,
                        "change_type": changed_file.change_type.value,
                        "language": language,
                        "path": changed_file.new_path.value,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        return "\n".join(lines)


def _render_memories(memories: tuple[MemoryContent, ...]) -> str:
    """準信頼メモリブロックを構築する.

    メモリは検証済みの抽象化知識であり、境界ブロックで囲まない代わりに
    行、根拠、重大度を上書きできないことをシステム指示で固定する.
    """
    if not memories:
        return "No project memory applies to this bundle."
    return "\n".join(render_memory_block(content) for content in memories)
