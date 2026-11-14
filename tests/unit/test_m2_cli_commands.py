"""M2 CLI 的解析、表示、分岐を検証する。"""

from __future__ import annotations

import json
import re
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from self_improving_code_review.application.evaluation.run_evaluation import EvaluationRunner
from self_improving_code_review.application.feedback.models import FeedbackOutcome
from self_improving_code_review.application.memory.change_status import MemoryStatusChange
from self_improving_code_review.application.memory.models import ExtractionOutcome
from self_improving_code_review.application.ports.memory_persistence import FeedbackRunContext
from self_improving_code_review.cli import (
    adjudication_commands,
    evaluation_commands,
    feedback_commands,
    ground_truth_commands,
    memory_commands,
)
from self_improving_code_review.cli.main import main
from self_improving_code_review.domain.common.enums import (
    EvaluationArm,
    ExtractionStatus,
    GroundTruthStatus,
    MatchResult,
    MemoryCategory,
    MemoryPolarity,
    MemoryStatus,
    RunStatus,
    Severity,
)
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import (
    ExtractionId,
    FeedbackId,
    GroundTruthIssueId,
    GroundTruthSetId,
    MemoryId,
    MemoryVersion,
    NormalizedPath,
    ProjectId,
    RunId,
)
from self_improving_code_review.domain.evaluation.models import (
    GroundTruthCase,
    GroundTruthIssue,
    GroundTruthSet,
)
from self_improving_code_review.domain.memory.models import MemoryContent, MemoryRecord
from self_improving_code_review.domain.memory.scope import MemoryScope
from self_improving_code_review.infrastructure.persistence.evaluation_store import (
    AdjudicationOutcome,
    EvaluationStore,
    PendingAdjudication,
    PublishedEvaluationSet,
)
from self_improving_code_review.messages import Messages

NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
RUN = "12345678-1234-4234-9234-123456789abc"
MEMORY_ID = MemoryId("22345678-1234-4234-9234-123456789abc")


def test_feedback_finding_id_parser_accepts_and_rejects_formats() -> None:
    assert str(feedback_commands._parse_finding_id(RUN, "B002-F003")) == "B002-F003"
    with pytest.raises(InvalidInputError, match=re.escape(Messages.FINDING_ID_FORMAT_INVALID)):
        feedback_commands._parse_finding_id(RUN, "invalid")
    with pytest.raises(
        InvalidInputError, match=re.escape(Messages.FINDING_ID_SEQUENCE_NOT_NUMERIC)
    ):
        feedback_commands._parse_finding_id(RUN, "Bxx-Fyy")


def test_feedback_database_path_is_confined_to_repository(tmp_path: Path) -> None:
    config = SimpleNamespace(database=SimpleNamespace(path=Path("data/review.db")))
    assert (
        feedback_commands._database_path(tmp_path, config)
        == (tmp_path / "data/review.db").resolve()
    )
    outside = SimpleNamespace(database=SimpleNamespace(path=tmp_path.parent / "outside.db"))
    with pytest.raises(InvalidInputError, match="リポジトリ内"):
        feedback_commands._database_path(tmp_path, outside)


def test_feedback_extraction_context_for_finding_and_missed_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FeedbackRepository:
        def find_finding_context(self, _run_id: RunId, finding_id: str) -> str | None:
            return "validated finding" if finding_id == "B001-F001" else None

    finding_args = Namespace(run=RUN, feedback_command="finding", finding="B001-F001")
    assert feedback_commands._extraction_context(
        finding_args, tmp_path, "a" * 40, FeedbackRepository()
    ) == ("validated finding", "finding:B001-F001")
    finding_args.finding = "B001-F999"
    with pytest.raises(InvalidInputError):
        feedback_commands._extraction_context(
            finding_args, tmp_path, "a" * 40, FeedbackRepository()
        )

    class Reader:
        def __init__(self, repository: Path) -> None:
            pass

        def read_text(self, _revision: str, _path: object) -> str | None:
            return "one\ndef target():\nthree\nfour"

    monkeypatch.setattr(feedback_commands, "GitRevisionContentReader", Reader)
    missed = Namespace(
        run=RUN,
        feedback_command="missed-issue",
        file="src/app.py",
        start_line=None,
        symbol="target",
    )
    context, locator = feedback_commands._extraction_context(
        missed, tmp_path, "a" * 40, FeedbackRepository()
    )
    assert "2: def target():" in context
    assert locator == "src/app.py:target"
    missed.start_line = 3
    assert (
        "3: three"
        in feedback_commands._extraction_context(missed, tmp_path, "a" * 40, FeedbackRepository())[
            0
        ]
    )
    with pytest.raises(InvalidInputError):
        feedback_commands._extraction_context(missed, tmp_path, None, FeedbackRepository())

    class MissingReader(Reader):
        def read_text(self, _revision: str, _path: object) -> str | None:
            return None

    monkeypatch.setattr(feedback_commands, "GitRevisionContentReader", MissingReader)
    with pytest.raises(InvalidInputError):
        feedback_commands._extraction_context(missed, tmp_path, "a" * 40, FeedbackRepository())


