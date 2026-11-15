"""M5 の正解データと SCM 資源に対するプロジェクト分離を検証する。"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from self_improving_code_review.application.evaluation.run_evaluation import EvaluationRunner
from self_improving_code_review.domain.common.enums import (
    EvaluationArm,
    GroundTruthStatus,
    Severity,
)
from self_improving_code_review.domain.common.errors import DatabaseError, InvalidInputError
from self_improving_code_review.domain.common.values import (
    EvaluationId,
    GroundTruthIssueId,
    GroundTruthSetId,
    NormalizedPath,
    ProjectId,
)
from self_improving_code_review.domain.evaluation.models import (
    GroundTruthIssue,
    GroundTruthSet,
)
from self_improving_code_review.infrastructure.persistence.evaluation_store import EvaluationStore
from self_improving_code_review.infrastructure.persistence.sqlite import initialize_database
from self_improving_code_review.messages import Messages

_NOW = datetime(2026, 9, 29, tzinfo=UTC)
_PROJECT_A = ProjectId("project-a")
_PROJECT_B = ProjectId("project-b")


@pytest.mark.case("UT-M5-001")
def test_ground_truth_is_created_and_queried_within_project_boundary(
    tmp_path: Path,
) -> None:
    """正解集合と所属を原子的に作成し、別プロジェクトから隠す。"""
    database = tmp_path / "review.db"
    initialize_database(database)
    _insert_projects(database)
    ground_truth = _ground_truth_set(1)
    store_a = EvaluationStore(database, _PROJECT_A)

    store_a.publish(ground_truth)
    loaded = store_a.load_published("test", 1)
    report = EvaluationRunner().evaluate_arm(
        EvaluationArm.RULES,
        1,
        main_findings=(),
        issues=loaded.issues,
    )
    store_a.save_report(
        EvaluationId("12345678-1234-4234-9234-123456789abc"),
        ground_truth.set_id,
        report,
        config_hash="config",
        rule_snapshot_id=None,
        memory_snapshot_id=None,
        started_at=_NOW,
        finished_at=_NOW,
    )

    assert store_a.list_pending_adjudications("test", 1) == ()
    loaded_reports = store_a.load_reports("test", 1)[EvaluationArm.RULES]
    assert loaded_reports[0].counts == report.counts
    with closing(sqlite3.connect(database)) as connection:
        assignment = connection.execute(
            "SELECT project_id FROM project_ground_truth_sets WHERE set_id=?",
            (str(ground_truth.set_id),),
        ).fetchone()
    assert assignment == (str(_PROJECT_A),)

    store_b = EvaluationStore(database, _PROJECT_B)
    for operation in (
        lambda: store_b.load_published("test", 1),
        lambda: store_b.list_pending_adjudications("test", 1),
        lambda: store_b.load_reports("test", 1),
    ):
        with pytest.raises(InvalidInputError, match="NOT_FOUND"):
            operation()
    with pytest.raises(InvalidInputError, match="NOT_FOUND"):
        store_b.publish(ground_truth)
    with pytest.raises(
        InvalidInputError, match=re.escape(Messages.GROUND_TRUTH_SET_ALREADY_EXISTS)
    ):
        store_a.publish(ground_truth)


def test_ground_truth_assignment_failure_rolls_back_the_new_set(tmp_path: Path) -> None:
    """存在しないプロジェクトへの所属保存失敗時に集合本体も残さない。"""
    database = tmp_path / "review.db"
    initialize_database(database)
    ground_truth = _ground_truth_set(2)

    with pytest.raises(DatabaseError):
        EvaluationStore(database, ProjectId("missing-project")).publish(ground_truth)

    with closing(sqlite3.connect(database)) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM ground_truth_sets WHERE set_id=?",
            (str(ground_truth.set_id),),
        ).fetchone()
    assert count == (0,)


def _insert_projects(database: Path) -> None:
    """テスト用の二つの有効プロジェクトを保存する。"""
    with closing(sqlite3.connect(database)) as connection:
        connection.executemany(
            "INSERT INTO projects VALUES(?,?,?,?,?,?,?)",
            [
                (str(_PROJECT_A), "A", "policy-a", "active", 1, _NOW.isoformat(), _NOW.isoformat()),
                (str(_PROJECT_B), "B", "policy-b", "active", 1, _NOW.isoformat(), _NOW.isoformat()),
            ],
        )
        connection.commit()


def _ground_truth_set(version: int) -> GroundTruthSet:
    """発行可能な正解集合を返す。"""
    set_id = GroundTruthSetId("test", version)
    issue = GroundTruthIssue(
        issue_id=GroundTruthIssueId(set_id, 1),
        change_ref="change-1",
        file=NormalizedPath("src/app.py"),
        start_line=10,
        issue_type="null-dereference",
        severity=Severity.HIGH,
        evidence="None 検査がありません。",
        status=GroundTruthStatus.PUBLISHED,
        created_by="author",
        verified_by="reviewer",
        approved_by="approver",
    )
    return GroundTruthSet(
        set_id=set_id,
        set_hash=f"set-hash-{version}",
        status=GroundTruthStatus.PUBLISHED,
        issues=(issue,),
        approved_by="approver",
        published_at=_NOW,
    )
