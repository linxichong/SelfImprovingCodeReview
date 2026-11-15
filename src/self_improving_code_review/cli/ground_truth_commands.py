"""M2 正解データ集合の発行コマンド."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from self_improving_code_review.application.config.settings import load_config
from self_improving_code_review.application.evaluation.ground_truth import GroundTruthService
from self_improving_code_review.cli.output import write_document
from self_improving_code_review.cli.review_commands import (
    _validate_project_context,
    _within_repository,
)
from self_improving_code_review.domain.common.enums import GroundTruthStatus, Severity
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import (
    GroundTruthIssueId,
    GroundTruthSetId,
    NormalizedPath,
    ProjectId,
)
from self_improving_code_review.domain.evaluation.models import (
    GroundTruthCase,
    GroundTruthIssue,
    GroundTruthSet,
)
from self_improving_code_review.infrastructure.persistence import SqliteUnitOfWorkFactory
from self_improving_code_review.infrastructure.persistence.evaluation_store import EvaluationStore
from self_improving_code_review.infrastructure.system import SystemClock
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.authorization import AllowListActorAuthorizer

__all__ = ["execute_ground_truth_command"]

_MAX_DOCUMENT_BYTES = 5 * 1024 * 1024


class _IssueDocument(BaseModel):
    """正解問題 JSON の厳密な入力形式."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    change_ref: str = Field(min_length=1, max_length=200)
    file: str = Field(min_length=1, max_length=500)
    start_line: int | None = Field(default=None, ge=1)
    symbol: str | None = Field(default=None, max_length=300)
    issue_type: str = Field(min_length=1, max_length=200)
    rule_id: str | None = Field(default=None, max_length=200)
    severity: Severity
    evidence: str = Field(min_length=1, max_length=4000)
    origin: str = Field(min_length=1, max_length=200)
    created_by: str = Field(min_length=1, max_length=200)
    verified_by: str = Field(min_length=1, max_length=200)
    approved_by: str = Field(min_length=1, max_length=200)


class _CaseDocument(BaseModel):
    """問題なし変更も表現できる評価対象ケースの厳密な入力形式."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    change_ref: str = Field(min_length=1, max_length=200)
    created_by: str = Field(min_length=1, max_length=200)
    verified_by: str = Field(min_length=1, max_length=200)
    approved_by: str = Field(min_length=1, max_length=200)


class _GroundTruthDocument(BaseModel):
    """発行対象の正解データ集合 JSON."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=2)
    split: str
    version: int = Field(ge=1)
    cases: tuple[_CaseDocument, ...] = ()
    issues: tuple[_IssueDocument, ...] = Field(min_length=1)


def execute_ground_truth_command(arguments: Namespace) -> int:
    """承認済み正解問題を不変の集合として SQLite へ発行する."""
    repository = Path(arguments.repository).resolve()
    config = load_config(Path(arguments.config) if arguments.config else None)
    input_path = _within_repository(
        repository,
        Path(arguments.input),
        Messages.GROUND_TRUTH_INPUT_LABEL,
    )
    document = _load_document(input_path)
    _validate_document(document)
    set_id = GroundTruthSetId(document.split, document.version)
    issues = tuple(_to_issue(set_id, item) for item in document.issues)
    cases = _to_cases(document, issues)
    service = GroundTruthService(
        authorizer=AllowListActorAuthorizer(
            maintainers=config.memory.maintainers,
            is_production=config.memory.is_production,
        ),
        clock=SystemClock(),
    )
    published = service.publish(
        GroundTruthSet(
            set_id=set_id,
            set_hash="",
            status=GroundTruthStatus.DRAFT,
            issues=issues,
            cases=cases,
        ),
        actor_id=arguments.actor,
    )
    database_path = _within_repository(
        repository, config.database.path, Messages.DATABASE_PATH_LABEL
    )
    SqliteUnitOfWorkFactory(database_path)
    _validate_project_context(repository, config, database_path)
    project_id = ProjectId(config.project_id) if config.governance.enabled else None
    EvaluationStore(database_path, project_id).publish(published)
    population = service.measure_population(published.issues, published.change_refs)
    write_document(
        {
            "schema_version": 1,
            "set_id": str(published.set_id),
            "set_hash": published.set_hash,
            "status": published.status.value,
            "issue_count": population.issue_count,
            "case_count": len(published.cases),
            "clean_case_count": len(
                published.change_refs - frozenset(issue.change_ref for issue in published.issues)
            ),
            "change_ref_count": population.change_ref_count,
            "critical_issue_count": population.critical_issue_count,
            "critical_change_ref_count": population.critical_change_ref_count,
            "is_evaluation_ready": population.is_satisfied,
            "unmet_requirements": list(population.unmet_requirements),
        },
        None,
    )
    return 0