@pytest.mark.case("UT-M2-021")
def test_feedback_outcome_document_contains_candidate_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents: list[dict[str, object]] = []
    monkeypatch.setattr(
        feedback_commands,
        "write_document",
        lambda document, _output: documents.append(document),
    )
    feedback_commands._write_outcome(
        FeedbackOutcome(
            FeedbackId("32345678-1234-4234-9234-123456789abc"),
            "recorded",
            extraction_id=ExtractionId("42345678-1234-4234-9234-123456789abc"),
            candidate_status=ExtractionStatus.SUCCESS,
            candidate_memory_id=MEMORY_ID,
            candidate_version=MemoryVersion(2),
            is_duplicate=False,
        )
    )
    assert documents[0]["candidate_status"] == "success"
    assert documents[0]["candidate_version"] == 2


def test_memory_scope_loader_accepts_yaml_and_rejects_invalid_files(tmp_path: Path) -> None:
    valid = tmp_path / "scope.yaml"
    valid.write_text("paths:\n  - ' src/** '\nlanguages:\n  - python\n", encoding="utf-8")
    scope = memory_commands._load_scope(str(valid))
    assert scope is not None
    assert scope.paths == ("src/**",)
    assert memory_commands._load_scope(None) is None

    for content in ("unknown: [x]", "paths: text", "paths: []"):
        invalid = tmp_path / f"invalid-{len(content)}.yaml"
        invalid.write_text(content, encoding="utf-8")
        with pytest.raises(InvalidInputError):
            memory_commands._load_scope(str(invalid))
    with pytest.raises(InvalidInputError):
        memory_commands._load_scope(str(tmp_path / "missing.yaml"))


@pytest.mark.case("UT-M2-021")
def test_memory_documents_list_show_and_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    record, content = _memory()
    documents: list[dict[str, object]] = []
    monkeypatch.setattr(
        memory_commands,
        "write_document",
        lambda document, _output: documents.append(document),
    )

    class Repository:
        def list_by_status(self, _project_id: ProjectId, _status: MemoryStatus) -> object:
            return ((record, content),)

        def find(self, memory_id: MemoryId) -> object:
            return (record, content) if memory_id == MEMORY_ID else None

    config = SimpleNamespace(project_id="project")
    assert (
        memory_commands._list_memories(
            Repository(), config, Namespace(project=None, status="active")
        )
        == 0
    )
    assert documents[-1]["memories"]
    assert memory_commands._show_memory(Repository(), Namespace(memory_id=str(MEMORY_ID))) == 0
    assert documents[-1]["scope"] == content.to_document()["scope"]
    with pytest.raises(InvalidInputError, match="見つかりません"):
        memory_commands._show_memory(
            Repository(), Namespace(memory_id="92345678-1234-4234-9234-123456789abc")
        )


def test_memory_database_path_and_group_validation(tmp_path: Path) -> None:
    assert memory_commands._group({"paths": [" src/** ", " "]}, "paths") == ("src/**",)
    with pytest.raises(InvalidInputError, match=re.escape(Messages.MEMORY_SCOPE_NOT_ARRAY)):
        memory_commands._group({"paths": "src/**"}, "paths")
    config = SimpleNamespace(database=SimpleNamespace(path=Path("review.db")))
    assert memory_commands._database_path(tmp_path, config) == (tmp_path / "review.db").resolve()
    outside = SimpleNamespace(database=SimpleNamespace(path=tmp_path.parent / "outside.db"))
    with pytest.raises(InvalidInputError):
        memory_commands._database_path(tmp_path, outside)


@pytest.mark.case("UT-M2-021")
def test_memory_change_document(monkeypatch: pytest.MonkeyPatch) -> None:
    documents: list[dict[str, object]] = []
    monkeypatch.setattr(
        memory_commands,
        "write_document",
        lambda document, _output: documents.append(document),
    )
    memory_commands._write_change(
        MemoryStatusChange(
            MEMORY_ID,
            MemoryVersion(2),
            MemoryStatus.CONFLICT,
            ("OPPOSITE_POLARITY",),
        )
    )
    assert documents[0]["conflict_reason_codes"] == ["OPPOSITE_POLARITY"]


