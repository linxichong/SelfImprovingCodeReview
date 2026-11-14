"""メモリ抽出プロンプトの構築.

プロンプトは信頼済み指示、出力スキーマ、未信頼フィードバック、
未信頼コード断片の固定順で構成する. 未信頼部分は必ず境界ブロックで囲み、
機密情報検査で封印した送信内容だけを埋め込む.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from self_improving_code_review.application.memory.candidate_schema import (
    build_memory_candidate_schema,
)
from self_improving_code_review.application.ports.llm_client import LlmRequest
from self_improving_code_review.application.review.prompt_loader import PromptTemplate
from self_improving_code_review.domain.common.errors import PolicyViolationError
from self_improving_code_review.domain.common.tokens import estimate_tokens
from self_improving_code_review.domain.feedback.models import FeedbackRecord
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.models import ScannedPayload
from self_improving_code_review.security.prompt_boundary import encode_untrusted_block

__all__ = ["BuiltExtractionPrompt", "ExtractionContextBuilder", "render_feedback_payload"]


@dataclass(frozen=True, slots=True)
class BuiltExtractionPrompt:
    """構築済み抽出要求と抽出実行へ記録する測定値."""

    request: LlmRequest
    prompt_version: str
    prompt_hash: str
    estimated_input_tokens: int


def render_feedback_payload(feedback: tuple[FeedbackRecord, ...]) -> str:
    """機密情報検査へ渡すフィードバック送信内容を構築する.

    識別子順に並べることで、同じ根拠集合から常に同じ送信内容を生成し、
    抽出の冪等性キーと検査結果を再現できるようにする.

    Args:
        feedback: 抽出根拠として固定した保存済みフィードバック.

    Returns:
        未信頼データとして検査および送信する平文テキスト.
    """
    ordered = sorted(feedback, key=lambda item: item.feedback_id.value)
    blocks: list[str] = []
    for item in ordered:
        lines = [
            f"feedback_id: {item.feedback_id}",
            f"label: {item.label.value}",
        ]
        if item.finding_id is not None:
            lines.append(f"finding_id: {item.finding_id}")
        if item.location is not None:
            lines.append(f"file: {item.location.file.value}")
            lines.append(f"location: {item.location.locator}")
        if item.issue_type is not None:
            lines.append(f"issue_type: {item.issue_type}")
        if item.severity is not None:
            lines.append(f"severity: {item.severity.value}")
        lines.append(f"reason: {item.reason}")
        if item.evidence is not None:
            lines.append(f"evidence: {item.evidence}")
        blocks.append("\n".join(lines))
    return "\n---\n".join(blocks)


class ExtractionContextBuilder:
    """1 回のメモリ候補抽出に対する要求を構築する."""

    def __init__(
        self,
        template: PromptTemplate,
        *,
        max_output_tokens: int,
        temperature: float | None = None,
    ) -> None:
        """プロンプトテンプレートと応答サイズ上限を保持する.

        Args:
            template: バージョン管理された抽出プロンプトテンプレート.
            max_output_tokens: 候補 1 件の応答に許可する最大トークン数.
            temperature: 明示する場合のサンプリング温度。None はモデル既定値を使用する.
        """
        self._template = template
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature

    @property
    def prompt_version(self) -> str:
        """抽出実行へ記録するバージョンラベルを返す."""
        return self._template.version_label

    @property
    def prompt_hash(self) -> str:
        """テンプレート内容のハッシュを返す."""
        return self._template.content_hash

    def build(
        self,
        scanned_payload: ScannedPayload,
        *,
        project_id: str,
        feedback_text: str,
        context_text: str = "",
    ) -> BuiltExtractionPrompt:
        """封印済みフィードバックから抽出要求を構築する.

        Args:
            scanned_payload: 機密情報検査で封印した最終送信内容.
            project_id: 候補を所属させるプロジェクト識別子.
            feedback_text: 封印済み送信内容に含まれるフィードバック部分.
            context_text: 対象リビジョンから取得した必要最小限のコード断片.

        Returns:
            構築済み要求と記録対象の測定値.

        Raises:
            PolicyViolationError: 封印内容と送信予定の内容が一致しない場合.
        """
        expected_payload = feedback_text if not context_text else f"{feedback_text}\n{context_text}"
        if not scanned_payload.matches(expected_payload):
            message = Messages.EXTRACTION_PAYLOAD_MODIFIED_AFTER_SCAN
            raise PolicyViolationError(message)

        output_schema = build_memory_candidate_schema()
        user_content = self._template.user_template.format(
            project_id=project_id,
            output_schema=json.dumps(output_schema, sort_keys=True, separators=(",", ":")),
            feedback_block=encode_untrusted_block("feedback", feedback_text),
            context_block=encode_untrusted_block("code-context", context_text),
        )
        request = LlmRequest(
            system_instruction=self._template.system_instruction,
            user_content=user_content,
            output_schema=output_schema,
            max_output_tokens=self._max_output_tokens,
            temperature=self._temperature,
            request_label="memory-extraction",
        )
        return BuiltExtractionPrompt(
            request=request,
            prompt_version=self._template.version_label,
            prompt_hash=self._template.content_hash,
            estimated_input_tokens=estimate_tokens(
                self._template.system_instruction + user_content
            ),
        )
