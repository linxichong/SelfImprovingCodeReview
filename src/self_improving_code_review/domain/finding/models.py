"""指摘候補および検証済み指摘のモデル."""

from __future__ import annotations

import re
from dataclasses import dataclass

from self_improving_code_review.domain.common.enums import (
    ReflectionStatus,
    Severity,
    ValidationStatus,
)
from self_improving_code_review.domain.common.values import (
    BundleId,
    FindingId,
    LineRange,
    NormalizedPath,
)

__all__ = [
    "CandidateFinding",
    "Evidence",
    "Finding",
    "ValidationOutcome",
    "build_general_rule_id",
]

_ISSUE_TYPE_SEPARATOR = re.compile(r"[^A-Z0-9]+")


def build_general_rule_id(issue_type: str) -> str:
    """プロジェクトルールに依存しない指摘の予約 ID を返す."""
    normalized = _ISSUE_TYPE_SEPARATOR.sub("_", issue_type.upper()).strip("_")
    return f"GENERAL.{normalized or 'ISSUE'}"


@dataclass(frozen=True, slots=True)
class Evidence:
    """指摘の根拠.

    Attributes:
        quote: モデルへ送信した差分に存在すべき原文断片.
        reason: 引用が問題を示す理由の説明.
    """

    quote: str
    reason: str


@dataclass(frozen=True, slots=True)
class CandidateFinding:
    """検証前にレビュー処理が返す構造化候補."""

    file: NormalizedPath
    line_range: LineRange
    category: str
    issue_type: str
    rule_id: str
    primary_entity: str
    severity: Severity
    title: str
    description: str
    evidence: Evidence
    suggestion: str
    needs_context: bool


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    """候補に適用した決定的な検証処理の結果.

    Attributes:
        position_status: 行範囲が追加行に紐づいているか.
        evidence_status: 根拠の引用が送信済み差分に含まれるか.
        reflection_status: 反証状態. M1 では常に自己検証を省略する.
        reason_codes: 無効状態を説明する機械可読コード.
    """

    position_status: ValidationStatus
    evidence_status: ValidationStatus
    reflection_status: ReflectionStatus
    reason_codes: tuple[str, ...] = ()

    @property
    def is_displayable(self) -> bool:
        """指摘が主要指摘一覧に属する場合は True を返す.

        システム設計の表示条件を反映する. 位置と根拠が有効であり、
        自己検証で指摘への反証や追加コンテキスト要求が
        発生していないことを条件とする.
        """
        return (
            self.position_status is ValidationStatus.VALID
            and self.evidence_status is ValidationStatus.VALID
            and self.reflection_status
            in {ReflectionStatus.SUPPORTED, ReflectionStatus.REFLECTION_SKIPPED}
        )


@dataclass(frozen=True, slots=True)
class Finding:
    """決定的な識別子を付与した検証済み指摘."""

    finding_id: FindingId
    bundle_id: BundleId
    candidate: CandidateFinding
    fingerprint: str
    validation: ValidationOutcome
    enclosing_symbol: str

    @property
    def is_main(self) -> bool:
        """指摘を主要一覧に表示する場合は True を返す."""
        return self.validation.is_displayable

    @property
    def severity(self) -> Severity:
        """候補の重大度を返す."""
        return self.candidate.severity
