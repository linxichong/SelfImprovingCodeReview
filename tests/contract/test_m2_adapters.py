"""M2 の Git、認可、識別子、評価保存アダプターを検証する。"""

from __future__ import annotations

import re
import sqlite3
import subprocess
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from self_improving_code_review.application.evaluation.run_evaluation import (
    ArmReport,
    EvaluationRunner,
)
from self_improving_code_review.domain.common.enums import (
    EvaluationArm,
    GroundTruthStatus,
    MatchResult,
    Permission,
    Severity,
)
from self_improving_code_review.domain.common.errors import (
    AuthorizationError,
    DatabaseError,
    GitCommandError,
    InvalidInputError,
)
from self_improving_code_review.domain.common.values import (
    BundleId,
    EvaluationId,
    ExtractionId,
    FeedbackId,
    FindingId,
    GroundTruthIssueId,
    GroundTruthSetId,
    MemoryId,
    MemoryVersion,
    NormalizedPath,
    RunId,
)
from self_improving_code_review.domain.evaluation.metrics import (
    MatchCounts,
    critical_recall,
    precision,
    recall,
)
from self_improving_code_review.domain.evaluation.models import MatchOutcome
from self_improving_code_review.infrastructure.git.revision_content_reader import (
    GitRevisionContentReader,
)
from self_improving_code_review.infrastructure.persistence.evaluation_store import EvaluationStore
from self_improving_code_review.infrastructure.persistence.sqlite import initialize_database
from self_improving_code_review.infrastructure.system import SystemClock, Uuid4IdGenerator
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.authorization import AllowListActorAuthorizer

NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)


def test_revision_reader_validates_revision_and_process_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader = GitRevisionContentReader(tmp_path)
    with pytest.raises(InvalidInputError, match=re.escape(Messages.FEEDBACK_REVISION_INVALID)):
        reader.read_text("HEAD", NormalizedPath("src/app.py"))

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=b"value = 1\n"),
    )
    assert reader.read_text("a" * 40, NormalizedPath("src/app.py")) == "value = 1\n"

    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=b"")
    )
    assert reader.read_text("a" * 40, NormalizedPath("missing.py")) is None


def test_revision_reader_translates_process_utf8_and_size_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader = GitRevisionContentReader(tmp_path)

    def fail(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired("git", 1)

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(GitCommandError):
        reader.read_text("a" * 40, NormalizedPath("src/app.py"))

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=b"x" * (2 * 1024 * 1024 + 1)
        ),
    )
    with pytest.raises(InvalidInputError, match=re.escape(Messages.FEEDBACK_SOURCE_TOO_LARGE)):
        reader.read_text("a" * 40, NormalizedPath("src/app.py"))

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=b"\xff"),
    )
    with pytest.raises(InvalidInputError, match="UTF-8"):
        reader.read_text("a" * 40, NormalizedPath("src/app.py"))


@pytest.mark.case("UT-M2-022", "UT-M4-008")
def test_authorizer_self_service_and_governance_rules() -> None:
    authorizer = AllowListActorAuthorizer(maintainers=("maintainer",))
    authorizer.require("user", Permission.RECORD_FEEDBACK)
    authorizer.require(" maintainer ", Permission.APPROVE_MEMORY)

    with pytest.raises(AuthorizationError, match=re.escape(Messages.ACTOR_ID_REQUIRED)):
        authorizer.require(" ", Permission.RECORD_FEEDBACK)
    with pytest.raises(AuthorizationError, match=Messages.ACTOR_NOT_MAINTAINER):
        authorizer.require("user", Permission.APPROVE_MEMORY)
    with pytest.raises(AuthorizationError, match="not enabled in production"):
        AllowListActorAuthorizer(("maintainer",), True).require(
            "maintainer", Permission.PUBLISH_GROUND_TRUTH
        )


