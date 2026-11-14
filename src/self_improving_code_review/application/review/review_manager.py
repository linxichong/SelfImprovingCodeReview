"""指摘の検証、統合、並べ替え.

管理処理は詳細設計の決定的な検証順序を適用する:
ファイル所属、行範囲、根拠、ルール識別情報、フィンガープリント、重複排除.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from self_improving_code_review.application.review.models import ReviewBundle
from self_improving_code_review.application.review.response_parser import RejectedCandidate
from self_improving_code_review.domain.change.line_map import build_new_line_map
from self_improving_code_review.domain.change.models import ChangedFile
from self_improving_code_review.domain.common.enums import (
    ReflectionStatus,
    ValidationStatus,
)
from self_improving_code_review.domain.common.values import FindingId
from self_improving_code_review.domain.finding.deduplication import deduplicate
from self_improving_code_review.domain.finding.fingerprint import (
    build_fingerprint,
    find_enclosing_symbol,
)
from self_improving_code_review.domain.finding.models import (
    CandidateFinding,
    Finding,
    ValidationOutcome,
    build_general_rule_id,
)
from self_improving_code_review.domain.finding.validators import (
    EvidenceValidator,
    PositionValidator,
    PythonSyntaxValidator,
)
from self_improving_code_review.domain.rule.models import FileRuleBinding, RuleSnapshot

__all__ = ["BundleReviewOutcome", "ReviewManager", "RunAggregation"]


@dataclass(frozen=True, slots=True)
class BundleReviewOutcome:
    """1 つのレビュー単位の検証済み指摘."""

    findings: tuple[Finding, ...]
    rejected: tuple[RejectedCandidate, ...]


@dataclass(frozen=True, slots=True)
class RunAggregation:
    """重複排除と並べ替え後に実行単位で分類した指摘."""

    main_findings: tuple[Finding, ...]
    reference_findings: tuple[Finding, ...]
    duplicates: tuple[Finding, ...]
    invalid_position_count: int
    invalid_evidence_count: int


class ReviewManager:
    """候補を検証し、複数のレビュー単位にまたがる指摘を集約する."""

    def __init__(
        self,
        *,
        position_validator: PositionValidator | None = None,
        evidence_validator: EvidenceValidator | None = None,
        syntax_validator: PythonSyntaxValidator | None = None,
    ) -> None:
        """検証処理を保持する.

        Args:
            position_validator: 行の紐付け検証処理.
            evidence_validator: 根拠引用の検証処理.
            syntax_validator: Python 構文エラー候補の反証処理.
        """
        self._position_validator = position_validator or PositionValidator()
        self._evidence_validator = evidence_validator or EvidenceValidator()
        self._syntax_validator = syntax_validator or PythonSyntaxValidator()

    def validate_bundle(
        self,
        bundle: ReviewBundle,
        candidates: tuple[CandidateFinding, ...],
        _sent_payload: str,
        rule_snapshot: RuleSnapshot,
        rendered_rule_ids: tuple[str, ...] = (),
        file_rule_bindings: tuple[FileRuleBinding, ...] = (),
    ) -> BundleReviewOutcome:
        """``candidates`` を実際に送信した内容に照らして検証する.

        Args:
            bundle: 候補が属するレビュー単位.
            candidates: レビュー応答から解析した候補.
            _sent_payload: 呼び出し契約として受け取る封印済み差分テキスト.
            rule_snapshot: 実行に固定されたルールセット.
            rendered_rule_ids: 実際にプロンプトへ構築したルール ID.
            file_rule_bindings: ファイルごとの適用ルール ID.

        Returns:
            検証済み指摘と検証時に除外された候補.
        """
        files_by_path = {changed_file.new_path.value: changed_file for changed_file in bundle.files}
        findings: list[Finding] = []
        rejected: list[RejectedCandidate] = []
        sequence = 1

        for candidate in candidates:
            candidate = _normalize_general_rule_id(candidate, rule_snapshot)
            changed_file = files_by_path.get(candidate.file.value)
            if changed_file is None:
                rejected.append(
                    RejectedCandidate(reason_code="FILE_NOT_IN_BUNDLE", detail=candidate.file.value)
                )
                continue
            rule_error = self._rule_reference_error(
                candidate,
                rule_snapshot,
                rendered_rule_ids,
                file_rule_bindings,
            )
            if rule_error is not None:
                rejected.append(RejectedCandidate(reason_code=rule_error, detail=candidate.rule_id))
                continue

            syntax_error = self._syntax_validator.rejection_reason(
                candidate.issue_type, changed_file
            )
            if syntax_error is not None:
                rejected.append(
                    RejectedCandidate(reason_code=syntax_error, detail=candidate.rule_id)
                )
                continue

            effective_candidate, outcome = self._validate_candidate(candidate, changed_file)
            finding_id = FindingId(bundle_id=bundle.bundle_id, sequence=sequence)
            enclosing_symbol = find_enclosing_symbol(changed_file, effective_candidate.line_range)
            findings.append(
                Finding(
                    finding_id=finding_id,
                    bundle_id=bundle.bundle_id,
                    candidate=effective_candidate,
                    fingerprint=build_fingerprint(
                        rule_id=effective_candidate.rule_id,
                        path=effective_candidate.file,
                        enclosing_symbol=enclosing_symbol,
                        primary_entity=effective_candidate.primary_entity,
                    ),
                    validation=outcome,
                    enclosing_symbol=enclosing_symbol,
                )
            )
            sequence += 1

        return BundleReviewOutcome(findings=tuple(findings), rejected=tuple(rejected))

    def aggregate(self, findings: tuple[Finding, ...]) -> RunAggregation:
        """``findings`` の重複を排除し、主要一覧と参照一覧へ分ける.

        主要指摘は重大度の降順、パス、開始行の順に並べるため、
        同じ実行では常に同じ出力順になる.
        """
        kept, duplicates = deduplicate(findings)
        main: list[Finding] = []
        reference: list[Finding] = []
        invalid_position = 0
        invalid_evidence = 0

        for finding in kept:
            validation = finding.validation
            if validation.position_status is ValidationStatus.INVALID:
                invalid_position += 1
            if validation.evidence_status is ValidationStatus.INVALID:
                invalid_evidence += 1

            if validation.is_displayable:
                main.append(finding)
            elif validation.reflection_status is ReflectionStatus.NEEDS_CONTEXT:
                reference.append(finding)

        return RunAggregation(
            main_findings=tuple(sorted(main, key=_sort_key)),
            reference_findings=tuple(sorted(reference, key=_sort_key)),
            duplicates=duplicates,
            invalid_position_count=invalid_position,
            invalid_evidence_count=invalid_evidence,
        )

    def _validate_candidate(
        self,
        candidate: CandidateFinding,
        changed_file: ChangedFile,
    ) -> tuple[CandidateFinding, ValidationOutcome]:
        """根拠から位置を決定し、候補と検証結果を返す."""
        evidence = self._evidence_validator.validate(candidate.evidence.quote, changed_file)
        effective_candidate = (
            replace(candidate, line_range=evidence.line_range)
            if evidence.line_range is not None
            else candidate
        )
        line_map = build_new_line_map(changed_file)
        position = self._position_validator.validate(effective_candidate.line_range, line_map)
        reason_codes = tuple(
            code for code in (position.reason_code, evidence.reason_code) if code is not None
        )
        reflection_status = (
            ReflectionStatus.NEEDS_CONTEXT
            if candidate.needs_context
            else ReflectionStatus.REFLECTION_SKIPPED
        )
        return (
            effective_candidate,
            ValidationOutcome(
                position_status=position.status,
                evidence_status=evidence.status,
                reflection_status=reflection_status,
                reason_codes=reason_codes,
            ),
        )

    def _rule_reference_error(
        self,
        candidate: CandidateFinding,
        rule_snapshot: RuleSnapshot,
        rendered_rule_ids: tuple[str, ...],
        file_rule_bindings: tuple[FileRuleBinding, ...],
    ) -> str | None:
        """ルール参照を検証し、不正な場合は理由コードを返す."""
        if not rule_snapshot.contains_rule(candidate.rule_id):
            expected_general_id = build_general_rule_id(candidate.issue_type)
            return None if candidate.rule_id == expected_general_id else "UNKNOWN_RULE_ID"

        if candidate.rule_id not in rendered_rule_ids:
            return "RULE_NOT_SENT"

        applicable_ids = next(
            (binding.rule_ids for binding in file_rule_bindings if binding.path == candidate.file),
            (),
        )
        if candidate.rule_id not in applicable_ids:
            return "RULE_NOT_APPLICABLE_TO_FILE"
        return None


def _sort_key(finding: Finding) -> tuple[int, str, int, str]:
    """決定的な出力順序キーを返す."""
    return (
        -finding.severity.rank,
        finding.candidate.file.value,
        finding.candidate.line_range.start_line,
        finding.fingerprint,
    )


def _normalize_general_rule_id(
    candidate: CandidateFinding,
    rule_snapshot: RuleSnapshot,
) -> CandidateFinding:
    """一般指摘のルール ID を issue_type から決定的に補正する."""
    if rule_snapshot.contains_rule(candidate.rule_id) or not candidate.rule_id.startswith(
        "GENERAL."
    ):
        return candidate
    expected = build_general_rule_id(candidate.issue_type)
    return candidate if candidate.rule_id == expected else replace(candidate, rule_id=expected)
