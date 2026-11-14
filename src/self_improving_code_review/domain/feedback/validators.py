"""フィードバック入力の検証と冪等性キー生成.

自由記述は未信頼入力として扱う. 長さ、制御文字、プロンプト境界を
検査してから保存し、検索やプロンプトへ未加工のまま渡さない.
"""

from __future__ import annotations

import re

from self_improving_code_review.domain.common.enums import FeedbackLabel
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_lines, hash_text
from self_improving_code_review.domain.common.values import FindingId, RunId
from self_improving_code_review.domain.feedback.models import IssueLocation

__all__ = [
    "MAX_FREE_TEXT_LENGTH",
    "build_finding_feedback_key",
    "build_missed_issue_key",
    "normalize_free_text",
]

MAX_FREE_TEXT_LENGTH = 2000
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BOUNDARY_MARKERS: tuple[str, ...] = ("<<<", ">>>", "UNTRUSTED_DATA_BEGIN", "UNTRUSTED_DATA_END")


def normalize_free_text(field_name: str, value: str, *, allow_empty: bool = False) -> str:
    """自由記述を検証して正規化する.

    Args:
        field_name: 診断に使用するフィールド名.
        value: 利用者が入力した未信頼テキスト.
        allow_empty: 空文字列を許可する場合は True.

    Returns:
        前後の空白を除去したテキスト.

    Raises:
        InvalidInputError: 空、長すぎる、制御文字を含む、または
            プロンプト境界マーカーを含む場合.
    """
    normalized = value.strip()
    if not normalized:
        if allow_empty:
            return ""
        message = f"{field_name} must not be empty"
        raise InvalidInputError(message)
    if len(normalized) > MAX_FREE_TEXT_LENGTH:
        message = f"{field_name} must be at most {MAX_FREE_TEXT_LENGTH} characters"
        raise InvalidInputError(message)
    if _CONTROL_CHARACTERS.search(normalized) is not None:
        message = f"{field_name} must not contain control characters"
        raise InvalidInputError(message)
    for marker in _BOUNDARY_MARKERS:
        if marker in normalized:
            message = f"{field_name} must not contain prompt boundary markers"
            raise InvalidInputError(message)
    return normalized


def build_finding_feedback_key(
    *,
    run_id: RunId,
    finding_id: FindingId,
    actor_id: str,
    label: FeedbackLabel,
    reason: str,
) -> str:
    """既存指摘評価の冪等性キーを返す.

    同じ実行者が同じ指摘へ同じラベルと理由を再送した場合に、
    重複レコードを作らないための決定的なキーとする.
    """
    return hash_lines(
        (
            "feedback:finding",
            str(run_id),
            str(finding_id),
            actor_id,
            label.value,
            hash_text(reason),
        )
    )


def build_missed_issue_key(
    *,
    run_id: RunId,
    location: IssueLocation,
    issue_type: str,
    evidence: str,
) -> str:
    """見逃し問題の冪等性キーを返す.

    位置と根拠が同じ報告は同一の見逃しとみなし、報告者が異なっても
    重複レコードを作らない.
    """
    return hash_lines(
        (
            "feedback:missed-issue",
            str(run_id),
            location.file.value,
            location.locator,
            issue_type,
            hash_text(evidence),
        )
    )