def test_system_clock_and_all_uuid_generators() -> None:
    assert SystemClock().now_utc().tzinfo is not None
    generator = Uuid4IdGenerator()
    assert generator.new_run_id().value
    assert generator.new_feedback_id().value
    assert generator.new_extraction_id().value
    assert generator.new_memory_id().value
    assert generator.new_evaluation_id().value


@pytest.mark.parametrize(
    "factory",
    [
        lambda: FeedbackId("invalid"),
        lambda: ExtractionId("invalid"),
        lambda: MemoryId("invalid"),
        lambda: EvaluationId("invalid"),
        lambda: MemoryVersion(0),
        lambda: GroundTruthSetId("invalid", 1),
        lambda: GroundTruthSetId("test", 0),
        lambda: GroundTruthIssueId(GroundTruthSetId("test", 1), 0),
    ],
)
def test_m2_value_objects_reject_invalid_identifiers(factory: object) -> None:
    with pytest.raises(InvalidInputError):
        factory()  # type: ignore[operator]


@pytest.mark.case("IT-M2-019")
def test_evaluation_store_loads_published_set_and_saves_report(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    initialize_database(database)
    set_id = GroundTruthSetId("test", 1)
    issue_id = GroundTruthIssueId(set_id, 1)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO ground_truth_sets VALUES(?,?,?,?,?,?,?)",
            (str(set_id), "test", 1, "set-hash", "published", "reviewer", NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO ground_truth_issues VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(issue_id),
                str(set_id),
                "change-1",
                "src/app.py",
                10,
                None,
                "null-dereference",
                None,
                "critical",
                "value may be null",
                "manual",
                "reviewer",
                "reviewer",
                "reviewer",
                "published",
            ),
        )
        connection.executemany(
            "INSERT INTO ground_truth_issues VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "test-v1-I002",
                    str(set_id),
                    "change-2",
                    "src/app.py",
                    20,
                    None,
                    "other",
                    None,
                    "high",
                    "別の変更です。",
                    "manual",
                    "author",
                    "reviewer",
                    "maintainer",
                    "published",
                ),
                (
                    "test-v1-I003",
                    str(set_id),
                    "change-1",
                    "src/other.py",
                    30,
                    None,
                    "other",
                    None,
                    "high",
                    "別のファイルです。",
                    "manual",
                    "author",
                    "reviewer",
                    "maintainer",
                    "published",
                ),
            ],
        )
        connection.commit()
    store = EvaluationStore(database)
    published = store.load_published("test", 1)
    issue = published.issues[0]
    report = EvaluationRunner().evaluate_arm(
        EvaluationArm.RULES, 1, main_findings=(), issues=(issue,)
    )

    store.save_report(
        EvaluationId("12345678-1234-4234-9234-123456789abc"),
        set_id,
        report,
        config_hash="config",
        rule_snapshot_id=None,
        memory_snapshot_id=None,
        started_at=NOW,
        finished_at=NOW,
    )

    assert published.set_hash == "set-hash"
    assert issue.status is GroundTruthStatus.PUBLISHED
    assert issue.severity is Severity.CRITICAL
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM evaluation_matches").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM evaluation_metrics").fetchone()[0] == 9