@pytest.mark.case("UT-M2-021")
def test_evaluation_arm_parser_and_report_document() -> None:
    assert evaluation_commands._parse_arms("B,C,B") == (
        EvaluationArm.RULES,
        EvaluationArm.MEMORY,
    )
    with pytest.raises(InvalidInputError, match=re.escape(Messages.EVAL_ARMS_INVALID)):
        evaluation_commands._parse_arms("Z")
    with pytest.raises(InvalidInputError, match=re.escape(Messages.EVAL_ARMS_REQUIRED)):
        evaluation_commands._parse_arms("")

    report = EvaluationRunner().evaluate_arm(EvaluationArm.RULES, 1, main_findings=(), issues=())
    document = evaluation_commands._report_document(report)
    assert document["arm"] == "B"
    assert document["precision"] is None


@pytest.mark.case("IT-M2-017", "UT-M2-021")
@pytest.mark.parametrize(
    ("command", "status", "expected"),
    [
        ("approve", MemoryStatus.ACTIVE, 0),
        ("approve", MemoryStatus.CONFLICT, 2),
        ("reject", MemoryStatus.REJECTED, 0),
    ],
)
def test_execute_memory_governance_commands(
    command: str,
    status: MemoryStatus,
    expected: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(
        database=SimpleNamespace(path=Path("review.db")),
        memory=SimpleNamespace(maintainers=("reviewer",), is_production=False),
        governance=SimpleNamespace(memory_lifecycle_enabled=False),
        project_id="project",
    )
    change = MemoryStatusChange(MEMORY_ID, MemoryVersion(1), status)

    class UseCase:
        def __init__(self, **kwargs: object) -> None:
            pass

        def execute(self, *_args: object, **_kwargs: object) -> MemoryStatusChange:
            return change

    monkeypatch.setattr(memory_commands, "load_config", lambda _path: config)
    monkeypatch.setattr(memory_commands, "SqliteUnitOfWorkFactory", lambda _path: object())
    monkeypatch.setattr(
        memory_commands, "ShortTransactionMemoryRepository", lambda _factory: object()
    )
    monkeypatch.setattr(memory_commands, "ApproveMemory", UseCase)
    monkeypatch.setattr(memory_commands, "RejectMemory", UseCase)
    monkeypatch.setattr(memory_commands, "_write_change", lambda _value: None)
    arguments = Namespace(
        repository=str(tmp_path),
        config=None,
        memory_command=command,
        memory_id=str(MEMORY_ID),
        actor="reviewer",
        reason="reviewed",
        lock_version=1,
        scope_file=None,
    )

    assert memory_commands.execute_memory_command(arguments) == expected


def test_execute_memory_routes_list_and_show(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = SimpleNamespace(
        database=SimpleNamespace(path=Path("review.db")),
        memory=SimpleNamespace(maintainers=(), is_production=False),
        project_id="project",
    )
    monkeypatch.setattr(memory_commands, "load_config", lambda _path: config)
    monkeypatch.setattr(memory_commands, "SqliteUnitOfWorkFactory", lambda _path: object())
    monkeypatch.setattr(
        memory_commands, "ShortTransactionMemoryRepository", lambda _factory: object()
    )
    monkeypatch.setattr(memory_commands, "_list_memories", lambda *_args: 7)
    monkeypatch.setattr(memory_commands, "_show_memory", lambda *_args: 8)
    base = {"repository": str(tmp_path), "config": None}
    assert memory_commands.execute_memory_command(Namespace(**base, memory_command="list")) == 7
    assert memory_commands.execute_memory_command(Namespace(**base, memory_command="show")) == 8


@pytest.mark.case("IT-M2-017", "UT-M2-021")
@pytest.mark.parametrize(
    ("feedback_command", "status", "error_code", "lifecycle_enabled", "expected"),
    [
        ("finding", ExtractionStatus.SUCCESS, None, True, 0),
        ("missed-issue", ExtractionStatus.BLOCKED, "SECRET_DETECTED", False, 4),
        ("finding", ExtractionStatus.FAILED, "InvalidLlmOutputError", False, 3),
        ("finding", ExtractionStatus.FAILED, "LlmClientError", False, 2),
    ],
)
def test_execute_feedback_records_then_extracts_candidate(
    feedback_command: str,
    status: ExtractionStatus,
    error_code: str | None,
    lifecycle_enabled: bool,
    expected: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feedback_id = FeedbackId("32345678-1234-4234-9234-123456789abc")
    extraction_id = ExtractionId("42345678-1234-4234-9234-123456789abc")
    saved = FeedbackOutcome(feedback_id, "recorded")
    extracted = ExtractionOutcome(extraction_id, status, 1, safe_error_code=error_code)
    config = SimpleNamespace(
        database=SimpleNamespace(path=Path("review.db")),
        llm=SimpleNamespace(temperature=None),
        memory=SimpleNamespace(
            maintainers=(),
            is_production=False,
            prompt_name="memory_extraction_v1",
            extraction_max_output_tokens=1000,
        ),
        governance=SimpleNamespace(
            memory_lifecycle_enabled=lifecycle_enabled,
            revalidation_default_days=180,
        ),
    )

    class FeedbackRepository:
        def __init__(self, factory: object) -> None:
            pass

        def find_run_context(self, _run_id: RunId) -> FeedbackRunContext | None:
            return FeedbackRunContext(ProjectId("project"), "a" * 40)

    class RecordUseCase:
        def __init__(self, **kwargs: object) -> None:
            pass

        def execute(self, _request: object) -> FeedbackOutcome:
            return saved

    class ExtractUseCase:
        def __init__(self, **kwargs: object) -> None:
            pass

        def execute(self, _request: object) -> ExtractionOutcome:
            return extracted

    class Llm:
        def close(self) -> None:
            pass

    monkeypatch.setattr(feedback_commands, "load_config", lambda _path: config)
    monkeypatch.setattr(feedback_commands, "SqliteUnitOfWorkFactory", lambda _path: object())
    monkeypatch.setattr(feedback_commands, "ShortTransactionFeedbackRepository", FeedbackRepository)
    monkeypatch.setattr(
        feedback_commands, "ShortTransactionExtractionRepository", lambda _value: object()
    )
    monkeypatch.setattr(
        feedback_commands, "ShortTransactionMemoryRepository", lambda _value: object()
    )
    monkeypatch.setattr(feedback_commands, "RecordFindingFeedback", RecordUseCase)
    monkeypatch.setattr(feedback_commands, "RecordMissedIssue", RecordUseCase)
    monkeypatch.setattr(feedback_commands, "ExtractMemoryCandidate", ExtractUseCase)
    monkeypatch.setattr(
        feedback_commands, "_extraction_context", lambda *_args: ("context", "locator")
    )
    monkeypatch.setattr(feedback_commands, "_build_llm_client", lambda *_args: Llm())
    monkeypatch.setattr(feedback_commands, "load_prompt_template", lambda *_args: object())
    monkeypatch.setattr(
        feedback_commands, "ExtractionContextBuilder", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(feedback_commands, "_write_outcome", lambda _value: None)
    arguments = Namespace(
        repository=str(tmp_path),
        config=None,
        feedback_command=feedback_command,
        run=RUN,
        finding="B001-F001",
        label="false-positive",
        reason="reason",
        actor="reviewer",
        file="src/app.py",
        issue_type="null-dereference",
        severity="high",
        description="description",
        evidence="evidence",
        start_line=10,
        symbol=None,
    )

    assert feedback_commands.execute_feedback_command(arguments) == expected


def test_execute_feedback_rejects_missing_run_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = SimpleNamespace(
        database=SimpleNamespace(path=Path("review.db")),
        memory=SimpleNamespace(maintainers=(), is_production=False),
    )

    class Repository:
        def __init__(self, factory: object) -> None:
            pass

        def find_run_context(self, _run_id: RunId) -> None:
            return None

    class Recorder:
        def __init__(self, **kwargs: object) -> None:
            pass

        def execute(self, _request: object) -> FeedbackOutcome:
            return FeedbackOutcome(FeedbackId("32345678-1234-4234-9234-123456789abc"), "recorded")

    monkeypatch.setattr(feedback_commands, "load_config", lambda _path: config)
    monkeypatch.setattr(feedback_commands, "SqliteUnitOfWorkFactory", lambda _path: object())
    monkeypatch.setattr(feedback_commands, "ShortTransactionFeedbackRepository", Repository)
    monkeypatch.setattr(feedback_commands, "RecordFindingFeedback", Recorder)
    arguments = Namespace(
        repository=str(tmp_path),
        config=None,
        feedback_command="finding",
        run=RUN,
        finding="B001-F001",
        label="false-positive",
        reason="reason",
        actor="reviewer",
    )
    with pytest.raises(InvalidInputError, match="見つかりません"):
        feedback_commands.execute_feedback_command(arguments)


@pytest.mark.case("IT-M2-018")
def test_evaluate_once_aggregates_changes_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    config = evaluation_commands.load_config(Path("config/test.yaml"))
    calls: list[tuple[str, str]] = []

    def run_review(
        repository: Path,
        arm_config: object,
        source: object,
        mode: object,
        **kwargs: object,
    ) -> object:
        calls.append((source.commit, kwargs["execution_nonce"]))  # type: ignore[attr-defined]
        metrics = SimpleNamespace(
            input_tokens=10,
            output_tokens=2,
            latency_ms=3,
            cost_usd=__import__("decimal").Decimal("0.1"),
        )
        versions = SimpleNamespace(rule_snapshot_id="rule", memory_snapshot_id="memory")
        return SimpleNamespace(
            main_findings=(),
            metrics=metrics,
            run=SimpleNamespace(
                run_id=RunId(RUN),
                status=RunStatus.SUCCESS,
                versions=versions,
            ),
        )

    monkeypatch.setattr(evaluation_commands, "run_configured_review", run_review)
    issues = (_ground_truth_issue(1, "change-b"), _ground_truth_issue(2, "change-a"))

    report, rule_snapshot, memory_snapshot, config_hash, review_run_ids = (
        evaluation_commands._evaluate_once(
            EvaluationRunner(),
            EvaluationArm.MEMORY,
            2,
            Path(),
            config,
            Path("config/test.yaml"),
            issues,
            frozenset({"change-a", "change-b", "change-clean"}),
            set_id="test-v1",
        )
    )

    assert [item[0] for item in calls] == ["change-a", "change-b", "change-clean"]
    assert report.input_tokens == 30
    assert report.output_tokens == 6
    assert report.latency_ms == 9
    assert str(report.cost_usd) == "0.3"
    assert rule_snapshot == "rule"
    assert memory_snapshot == "memory"
    assert config_hash
    assert review_run_ids == (RUN, RUN, RUN)


@pytest.mark.case("IT-M3-010", "UT-M3-014")
@pytest.mark.parametrize(
    "status",
    [
        RunStatus.PENDING,
        RunStatus.RUNNING,
        RunStatus.PARTIAL_SUCCESS,
        RunStatus.FAILED,
        RunStatus.BLOCKED,
    ],
)
def test_evaluate_once_rejects_non_successful_review_run(
    status: RunStatus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = evaluation_commands.load_config(Path("config/test.yaml"))
    result = SimpleNamespace(
        run=SimpleNamespace(run_id=RunId(RUN), status=status),
    )
    monkeypatch.setattr(
        evaluation_commands, "run_configured_review", lambda *_args, **_kwargs: result
    )

    with pytest.raises(evaluation_commands._IncompleteReviewRunsError) as raised:
        evaluation_commands._evaluate_once(
            EvaluationRunner(),
            EvaluationArm.RULES,
            1,
            Path(),
            config,
            Path("config/test.yaml"),
            (_ground_truth_issue(1, "change-a"),),
            set_id="test-v1",
        )

    assert raised.value.run_id == RUN
    assert raised.value.status is status


@pytest.mark.case("IT-M2-017", "IT-M2-018", "UT-M2-021")
@pytest.mark.parametrize(
    ("satisfied", "arms", "expected"),
    [(False, "A", 1), (True, "A", 0), (True, "B,C", 2)],
)
def test_execute_evaluation_command_branches(
    satisfied: bool,
    arms: str,
    expected: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = evaluation_commands.load_config(Path("config/test.yaml"))
    published = PublishedEvaluationSet(
        GroundTruthSetId("test", 1), "set-hash", (_ground_truth_issue(1, "change-a"),)
    )
    published_with_cases = PublishedEvaluationSet(
        published.set_id,
        published.set_hash,
        published.issues,
        (GroundTruthCase(1, "change-a", GroundTruthStatus.PUBLISHED),),
    )
    assert published_with_cases.change_refs == frozenset({"change-a"})
    saved: list[object] = []
    documents: list[dict[str, object]] = []

    class Store:
        def __init__(self, _path: Path, _project_id: object = None) -> None:
            pass

        def load_published(self, _split: str, _version: int) -> PublishedEvaluationSet:
            return published

        def save_report(self, *args: object, **_kwargs: object) -> None:
            saved.append(args)

    class PopulationService:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def measure_population(self, _issues: object, _change_refs: object) -> object:
            return SimpleNamespace(
                is_satisfied=satisfied,
                unmet_requirements=("issue_count",) if not satisfied else (),
            )

    def evaluate_once(
        runner: EvaluationRunner,
        arm: EvaluationArm,
        repeat: int,
        *args: object,
        **kwargs: object,
    ) -> object:
        report = runner.evaluate_arm(arm, repeat, main_findings=(), issues=published.issues)
        return report, None, None, "config-hash", (RUN,)

    monkeypatch.setattr(evaluation_commands, "load_config", lambda _path: config)
    monkeypatch.setattr(evaluation_commands, "_within_repository", lambda *_args: tmp_path / "db")
    monkeypatch.setattr(evaluation_commands, "SqliteUnitOfWorkFactory", lambda _path: object())
    monkeypatch.setattr(evaluation_commands, "EvaluationStore", Store)
    monkeypatch.setattr(evaluation_commands, "GroundTruthService", PopulationService)
    monkeypatch.setattr(evaluation_commands, "_evaluate_once", evaluate_once)
    monkeypatch.setattr(
        evaluation_commands,
        "write_document",
        lambda document, _output: documents.append(document),
    )
    arguments = Namespace(
        repository=str(tmp_path),
        config=None,
        split="test",
        set_version=1,
        arms=arms,
        repeat=3,
    )

    assert evaluation_commands.execute_evaluation_command(arguments) == expected
    if satisfied:
        assert saved
        assert documents[-1]["reports"]
    else:
        assert documents[-1]["status"] == "insufficient-population"


@pytest.mark.case("IT-M3-010", "UT-M3-014")
def test_evaluation_failure_gate_does_not_save_staged_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = evaluation_commands.load_config(Path("config/test.yaml"))
    published = PublishedEvaluationSet(
        GroundTruthSetId("test", 1), "set-hash", (_ground_truth_issue(1, "change-a"),)
    )
    saved: list[object] = []
    documents: list[dict[str, object]] = []

    class Store:
        def __init__(self, _path: Path, _project_id: object = None) -> None:
            pass

        def load_published(self, _split: str, _version: int) -> PublishedEvaluationSet:
            return published

        def save_report(self, *args: object, **_kwargs: object) -> None:
            saved.append(args)

    class PopulationService:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def measure_population(self, _issues: object, _change_refs: object) -> object:
            return SimpleNamespace(is_satisfied=True, unmet_requirements=())

    call_count = 0

    def evaluate_once(
        runner: EvaluationRunner,
        arm: EvaluationArm,
        repeat: int,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise evaluation_commands._IncompleteReviewRunsError(RUN, RunStatus.FAILED)
        report = runner.evaluate_arm(arm, repeat, main_findings=(), issues=published.issues)
        return report, None, None, "config-hash", (RUN,)

    monkeypatch.setattr(evaluation_commands, "load_config", lambda _path: config)
    monkeypatch.setattr(evaluation_commands, "_within_repository", lambda *_args: tmp_path / "db")
    monkeypatch.setattr(evaluation_commands, "SqliteUnitOfWorkFactory", lambda _path: object())
    monkeypatch.setattr(evaluation_commands, "EvaluationStore", Store)
    monkeypatch.setattr(evaluation_commands, "GroundTruthService", PopulationService)
    monkeypatch.setattr(evaluation_commands, "_evaluate_once", evaluate_once)
    monkeypatch.setattr(
        evaluation_commands,
        "write_document",
        lambda document, _output: documents.append(document),
    )
    arguments = Namespace(
        repository=str(tmp_path),
        config=None,
        split="test",
        set_version=1,
        arms="B",
        repeat=3,
    )

    assert evaluation_commands.execute_evaluation_command(arguments) == 2
    assert saved == []
    assert documents == [
        {
            "schema_version": 1,
            "set_id": "test-v1",
            "set_hash": "set-hash",
            "status": "INCOMPLETE_REVIEW_RUNS",
            "incomplete_review_runs": [{"run_id": RUN, "status": "failed"}],
        }
    ]


def test_ground_truth_publish_command_uses_validated_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    config = repository / "test.yaml"
    config.write_text(
        "schema_version: 1\n"
        "project_id: evaluation-test\n"
        "database:\n  path: .review/test.db\n"
        "memory:\n  maintainers: [maintainer]\n",
        encoding="utf-8",
    )
    input_path = repository / "ground-truth.json"
    input_path.write_text(
        json.dumps(_ground_truth_document(), ensure_ascii=False),
        encoding="utf-8",
    )
    arguments = Namespace(
        repository=str(repository),
        config=str(config),
        input="ground-truth.json",
        actor="maintainer",
        ground_truth_command="publish",
    )

    assert (
        main(
            [
                "ground-truth",
                "publish",
                "--repository",
                arguments.repository,
                "--config",
                arguments.config,
                "--input",
                arguments.input,
                "--actor",
                arguments.actor,
            ]
        )
        == 0
    )

    output = json.loads(capsys.readouterr().out)
    assert output["set_id"] == "test-v1"
    assert output["issue_count"] == 2
    assert output["case_count"] == 2
    assert output["clean_case_count"] == 0
    assert not output["is_evaluation_ready"]
    loaded = EvaluationStore(repository / ".review/test.db").load_published("test", 1)
    assert loaded.set_hash == output["set_hash"]
    assert loaded.issues[0].verified_by == "reviewer"
    assert len(loaded.cases) == 2
    with pytest.raises(InvalidInputError, match="発行済み"):
        ground_truth_commands.execute_ground_truth_command(arguments)


def test_adjudication_commands_export_resolve_and_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents: list[tuple[dict[str, object], Path | None]] = []
    report = EvaluationRunner().evaluate_arm(
        EvaluationArm.RULES,
        1,
        main_findings=(),
        issues=(),
    )
    reports: dict[EvaluationArm, tuple[object, ...]] = {
        EvaluationArm.RULES: (report,),
        EvaluationArm.MEMORY: (report,),
    }

    class Store:
        def __init__(self, _path: Path, _project_id: object = None) -> None:
            pass

        def list_pending_adjudications(
            self, _split: str, _version: int
        ) -> tuple[PendingAdjudication, ...]:
            return (
                PendingAdjudication(
                    "blind-token",
                    {"file": "src/app.py"},
                    ({"issue_id": "test-v1-I001"},),
                ),
            )

        def resolve_adjudication(self, *_args: object, **_kwargs: object) -> AdjudicationOutcome:
            return AdjudicationOutcome("blind-token", MatchResult.FALSE_POSITIVE, 0)

        def load_reports(
            self, _split: str, _version: int
        ) -> dict[EvaluationArm, tuple[object, ...]]:
            return reports

    config = SimpleNamespace(
        database=SimpleNamespace(path=Path(".review/test.db")),
        memory=SimpleNamespace(maintainers=("reviewer",), is_production=False),
        governance=SimpleNamespace(enabled=False),
        project_id="default",
    )
    monkeypatch.setattr(adjudication_commands, "load_config", lambda _path: config)
    monkeypatch.setattr(
        adjudication_commands,
        "_within_repository",
        lambda repository, path, _label: repository / path,
    )
    monkeypatch.setattr(adjudication_commands, "SqliteUnitOfWorkFactory", lambda _path: object())
    monkeypatch.setattr(adjudication_commands, "EvaluationStore", Store)
    monkeypatch.setattr(
        adjudication_commands,
        "write_document",
        lambda document, output: documents.append((document, output)),
    )
    common = {
        "repository": str(tmp_path),
        "config": None,
        "split": "test",
        "set_version": 1,
    }

    assert (
        adjudication_commands.execute_adjudication_command(
            Namespace(**common, adjudication_command="export", output="pending.json")
        )
        == 0
    )
    assert documents[-1][0]["pending_count"] == 1
    assert documents[-1][1] == tmp_path / "pending.json"
    assert (
        adjudication_commands.execute_adjudication_command(
            Namespace(**common, adjudication_command="export", output=None)
        )
        == 0
    )
    assert documents[-1][1] is None

    assert (
        adjudication_commands.execute_adjudication_command(
            Namespace(
                **common,
                adjudication_command="resolve",
                token="blind-token",
                result=MatchResult.FALSE_POSITIVE.value,
                issue_id=None,
                actor="reviewer",
                reason="既知問題に一致しません。",
            )
        )
        == 0
    )
    assert documents[-1][0]["remaining_count"] == 0

    assert (
        adjudication_commands.execute_adjudication_command(
            Namespace(**common, adjudication_command="report")
        )
        == 2
    )
    assert documents[-1][0]["is_successful"] is False
    reports.pop(EvaluationArm.MEMORY)
    with pytest.raises(InvalidInputError, match=re.escape(Messages.ADJUDICATION_REPORTS_REQUIRED)):
        adjudication_commands.execute_adjudication_command(
            Namespace(**common, adjudication_command="report")
        )


def test_main_routes_adjudication_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "self_improving_code_review.cli.main.execute_adjudication_command",
        lambda _arguments: 7,
    )
    assert (
        main(
            [
                "adjudication",
                "export",
                "--split",
                "test",
                "--set-version",
                "1",
            ]
        )
        == 7
    )


def test_ground_truth_document_rejects_invalid_inputs(tmp_path: Path) -> None:
    with pytest.raises(InvalidInputError, match="存在しません"):
        ground_truth_commands._load_document(tmp_path / "missing.json")

    unreadable = Mock(spec=Path)
    unreadable.is_file.return_value = True
    unreadable.stat.side_effect = OSError("unreadable")
    with pytest.raises(InvalidInputError):
        ground_truth_commands._load_document(unreadable)

    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json", encoding="utf-8")
    with pytest.raises(InvalidInputError):
        ground_truth_commands._load_document(invalid)

    valid = ground_truth_commands._GroundTruthDocument.model_validate(_ground_truth_document())
    duplicate = valid.model_copy(update={"issues": (valid.issues[0], valid.issues[0])})
    with pytest.raises(InvalidInputError, match="sequence"):
        ground_truth_commands._validate_document(duplicate)
    invalid_approval = valid.model_copy(
        update={"issues": (valid.issues[0].model_copy(update={"verified_by": "author"}),)}
    )
    with pytest.raises(InvalidInputError, match="検証者"):
        ground_truth_commands._validate_document(invalid_approval)

    version_two = ground_truth_commands._GroundTruthDocument.model_validate(
        {
            **_ground_truth_document(),
            "schema_version": 2,
            "cases": [
                {
                    "sequence": 1,
                    "change_ref": "a" * 40,
                    "created_by": "author",
                    "verified_by": "reviewer",
                    "approved_by": "maintainer",
                },
                {
                    "sequence": 2,
                    "change_ref": "b" * 40,
                    "created_by": "author",
                    "verified_by": "reviewer",
                    "approved_by": "maintainer",
                },
                {
                    "sequence": 3,
                    "change_ref": "c" * 40,
                    "created_by": "author",
                    "verified_by": "reviewer",
                    "approved_by": "maintainer",
                },
            ],
        }
    )
    ground_truth_commands._validate_document(version_two)
    cases = ground_truth_commands._to_cases(
        version_two,
        tuple(
            ground_truth_commands._to_issue(GroundTruthSetId("test", 1), item)
            for item in version_two.issues
        ),
    )
    assert len(cases) == 3

    for changed, message in (
        ({"cases": ()}, "cases"),
        ({"cases": (version_two.cases[0], version_two.cases[0])}, "重複"),
        (
            {"cases": (version_two.cases[0].model_copy(update={"verified_by": "author"}),)},
            "検証者",
        ),
        (
            {"issues": (version_two.issues[1].model_copy(update={"change_ref": "d" * 40}),)},
            "ケース",
        ),
    ):
        with pytest.raises(InvalidInputError, match=message):
            ground_truth_commands._validate_document(version_two.model_copy(update=changed))

    legacy_with_cases = valid.model_copy(update={"cases": version_two.cases})
    with pytest.raises(InvalidInputError):
        ground_truth_commands._validate_document(legacy_with_cases)

    oversized = tmp_path / "oversized.json"
    oversized.write_text("x" * (5 * 1024 * 1024 + 1), encoding="utf-8")
    with pytest.raises(InvalidInputError):
        ground_truth_commands._load_document(oversized)

    store = EvaluationStore(tmp_path / "review.db")
    with pytest.raises(InvalidInputError, match=re.escape(Messages.GROUND_TRUTH_SET_NOT_PUBLISHED)):
        store.publish(
            GroundTruthSet(
                GroundTruthSetId("test", 1),
                "",
                GroundTruthStatus.DRAFT,
                (_ground_truth_issue(1, "a" * 40),),
            )
        )


def _ground_truth_issue(sequence: int, change_ref: str) -> GroundTruthIssue:
    set_id = GroundTruthSetId("test", 1)
    return GroundTruthIssue(
        GroundTruthIssueId(set_id, sequence),
        change_ref,
        NormalizedPath("src/app.py"),
        "null-dereference",
        Severity.CRITICAL,
        "value may be null",
        GroundTruthStatus.PUBLISHED,
        start_line=10 + sequence,
    )


def _ground_truth_document() -> dict[str, object]:
    """正解データ発行テスト用の入力文書を返す."""
    common = {
        "issue_type": "null-dereference",
        "severity": "critical",
        "evidence": "None の参照が発生します。",
        "origin": "manual-review",
        "created_by": "author",
        "verified_by": "reviewer",
        "approved_by": "maintainer",
    }
    return {
        "schema_version": 1,
        "split": "test",
        "version": 1,
        "issues": [
            {
                **common,
                "sequence": 1,
                "change_ref": "a" * 40,
                "file": "src/app.py",
                "start_line": 10,
                "rule_id": "PY.NULL",
            },
            {
                **common,
                "sequence": 2,
                "change_ref": "b" * 40,
                "file": "src/service.py",
                "symbol": "load_user",
            },
        ],
    }


def _memory() -> tuple[MemoryRecord, MemoryContent]:
    content = MemoryContent(
        MEMORY_ID,
        MemoryVersion(1),
        MemoryCategory.FRAMEWORK,
        MemoryScope(paths=("src/**",)),
        "Fact",
        "Instruction",
        MemoryPolarity.REINFORCE,
        0.8,
        "Evidence",
        "actor",
        NOW,
    )
    return (
        MemoryRecord(
            MEMORY_ID,
            ProjectId("project"),
            MemoryVersion(1),
            MemoryStatus.ACTIVE,
            2,
            NOW,
            NOW,
        ),
        content,
    )