def _load_document(path: Path) -> _GroundTruthDocument:
    """サイズ制限付きで正解データ JSON を読み込む."""
    if not path.is_file():
        raise InvalidInputError(Messages.GROUND_TRUTH_DOCUMENT_NOT_FOUND)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise InvalidInputError(Messages.INVALID_GROUND_TRUTH_DOCUMENT) from exc
    if size > _MAX_DOCUMENT_BYTES:
        raise InvalidInputError(Messages.GROUND_TRUTH_DOCUMENT_TOO_LARGE)
    try:
        return _GroundTruthDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as exc:
        raise InvalidInputError(Messages.INVALID_GROUND_TRUTH_DOCUMENT) from exc


def _validate_document(document: _GroundTruthDocument) -> None:
    """連番の一意性と人間による分離承認を検証する."""
    sequences = tuple(issue.sequence for issue in document.issues)
    if len(set(sequences)) != len(sequences):
        raise InvalidInputError(Messages.DUPLICATE_GROUND_TRUTH_SEQUENCE)
    if any(
        issue.created_by.strip() == issue.verified_by.strip() or not issue.approved_by.strip()
        for issue in document.issues
    ):
        raise InvalidInputError(Messages.INVALID_GROUND_TRUTH_APPROVAL)
    if document.schema_version == 1:
        if document.cases:
            raise InvalidInputError(Messages.INVALID_GROUND_TRUTH_DOCUMENT)
        return
    if not document.cases:
        raise InvalidInputError(Messages.GROUND_TRUTH_CASES_REQUIRED)
    case_sequences = tuple(case.sequence for case in document.cases)
    case_refs = tuple(case.change_ref.strip() for case in document.cases)
    if len(set(case_sequences)) != len(case_sequences) or len(set(case_refs)) != len(case_refs):
        raise InvalidInputError(Messages.DUPLICATE_GROUND_TRUTH_CASE)
    if any(
        case.created_by.strip() == case.verified_by.strip() or not case.approved_by.strip()
        for case in document.cases
    ):
        raise InvalidInputError(Messages.INVALID_GROUND_TRUTH_CASE_APPROVAL)
    if any(issue.change_ref.strip() not in set(case_refs) for issue in document.issues):
        raise InvalidInputError(Messages.GROUND_TRUTH_ISSUE_CASE_MISSING)


def _to_issue(set_id: GroundTruthSetId, item: _IssueDocument) -> GroundTruthIssue:
    """検証済み JSON を正解問題へ変換する."""
    return GroundTruthIssue(
        issue_id=GroundTruthIssueId(set_id, item.sequence),
        change_ref=item.change_ref.strip(),
        file=NormalizedPath(item.file),
        start_line=item.start_line,
        symbol=item.symbol.strip() if item.symbol is not None else None,
        issue_type=item.issue_type.strip(),
        rule_id=item.rule_id.strip() if item.rule_id is not None else None,
        severity=item.severity,
        evidence=item.evidence.strip(),
        status=GroundTruthStatus.APPROVED,
        origin=item.origin.strip(),
        created_by=item.created_by.strip(),
        verified_by=item.verified_by.strip(),
        approved_by=item.approved_by.strip(),
    )


def _to_cases(
    document: _GroundTruthDocument,
    issues: tuple[GroundTruthIssue, ...],
) -> tuple[GroundTruthCase, ...]:
    """入力版に応じて明示ケースまたは旧形式から補完したケースを返す."""
    if document.cases:
        return tuple(
            GroundTruthCase(
                sequence=item.sequence,
                change_ref=item.change_ref.strip(),
                status=GroundTruthStatus.APPROVED,
                created_by=item.created_by.strip(),
                verified_by=item.verified_by.strip(),
                approved_by=item.approved_by.strip(),
            )
            for item in document.cases
        )
    first_issue_by_ref: dict[str, GroundTruthIssue] = {}
    for issue in issues:
        first_issue_by_ref.setdefault(issue.change_ref, issue)
    return tuple(
        GroundTruthCase(
            sequence=sequence,
            change_ref=change_ref,
            status=GroundTruthStatus.APPROVED,
            created_by=issue.created_by,
            verified_by=issue.verified_by,
            approved_by=issue.approved_by,
        )
        for sequence, (change_ref, issue) in enumerate(
            sorted(first_issue_by_ref.items()),
            start=1,
        )
    )