def test_evaluation_store_reports_missing_set_and_database_errors(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    initialize_database(database)
    with pytest.raises(
        InvalidInputError, match=re.escape(Messages.PUBLISHED_GROUND_TRUTH_SET_NOT_FOUND)
    ):
        EvaluationStore(database).load_published("test", 1)
    broken = tmp_path / "broken.db"
    broken.write_text("invalid", encoding="utf-8")
    with pytest.raises(DatabaseError, match=re.escape(Messages.EVALUATION_DATABASE_FAILED)):
        EvaluationStore(broken).load_published("test", 1)


def test_evaluation_store_blind_adjudication_and_metric_refresh(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    initialize_database(database)
    set_id = GroundTruthSetId("test", 1)
    issue_id = GroundTruthIssueId(set_id, 1)
    run_id = RunId("12345678-1234-4234-9234-123456789abc")
    finding_id = FindingId(BundleId(run_id, 1), 1)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            """
            INSERT INTO review_runs(
                run_id, project_id, mode, status, source_revision, model,
                package_version, config_hash, prompt_version, prompt_hash,
                rule_snapshot_id, rule_set_hash, secret_engine_version,
                idempotency_key, started_at, finished_at, metrics_json,
                memory_snapshot_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(run_id),
                "project",
                "rules",
                "success",
                "change-1",
                "dataset/test",
                "1.0",
                "config",
                "prompt@1",
                "prompt-hash",
                "rule-snapshot",
                "rule-hash",
                "scanner",
                "idempotency",
                NOW.isoformat(),
                NOW.isoformat(),
                "{}",
                None,
            ),
        )
        connection.execute(
            "INSERT INTO ground_truth_sets VALUES(?,?,?,?,?,?,?)",
            (str(set_id), "test", 1, "set-hash", "published", "reviewer", NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO ground_truth_issues VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(issue_id),
                str(set_id),
                "change-1",
                "src/app.py",
                10,
                "load_user",
                "null-dereference",
                "PY.NULL",
                "critical",
                "value may be null",
                "manual",
                "author",
                "reviewer",
                "maintainer",
                "published",
            ),
        )
        connection.execute(
            "INSERT INTO findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(run_id),
                str(finding_id),
                str(finding_id.bundle_id),
                "src/app.py",
                10,
                11,
                "correctness",
                "null-dereference",
                "PY.NULL",
                "load_user",
                "critical",
                "None 参照",
                "値が None の場合に失敗します。",
                "value.name",
                "事前の None 検査がありません。",
                "None を検査します。",
                "fingerprint-1",
                "load_user",
                "valid",
                "valid",
                "reflection_skipped",
                "[]",
                1,
            ),
        )
        connection.commit()

    store = EvaluationStore(database)
    review_counts = MatchCounts(false_negative=1, review_required=1)
    report = ArmReport(
        EvaluationArm.RULES,
        1,
        review_counts,
        precision(review_counts),
        recall(review_counts),
        critical_recall(MatchCounts(false_negative=1)),
        frozenset(),
        input_tokens=12,
        output_tokens=3,
        latency_ms=45,
        matches=(
            MatchOutcome(MatchResult.REVIEW_REQUIRED, finding_id=finding_id),
            MatchOutcome(MatchResult.FALSE_NEGATIVE, issue_id=issue_id),
        ),
    )
    evaluation_id = EvaluationId("22345678-1234-4234-9234-123456789abc")
    store.save_report(
        evaluation_id,
        set_id,
        report,
        config_hash="config",
        rule_snapshot_id=None,
        memory_snapshot_id=None,
        started_at=NOW,
        finished_at=NOW,
        review_run_ids=(str(run_id),),
    )

    pending = store.list_pending_adjudications("test", 1)
    assert len(pending) == 1
    assert pending[0].finding["file"] == "src/app.py"
    assert pending[0].finding["change_ref"] == "change-1"
    assert len(pending[0].candidate_ground_truth_issues) == 1
    assert pending[0].candidate_ground_truth_issues[0]["issue_id"] == str(issue_id)
    token = pending[0].token
    invalid_inputs = (
        ("", "reason", MatchResult.TRUE_POSITIVE, str(issue_id)),
        ("reviewer", "", MatchResult.TRUE_POSITIVE, str(issue_id)),
        ("reviewer", "reason", MatchResult.TRUE_POSITIVE, None),
        ("reviewer", "reason", MatchResult.FALSE_POSITIVE, str(issue_id)),
    )
    for actor, reason, result, target_issue_id in invalid_inputs:
        with pytest.raises(InvalidInputError):
            store.resolve_adjudication(
                token,
                result,
                issue_id=target_issue_id,
                actor=actor,
                reason=reason,
                adjudicated_at=NOW,
            )
    with pytest.raises(InvalidInputError, match=re.escape(Messages.ADJUDICATION_ITEM_NOT_FOUND)):
        store.resolve_adjudication(
            "unknown",
            MatchResult.FALSE_POSITIVE,
            issue_id=None,
            actor="reviewer",
            reason="reason",
            adjudicated_at=NOW,
        )
    with pytest.raises(InvalidInputError, match=re.escape(Messages.ADJUDICATION_ISSUE_NOT_IN_SET)):
        store.resolve_adjudication(
            token,
            MatchResult.TRUE_POSITIVE,
            issue_id="test-v1-I999",
            actor="reviewer",
            reason="reason",
            adjudicated_at=NOW,
        )

    outcome = store.resolve_adjudication(
        token,
        MatchResult.TRUE_POSITIVE,
        issue_id=str(issue_id),
        actor=" reviewer ",
        reason=" matched evidence ",
        adjudicated_at=NOW,
    )
    assert outcome.remaining_count == 0
    loaded = store.load_reports("test", 1)[EvaluationArm.RULES][0]
    assert loaded.counts == MatchCounts(true_positive=1)
    assert loaded.critical_issue_ids == frozenset({str(issue_id)})
    assert loaded.input_tokens == 12
    assert loaded.cost_usd == 0
    with closing(sqlite3.connect(database)) as connection:
        adjudication = connection.execute(
            "SELECT adjudicator, adjudication_reason, adjudicated_at FROM evaluation_matches "
            "WHERE adjudication_token=?",
            (token,),
        ).fetchone()
        assert adjudication == ("reviewer", "matched evidence", NOW.isoformat())
        assert (
            connection.execute(
                "SELECT is_superseded FROM evaluation_matches WHERE result=?",
                (MatchResult.FALSE_NEGATIVE.value,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT status FROM evaluation_runs WHERE evaluation_id=?",
                (str(evaluation_id),),
            ).fetchone()[0]
            == "success"
        )
        assert connection.execute(
            "SELECT run_id FROM evaluation_review_runs WHERE evaluation_id=?",
            (str(evaluation_id),),
        ).fetchone()[0] == str(run_id)


def test_evaluation_store_resolves_false_positive_without_issue(tmp_path: Path) -> None:
    database = tmp_path / "review.db"
    initialize_database(database)
    set_id = GroundTruthSetId("test", 1)
    evaluation_id = EvaluationId("32345678-1234-4234-9234-123456789abc")
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO ground_truth_sets VALUES(?,?,?,?,?,?,?)",
            (str(set_id), "test", 1, "hash", "published", "reviewer", NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO evaluation_runs VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                str(evaluation_id),
                str(set_id),
                "C",
                1,
                "config",
                None,
                None,
                "pending",
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO evaluation_matches("
            "evaluation_id,sequence,run_id,finding_id,result,adjudication_token"
            ") VALUES(?,?,?,?,?,?)",
            (
                str(evaluation_id),
                1,
                "run",
                "finding",
                MatchResult.REVIEW_REQUIRED.value,
                "blind-token",
            ),
        )
        for metric in (
            "precision",
            "recall",
            "critical_recall",
            "duplicate_count",
            "review_required_count",
        ):
            connection.execute(
                "INSERT INTO evaluation_metrics VALUES(?,?,?,?,?,?)",
                (str(evaluation_id), metric, 0, 0, None, "not-evaluable"),
            )
        connection.commit()

    outcome = EvaluationStore(database).resolve_adjudication(
        "blind-token",
        MatchResult.FALSE_POSITIVE,
        issue_id=None,
        actor="reviewer",
        reason="既知問題に対応しません。",
        adjudicated_at=NOW,
    )
    assert outcome.result is MatchResult.FALSE_POSITIVE
    loaded = EvaluationStore(database).load_reports("test", 1)[EvaluationArm.MEMORY][0]
    assert loaded.counts == MatchCounts(false_positive=1)
