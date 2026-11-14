"""位置と根拠に対する決定的な検証処理."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import cast

from self_improving_code_review.domain.change.line_map import LineMap
from self_improving_code_review.domain.change.models import ChangedFile
from self_improving_code_review.domain.common.enums import ValidationStatus
from self_improving_code_review.domain.common.values import LineRange

__all__ = [
    "EvidenceValidationResult",
    "EvidenceValidator",
    "PositionValidator",
    "PythonSyntaxValidator",
    "ValidatorResult",
]


@dataclass(frozen=True, slots=True)
class ValidatorResult:
    """単一検証処理の結果."""

    status: ValidationStatus
    reason_code: str | None = None

    @property
    def is_valid(self) -> bool:
        """検証処理が候補を受理した場合は True を返す."""
        return self.status is ValidationStatus.VALID


@dataclass(frozen=True, slots=True)
class EvidenceValidationResult:
    """根拠検証結果と、根拠から決定した変更後行範囲."""

    status: ValidationStatus
    line_range: LineRange | None = None
    reason_code: str | None = None

    @property
    def is_valid(self) -> bool:
        """根拠を一意の追加行へ対応付けられた場合は True を返す."""
        return self.status is ValidationStatus.VALID


class PositionValidator:
    """指摘が新しいファイルのコメント可能行に紐づくことを検証する."""

    def validate(self, line_range: LineRange, line_map: LineMap) -> ValidatorResult:
        """位置検証結果を返す.

        範囲内の全行が新しいファイルに存在し、少なくとも 1 行が追加行の場合だけ
        有効とする. 変更されていない行や削除行にはコメントを
        付けられないためである.
        """
        if not line_map.covers(line_range):
            return ValidatorResult(status=ValidationStatus.INVALID, reason_code="LINE_NOT_IN_DIFF")
        if not line_map.intersects_commentable(line_range):
            return ValidatorResult(
                status=ValidationStatus.INVALID, reason_code="LINE_NOT_COMMENTABLE"
            )
        return ValidatorResult(status=ValidationStatus.VALID)


class EvidenceValidator:
    """根拠の引用を送信済み差分内の一意な追加行へ対応付ける."""

    def __init__(self, *, min_quote_length: int = 3) -> None:
        """許容する引用の最小長を保持する.

        Args:
            min_quote_length: コード断片を識別できる引用の最小長.
                これより短い引用は検証不能として拒否する.
        """
        self._min_quote_length = min_quote_length

    def validate(self, quote: str, changed_file: ChangedFile) -> EvidenceValidationResult:
        """根拠検証結果と決定的に導出した行範囲を返す.

        Args:
            quote: モデルが報告した断片.
            changed_file: モデルへ送信した解析済みファイル差分.
        """
        normalized_quote = quote.strip()
        if len(normalized_quote) < self._min_quote_length:
            return EvidenceValidationResult(
                status=ValidationStatus.INVALID, reason_code="EVIDENCE_TOO_SHORT"
            )

        locations = _find_evidence_locations(normalized_quote, changed_file)
        if not locations:
            return EvidenceValidationResult(
                status=ValidationStatus.INVALID, reason_code="EVIDENCE_NOT_IN_PAYLOAD"
            )
        if len(locations) > 1:
            return EvidenceValidationResult(
                status=ValidationStatus.INVALID, reason_code="EVIDENCE_AMBIGUOUS"
            )
        return EvidenceValidationResult(
            status=ValidationStatus.VALID,
            line_range=next(iter(locations)),
        )


class PythonSyntaxValidator:
    """Python 構文エラー候補を変更後コード断片で決定的に反証する."""

    def rejection_reason(self, issue_type: str, changed_file: ChangedFile) -> str | None:
        """構文的に正しい変更へ構文エラーを指摘した場合の理由コードを返す."""
        normalized_issue_type = "".join(
            character for character in issue_type.upper() if character.isalnum()
        )
        if normalized_issue_type != "SYNTAXERROR" or changed_file.language != "python":
            return None
        if len(changed_file.hunks) != 1 or changed_file.hunks[0].new_start != 1:
            return None

        source = "\n".join(
            line.text for line in changed_file.hunks[0].lines if line.new_line is not None
        )
        try:
            ast.parse(source)
        except SyntaxError:
            return None
        return "PYTHON_SYNTAX_VALID"


def _find_evidence_locations(quote: str, changed_file: ChangedFile) -> set[LineRange]:
    """引用と一致する一行または連続した追加行の範囲を返す."""
    quote_lines = quote.splitlines()
    if len(quote_lines) == 1:
        fragments = _quote_line_fragments(quote_lines[0])
        return {
            LineRange(line.new_line, line.new_line)
            for hunk in changed_file.hunks
            for line in hunk.added_lines
            if line.new_line is not None
            and any(fragment in line.text.strip() for fragment in fragments)
        }

    locations: set[LineRange] = set()
    expected_lines = tuple(_quote_line_fragments(line) for line in quote_lines)
    for hunk in changed_file.hunks:
        width = len(expected_lines)
        for index in range(len(hunk.lines) - width + 1):
            window = hunk.lines[index : index + width]
            if not all(line.is_commentable and line.new_line is not None for line in window):
                continue
            if not all(
                line.text.strip() in expected
                for line, expected in zip(window, expected_lines, strict=True)
            ):
                continue
            # 直前の全行検査により、追加行の変更後行番号は None ではない.
            start_line = cast(int, window[0].new_line)
            end_line = cast(int, window[-1].new_line)
            locations.add(LineRange(start_line, end_line))
    return locations


def _quote_line_fragments(line: str) -> frozenset[str]:
    """差分記号の有無を吸収した引用行の比較用断片を返す."""
    stripped = line.strip()
    fragments = {stripped}
    if stripped[:1] in {"+", "-"}:
        fragments.add(stripped[1:].strip())
    return frozenset(fragment for fragment in fragments if fragment)
