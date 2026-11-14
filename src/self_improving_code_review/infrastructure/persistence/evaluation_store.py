"""M2 正解データと評価結果の SQLite 保存処理."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from self_improving_code_review.application.evaluation.run_evaluation import ArmReport
from self_improving_code_review.domain.common.enums import (
    EvaluationArm,
    GroundTruthStatus,
    MatchResult,
    Severity,
)
from self_improving_code_review.domain.common.errors import DatabaseError, InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.common.values import (
    EvaluationId,
    GroundTruthIssueId,
    GroundTruthSetId,
    NormalizedPath,
    ProjectId,
)
from self_improving_code_review.domain.evaluation.metrics import (
    MatchCounts,
    MetricValue,
    critical_recall,
    precision,
    recall,
)
from self_improving_code_review.domain.evaluation.models import (
    GroundTruthCase,
    GroundTruthIssue,
    GroundTruthSet,
)
from self_improving_code_review.messages import Messages

__all__ = [
    "AdjudicationOutcome",
    "EvaluationStore",
    "PendingAdjudication",
    "PublishedEvaluationSet",
]

_PROJECT_RESOURCE_NOT_FOUND = "NOT_FOUND"

_PENDING_SQL = (
    "SELECT em.adjudication_token, rr.source_revision, f.path, f.start_line, f.end_line, "
    "f.issue_type, "
    "f.title, f.description, f.evidence_quote, f.evidence_reason "
    "FROM evaluation_matches em "
    "JOIN evaluation_runs er ON er.evaluation_id=em.evaluation_id "
    "JOIN findings f ON f.run_id=em.run_id AND f.finding_id=em.finding_id "
    "JOIN review_runs rr ON rr.run_id=em.run_id "
    "WHERE er.set_id=? AND em.result=? ORDER BY em.adjudication_token"
)
_ISSUES_SQL = (
    "SELECT issue_id, change_ref, file, start_line, symbol, issue_type, rule_id, severity, "
    "evidence "
    "FROM ground_truth_issues WHERE set_id=? AND status=? ORDER BY issue_id"
)


@dataclass(frozen=True, slots=True)
class PublishedEvaluationSet:
    """評価で使用する発行済み正解データ集合."""

    set_id: GroundTruthSetId
    set_hash: str
    issues: tuple[GroundTruthIssue, ...]
    cases: tuple[GroundTruthCase, ...] = ()

    @property
    def change_refs(self) -> frozenset[str]:
        """問題なし変更を含む評価対象の変更参照を返す."""
        if self.cases:
            return frozenset(case.change_ref for case in self.cases)
        return frozenset(issue.change_ref for issue in self.issues)


@dataclass(frozen=True, slots=True)
class PendingAdjudication:
    """比較群を伏せた人工裁定対象."""

    token: str
    finding: dict[str, object]
    candidate_ground_truth_issues: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class AdjudicationOutcome:
    """人工裁定の保存結果."""

    token: str
    result: MatchResult
    remaining_count: int


class EvaluationStore:
    """発行済み正解データを読み、評価監査情報を保存する."""

    def __init__(self, database_path: Path, project_id: ProjectId | None = None) -> None:
        """SQLite データベースのパスと任意のプロジェクト境界を保持する."""
        self._database_path = database_path
        self._project_id = project_id

    def load_published(self, split: str, version: int) -> PublishedEvaluationSet:
        """指定した発行済み正解データ集合を返す."""
        set_id = GroundTruthSetId(split=split, version=version)
        with _database_connection(self._database_path) as connection:
            _require_ground_truth_membership(connection, set_id, self._project_id)
            row = connection.execute(
                "SELECT set_hash FROM ground_truth_sets "
                "WHERE set_id = ? AND split = ? AND version = ? AND status = 'published'",
                (str(set_id), split, version),
            ).fetchone()
            if row is None:
                raise InvalidInputError(Messages.PUBLISHED_GROUND_TRUTH_SET_NOT_FOUND)
            rows = connection.execute(
                "SELECT issue_id, change_ref, file, start_line, symbol, issue_type, rule_id, "
                "severity, evidence, status, origin, created_by, verified_by, approved_by "
                "FROM ground_truth_issues "
                "WHERE set_id = ? AND status = 'published' ORDER BY issue_id",
                (str(set_id),),
            ).fetchall()
            case_rows = connection.execute(
                "SELECT sequence, change_ref, status, created_by, verified_by, approved_by "
                "FROM ground_truth_cases WHERE set_id = ? AND status = 'published' "
                "ORDER BY sequence",
                (str(set_id),),
            ).fetchall()
        issues = tuple(_issue_from_row(set_id, item) for item in rows)
        cases = tuple(_case_from_row(item) for item in case_rows)
        return PublishedEvaluationSet(
            set_id=set_id,
            set_hash=str(row[0]),
            issues=issues,
            cases=cases,
        )

    def publish(self, published: GroundTruthSet) -> None:
        """発行済み正解データ集合を一つのトランザクションで保存する."""
        if (
            published.status is not GroundTruthStatus.PUBLISHED
            or published.published_at is None
            or published.approved_by is None
        ):
            raise InvalidInputError(Messages.GROUND_TRUTH_SET_NOT_PUBLISHED)
        with _database_connection(self._database_path) as connection:
            save_published_set(connection, published, self._project_id)
            connection.commit()

    def list_pending_adjudications(
        self,
        split: str,
        version: int,
    ) -> tuple[PendingAdjudication, ...]:
        """比較群情報を含めず、未裁定の照合結果を返す."""
        return _load_pending(
            self._database_path,
            GroundTruthSetId(split, version),
            self._project_id,
        )

    def resolve_adjudication(
        self,
        token: str,
        result: MatchResult,
        *,
        issue_id: str | None,
        actor: str,
        reason: str,
        adjudicated_at: datetime,
    ) -> AdjudicationOutcome:
        """盲化トークンに対応する未裁定照合を確定する."""
        return _resolve_adjudication(
            self._database_path,
            token,
            result,
            issue_id=issue_id,
            actor=actor,
            reason=reason,
            adjudicated_at=adjudicated_at,
            project_id=self._project_id,
        )

    def load_reports(
        self,
        split: str,
        version: int,
    ) -> dict[EvaluationArm, tuple[ArmReport, ...]]:
        """裁定反映後の比較群報告を DB から再構築する."""
        return _load_reports(
            self._database_path,
            GroundTruthSetId(split, version),
            self._project_id,
        )

    def save_report(
        self,
        evaluation_id: EvaluationId,
        set_id: GroundTruthSetId,
        report: ArmReport,
        *,
        config_hash: str,
        rule_snapshot_id: str | None,
        memory_snapshot_id: str | None,
        started_at: datetime,
        finished_at: datetime,
        review_run_ids: tuple[str, ...] = (),
    ) -> None:
        """比較群一回分の実行、照合、測定値を一つのトランザクションで保存する."""
        with _database_connection(self._database_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            _require_ground_truth_membership(connection, set_id, self._project_id)
            connection.execute(
                "INSERT INTO evaluation_runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    str(evaluation_id),
                    str(set_id),
                    report.arm.value,
                    report.repeat,
                    config_hash,
                    rule_snapshot_id,
                    memory_snapshot_id,
                    "pending" if report.counts.review_required else "success",
                    started_at.isoformat(),
                    finished_at.isoformat(),
                ),
            )
            connection.executemany(
                "INSERT INTO evaluation_review_runs(evaluation_id, run_id) VALUES(?,?)",
                [(str(evaluation_id), run_id) for run_id in review_run_ids],
            )
            connection.executemany(
                "INSERT INTO evaluation_matches("
                "evaluation_id, sequence, run_id, finding_id, set_id, issue_id, result, "
                "adjudicator, adjudication_token, adjudication_reason, adjudicated_at, "
                "is_superseded) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        str(evaluation_id),
                        sequence,
                        str(item.finding_id.bundle_id.run_id) if item.finding_id else None,
                        str(item.finding_id) if item.finding_id else None,
                        str(set_id) if item.issue_id else None,
                        str(item.issue_id) if item.issue_id else None,
                        item.result.value,
                        item.adjudicator,
                        (
                            hash_text(f"adjudication:{evaluation_id}:{sequence}")
                            if item.result is MatchResult.REVIEW_REQUIRED
                            else None
                        ),
                        None,
                        None,
                        0,
                    )
                    for sequence, item in enumerate(report.matches, start=1)
                ],
            )
            connection.executemany(
                "INSERT INTO evaluation_metrics VALUES(?,?,?,?,?,?)",
                [
                    (
                        str(evaluation_id),
                        name,
                        metric.numerator,
                        metric.denominator,
                        metric.value,
                        metric.status.value,
                    )
                    for name, metric in (
                        ("precision", report.precision),
                        ("recall", report.recall),
                        ("critical_recall", report.critical_recall),
                    )
                ]
                + [
                    (
                        str(evaluation_id),
                        "duplicate_count",
                        report.counts.duplicate,
                        1,
                        float(report.counts.duplicate),
                        "evaluated",
                    ),
                    (
                        str(evaluation_id),
                        "review_required_count",
                        report.counts.review_required,
                        1,
                        float(report.counts.review_required),
                        "evaluated",
                    ),
                    (
                        str(evaluation_id),
                        "input_tokens",
                        report.input_tokens,
                        1,
                        float(report.input_tokens),
                        "evaluated",
                    ),
                    (
                        str(evaluation_id),
                        "output_tokens",
                        report.output_tokens,
                        1,
                        float(report.output_tokens),
                        "evaluated",
                    ),
                    (
                        str(evaluation_id),
                        "latency_ms",
                        report.latency_ms,
                        1,
                        float(report.latency_ms),
                        "evaluated",
                    ),
                    (str(evaluation_id), "cost_usd", 0, 1, float(report.cost_usd), "evaluated"),
                ],
            )
            connection.commit()


def _require_ground_truth_membership(
    connection: sqlite3.Connection,
    set_id: GroundTruthSetId | str,
    project_id: ProjectId | None,
) -> None:
    """指定プロジェクトに所属する正解集合だけを参照可能にする."""
    if project_id is None:
        return
    row = connection.execute(
        "SELECT 1 FROM project_ground_truth_sets WHERE project_id = ? AND set_id = ?",
        (str(project_id), str(set_id)),
    ).fetchone()
    if row is None:
        raise InvalidInputError(_PROJECT_RESOURCE_NOT_FOUND)


@contextmanager
def _database_connection(database_path: Path) -> Iterator[sqlite3.Connection]:
    """SQLite 例外をプロジェクト境界の例外へ変換する接続を返す."""
    try:
        with closing(sqlite3.connect(database_path)) as connection:
            yield connection
    except sqlite3.Error as exc:
        raise DatabaseError(Messages.EVALUATION_DATABASE_FAILED) from exc


def _issue_from_row(set_id: GroundTruthSetId, row: tuple[object, ...]) -> GroundTruthIssue:
    """SQLite 行を正解データ問題へ変換する."""
    sequence = int(str(row[0]).rsplit("-I", maxsplit=1)[1])
    return GroundTruthIssue(
        issue_id=GroundTruthIssueId(set_id=set_id, sequence=sequence),
        change_ref=str(row[1]),
        file=NormalizedPath(str(row[2])),
        start_line=int(str(row[3])) if row[3] is not None else None,
        symbol=str(row[4]) if row[4] is not None else None,
        issue_type=str(row[5]),
        rule_id=str(row[6]) if row[6] is not None else None,
        severity=Severity(str(row[7])),
        evidence=str(row[8]),
        status=GroundTruthStatus(str(row[9])),
        origin=str(row[10]) if len(row) > 10 else "unknown",
        created_by=str(row[11]) if len(row) > 11 else "unknown",
        verified_by=str(row[12]) if len(row) > 12 and row[12] is not None else None,
        approved_by=str(row[13]) if len(row) > 13 and row[13] is not None else None,
    )


def _case_from_row(row: tuple[object, ...]) -> GroundTruthCase:
    """SQLite 行を評価対象ケースへ変換する."""
    return GroundTruthCase(
        sequence=int(str(row[0])),
        change_ref=str(row[1]),
        status=GroundTruthStatus(str(row[2])),
        created_by=str(row[3]),
        verified_by=str(row[4]) if row[4] is not None else None,
        approved_by=str(row[5]) if row[5] is not None else None,
    )


def _load_pending(
    database_path: Path,
    set_id: GroundTruthSetId,
    project_id: ProjectId | None,
) -> tuple[PendingAdjudication, ...]:
    """未裁定指摘と同一ファイルの正解候補を読み込む."""
    with _database_connection(database_path) as connection:
        _require_ground_truth_membership(connection, set_id, project_id)
        rows = _pending_rows(connection, set_id)
        issues = _published_issue_rows(connection, set_id)
    return tuple(_pending_from_row(row, issues) for row in rows)


def _pending_rows(
    connection: sqlite3.Connection, set_id: GroundTruthSetId
) -> list[tuple[object, ...]]:
    """未裁定指摘を比較群なしで取得する."""
    return connection.execute(
        _PENDING_SQL, (str(set_id), MatchResult.REVIEW_REQUIRED.value)
    ).fetchall()


def _published_issue_rows(
    connection: sqlite3.Connection, set_id: GroundTruthSetId
) -> list[tuple[object, ...]]:
    """裁定候補となる発行済み正解問題を取得する."""
    return connection.execute(
        _ISSUES_SQL, (str(set_id), GroundTruthStatus.PUBLISHED.value)
    ).fetchall()


def _pending_from_row(
    row: tuple[object, ...],
    issues: list[tuple[object, ...]],
) -> PendingAdjudication:
    """SQLite 行を盲化した裁定対象へ変換する."""
    change_ref = str(row[1])
    path = str(row[2])
    candidates = tuple(
        _candidate_document(issue)
        for issue in issues
        if str(issue[1]) == change_ref and str(issue[2]) == path
    )
    return PendingAdjudication(
        token=str(row[0]),
        finding={
            "file": path,
            "change_ref": change_ref,
            "start_line": int(str(row[3])),
            "end_line": int(str(row[4])),
            "issue_type": str(row[5]),
            "title": str(row[6]),
            "description": str(row[7]),
            "evidence_quote": str(row[8]),
            "evidence_reason": str(row[9]),
        },
        candidate_ground_truth_issues=candidates,
    )


def _candidate_document(issue: tuple[object, ...]) -> dict[str, object]:
    """正解問題候補を JSON 出力可能な文書へ変換する."""
    return {
        "issue_id": str(issue[0]),
        "change_ref": str(issue[1]),
        "file": str(issue[2]),
        "start_line": issue[3],
        "symbol": issue[4],
        "issue_type": str(issue[5]),
        "rule_id": issue[6],
        "severity": str(issue[7]),
        "evidence": str(issue[8]),
    }


def _resolve_adjudication(
    database_path: Path,
    token: str,
    result: MatchResult,
    *,
    issue_id: str | None,
    actor: str,
    reason: str,
    adjudicated_at: datetime,
    project_id: ProjectId | None,
) -> AdjudicationOutcome:
    """盲化トークンを使って人工裁定を保存する."""
    normalized_actor = actor.strip()
    normalized_reason = reason.strip()
    if not normalized_actor:
        raise InvalidInputError(Messages.ADJUDICATION_ACTOR_REQUIRED)
    if not normalized_reason:
        raise InvalidInputError(Messages.ADJUDICATION_REASON_REQUIRED)
    _validate_adjudication_issue(result, issue_id)
    with _database_connection(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        target = _find_adjudication_target(connection, token)
        if target is None:
            raise InvalidInputError(Messages.ADJUDICATION_ITEM_NOT_FOUND)
        evaluation_id, set_id = str(target[0]), str(target[1])
        _require_ground_truth_membership(connection, set_id, project_id)
        _validate_adjudication_issue_exists(connection, set_id, issue_id)
        _update_adjudication(
            connection,
            token,
            result,
            set_id=set_id,
            issue_id=issue_id,
            actor=normalized_actor,
            reason=normalized_reason,
            adjudicated_at=adjudicated_at,
        )
        if issue_id is not None:
            _supersede_false_negative(connection, evaluation_id, issue_id)
        _refresh_metrics(connection, evaluation_id)
        remaining = _remaining_adjudications(connection, set_id)
        connection.commit()
    return AdjudicationOutcome(token, result, remaining)


def _validate_adjudication_issue(result: MatchResult, issue_id: str | None) -> None:
    """裁定結果と正解問題指定の組合せを検証する."""
    if result in {MatchResult.TRUE_POSITIVE, MatchResult.DUPLICATE} and issue_id is None:
        raise InvalidInputError(Messages.ADJUDICATION_ISSUE_REQUIRED)
    if result is MatchResult.FALSE_POSITIVE and issue_id is not None:
        raise InvalidInputError(Messages.ADJUDICATION_ISSUE_FORBIDDEN)


def _find_adjudication_target(
    connection: sqlite3.Connection,
    token: str,
) -> tuple[object, ...] | None:
    """未裁定トークンに対応する評価と集合を返す."""
    row = connection.execute(
        "SELECT em.evaluation_id, er.set_id FROM evaluation_matches em "
        "JOIN evaluation_runs er ON er.evaluation_id=em.evaluation_id "
        "WHERE em.adjudication_token=? AND em.result=?",
        (token, MatchResult.REVIEW_REQUIRED.value),
    ).fetchone()
    return tuple(row) if row is not None else None


def _validate_adjudication_issue_exists(
    connection: sqlite3.Connection,
    set_id: str,
    issue_id: str | None,
) -> None:
    """指定された正解問題が同じ評価集合に属することを検証する."""
    if issue_id is None:
        return
    found = connection.execute(
        "SELECT 1 FROM ground_truth_issues WHERE set_id=? AND issue_id=?",
        (set_id, issue_id),
    ).fetchone()
    if found is None:
        raise InvalidInputError(Messages.ADJUDICATION_ISSUE_NOT_IN_SET)


def _update_adjudication(
    connection: sqlite3.Connection,
    token: str,
    result: MatchResult,
    *,
    set_id: str,
    issue_id: str | None,
    actor: str,
    reason: str,
    adjudicated_at: datetime,
) -> None:
    """裁定対象の有効結果と監査情報を更新する."""
    connection.execute(
        "UPDATE evaluation_matches SET result=?, set_id=?, issue_id=?, adjudicator=?, "
        "adjudication_reason=?, adjudicated_at=? WHERE adjudication_token=?",
        (
            result.value,
            set_id if issue_id is not None else None,
            issue_id,
            actor,
            reason,
            adjudicated_at.isoformat(),
            token,
        ),
    )


def _supersede_false_negative(
    connection: sqlite3.Connection,
    evaluation_id: str,
    issue_id: str,
) -> None:
    """人工的に検出済みとなった問題の旧偽陰性を集計対象外にする."""
    connection.execute(
        "UPDATE evaluation_matches SET is_superseded=1 "
        "WHERE evaluation_id=? AND issue_id=? AND result=?",
        (evaluation_id, issue_id, MatchResult.FALSE_NEGATIVE.value),
    )


def _remaining_adjudications(connection: sqlite3.Connection, set_id: str) -> int:
    """同じ正解集合に残る未裁定件数を返す."""
    row = connection.execute(
        "SELECT COUNT(*) FROM evaluation_matches em "
        "JOIN evaluation_runs er ON er.evaluation_id=em.evaluation_id "
        "WHERE er.set_id=? AND em.result=?",
        (set_id, MatchResult.REVIEW_REQUIRED.value),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _refresh_metrics(connection: sqlite3.Connection, evaluation_id: str) -> None:
    """裁定後の有効照合結果から主要測定値を再計算する."""
    counts = _effective_counts(connection, evaluation_id)
    critical_counts = _effective_critical_counts(connection, evaluation_id)
    for metric in (precision(counts), recall(counts), critical_recall(critical_counts)):
        _update_metric(connection, evaluation_id, metric)
    _update_count_metric(
        connection,
        evaluation_id,
        "duplicate_count",
        counts.duplicate,
    )
    _update_count_metric(
        connection,
        evaluation_id,
        "review_required_count",
        counts.review_required,
    )
    status = "pending" if counts.review_required else "success"
    connection.execute(
        "UPDATE evaluation_runs SET status=? WHERE evaluation_id=?",
        (status, evaluation_id),
    )


def _effective_counts(connection: sqlite3.Connection, evaluation_id: str) -> MatchCounts:
    """上書き済み偽陰性を除外した照合件数を返す."""
    rows = connection.execute(
        "SELECT result, COUNT(*) FROM evaluation_matches "
        "WHERE evaluation_id=? AND is_superseded=0 GROUP BY result",
        (evaluation_id,),
    ).fetchall()
    values = {str(row[0]): int(row[1]) for row in rows}
    duplicates = values.get(MatchResult.DUPLICATE.value, 0)
    return MatchCounts(
        true_positive=values.get(MatchResult.TRUE_POSITIVE.value, 0),
        false_positive=values.get(MatchResult.FALSE_POSITIVE.value, 0) + duplicates,
        false_negative=values.get(MatchResult.FALSE_NEGATIVE.value, 0),
        duplicate=duplicates,
        review_required=values.get(MatchResult.REVIEW_REQUIRED.value, 0),
    )


def _effective_critical_counts(
    connection: sqlite3.Connection,
    evaluation_id: str,
) -> MatchCounts:
    """重大問題に限定した有効照合件数を返す."""
    rows = connection.execute(
        "SELECT em.result, COUNT(*) FROM evaluation_matches em "
        "JOIN ground_truth_issues gi ON gi.set_id=em.set_id AND gi.issue_id=em.issue_id "
        "WHERE em.evaluation_id=? AND em.is_superseded=0 AND gi.severity=? "
        "GROUP BY em.result",
        (evaluation_id, Severity.CRITICAL.value),
    ).fetchall()
    values = {str(row[0]): int(row[1]) for row in rows}
    return MatchCounts(
        true_positive=values.get(MatchResult.TRUE_POSITIVE.value, 0),
        false_negative=values.get(MatchResult.FALSE_NEGATIVE.value, 0),
    )


def _update_metric(
    connection: sqlite3.Connection,
    evaluation_id: str,
    metric: MetricValue,
) -> None:
    """再計算した比率測定値を更新する."""
    connection.execute(
        "UPDATE evaluation_metrics SET numerator=?, denominator=?, value=?, status=? "
        "WHERE evaluation_id=? AND metric=?",
        (
            metric.numerator,
            metric.denominator,
            metric.value,
            metric.status.value,
            evaluation_id,
            metric.metric,
        ),
    )


def _update_count_metric(
    connection: sqlite3.Connection,
    evaluation_id: str,
    metric: str,
    value: int,
) -> None:
    """再計算した件数測定値を更新する."""
    connection.execute(
        "UPDATE evaluation_metrics SET numerator=?, denominator=1, value=?, status=? "
        "WHERE evaluation_id=? AND metric=?",
        (value, float(value), "evaluated", evaluation_id, metric),
    )


def _load_reports(
    database_path: Path,
    set_id: GroundTruthSetId,
    project_id: ProjectId | None,
) -> dict[EvaluationArm, tuple[ArmReport, ...]]:
    """正解集合に保存された全比較群報告を再構築する."""
    with _database_connection(database_path) as connection:
        _require_ground_truth_membership(connection, set_id, project_id)
        rows = connection.execute(
            "SELECT evaluation_id, arm, repeat_index FROM evaluation_runs "
            "WHERE set_id=? ORDER BY arm, repeat_index",
            (str(set_id),),
        ).fetchall()
        grouped: dict[EvaluationArm, list[ArmReport]] = {}
        for row in rows:
            arm = EvaluationArm(str(row[1]))
            grouped.setdefault(arm, []).append(
                _report_from_database(connection, str(row[0]), arm, int(row[2]))
            )
    return {arm: tuple(reports) for arm, reports in grouped.items()}


def _report_from_database(
    connection: sqlite3.Connection,
    evaluation_id: str,
    arm: EvaluationArm,
    repeat: int,
) -> ArmReport:
    """評価一回分の有効照合と使用量を報告へ変換する."""
    counts = _effective_counts(connection, evaluation_id)
    critical_counts = _effective_critical_counts(connection, evaluation_id)
    metrics = {
        str(row[0]): float(row[1])
        for row in connection.execute(
            "SELECT metric, value FROM evaluation_metrics WHERE evaluation_id=?",
            (evaluation_id,),
        ).fetchall()
        if row[1] is not None
    }
    return ArmReport(
        arm=arm,
        repeat=repeat,
        counts=counts,
        precision=precision(counts),
        recall=recall(counts),
        critical_recall=critical_recall(critical_counts),
        critical_issue_ids=_critical_issue_ids(connection, evaluation_id),
        input_tokens=int(metrics.get("input_tokens", 0)),
        output_tokens=int(metrics.get("output_tokens", 0)),
        latency_ms=int(metrics.get("latency_ms", 0)),
        cost_usd=Decimal(str(metrics.get("cost_usd", 0))),
    )


def _critical_issue_ids(
    connection: sqlite3.Connection,
    evaluation_id: str,
) -> frozenset[str]:
    """検出済みの重大正解問題 ID を返す."""
    rows = connection.execute(
        "SELECT DISTINCT em.issue_id FROM evaluation_matches em "
        "JOIN ground_truth_issues gi ON gi.set_id=em.set_id AND gi.issue_id=em.issue_id "
        "WHERE em.evaluation_id=? AND em.is_superseded=0 AND gi.severity=? "
        "AND em.result IN (?,?)",
        (
            evaluation_id,
            Severity.CRITICAL.value,
            MatchResult.TRUE_POSITIVE.value,
            MatchResult.DUPLICATE.value,
        ),
    ).fetchall()
    return frozenset(str(row[0]) for row in rows)


def save_published_set(
    connection: sqlite3.Connection,
    published: GroundTruthSet,
    project_id: ProjectId | None,
) -> None:
    """既存の発行保存を呼出元の短いトランザクション内で実行する."""
    if (
        published.status is not GroundTruthStatus.PUBLISHED
        or published.published_at is None
        or published.approved_by is None
    ):
        raise InvalidInputError(Messages.GROUND_TRUTH_SET_NOT_PUBLISHED)
    connection.execute("PRAGMA foreign_keys = ON")
    existing = connection.execute(
        "SELECT 1 FROM ground_truth_sets WHERE set_id = ?",
        (str(published.set_id),),
    ).fetchone()
    if existing is not None:
        _require_ground_truth_membership(
            connection,
            published.set_id,
            project_id,
        )
        raise InvalidInputError(Messages.GROUND_TRUTH_SET_ALREADY_EXISTS)
    connection.execute(
        "INSERT INTO ground_truth_sets VALUES(?,?,?,?,?,?,?)",
        (
            str(published.set_id),
            published.set_id.split,
            published.set_id.version,
            published.set_hash,
            published.status.value,
            published.approved_by,
            published.published_at.isoformat(),
        ),
    )
    connection.executemany(
        "INSERT INTO ground_truth_cases VALUES(?,?,?,?,?,?,?)",
        [
            (
                str(published.set_id),
                case.sequence,
                case.change_ref,
                case.created_by,
                case.verified_by,
                case.approved_by,
                case.status.value,
            )
            for case in published.cases
        ],
    )
    connection.executemany(
        "INSERT INTO ground_truth_issues VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                str(issue.issue_id),
                str(published.set_id),
                issue.change_ref,
                issue.file.value,
                issue.start_line,
                issue.symbol,
                issue.issue_type,
                issue.rule_id,
                issue.severity.value,
                issue.evidence,
                issue.origin,
                issue.created_by,
                issue.verified_by,
                issue.approved_by,
                issue.status.value,
            )
            for issue in published.issues
        ],
    )
    if project_id is not None:
        connection.execute(
            "INSERT INTO project_ground_truth_sets(project_id, set_id, adopted_at) VALUES(?, ?, ?)",
            (
                str(project_id),
                str(published.set_id),
                published.published_at.isoformat(),
            ),
        )
