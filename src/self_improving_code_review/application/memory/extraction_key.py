"""メモリ候補抽出の冪等性キー生成.

入力、プロンプト、モデルのいずれかが変わる場合だけ新しい抽出実行とする.
同じ根拠集合からは常に同じキーを生成し、再実行で候補を重複作成しない.
"""

from __future__ import annotations

from self_improving_code_review.domain.common.hashing import hash_lines, hash_text
from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.domain.feedback.models import FeedbackRecord

__all__ = ["build_extraction_key", "feedback_content_hash"]


def feedback_content_hash(record: FeedbackRecord) -> str:
    """抽出入力として固定した 1 件のフィードバック内容ハッシュを返す.

    同じ識別子でも内容が異なれば別入力として扱えるよう、ラベルと
    自由記述を含めてダイジェストを計算する.
    """
    return hash_lines(
        (
            str(record.feedback_id),
            record.label.value,
            str(record.finding_id) if record.finding_id is not None else "",
            record.location.file.value if record.location is not None else "",
            record.location.locator if record.location is not None else "",
            record.issue_type or "",
            record.severity.value if record.severity is not None else "",
            record.reason,
            record.evidence or "",
        )
    )


def build_extraction_key(
    *,
    project_id: ProjectId,
    source_revision: str | None,
    feedback: tuple[FeedbackRecord, ...],
    context_locator: str,
    prompt_hash: str,
    model: str,
) -> str:
    """抽出実行の冪等性キーを返す.

    Args:
        project_id: 候補を所属させるプロジェクト.
        source_revision: 根拠を確認した対象リビジョン.
        feedback: 抽出根拠として固定したフィードバック.
        context_locator: 追加取得したコンテキストの位置表現.
        prompt_hash: 抽出プロンプトの内容ハッシュ.
        model: 使用する固定済みモデル識別子.

    Returns:
        同一入力から再現できる SHA-256 ダイジェスト.
    """
    ordered = sorted(feedback, key=lambda item: item.feedback_id.value)
    return hash_lines(
        (
            "memory-extraction",
            str(project_id),
            source_revision or "unborn",
            ",".join(str(item.feedback_id) for item in ordered),
            ",".join(feedback_content_hash(item) for item in ordered),
            hash_text(context_locator),
            prompt_hash,
            model,
        )
    )
