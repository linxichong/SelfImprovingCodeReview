"""フィードバックの SQLite リポジトリ.

フィードバックは追記のみで管理する. 訂正は既存行を更新せず、
``supersedes_feedback_id`` を持つ新しい行で表現する.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from self_improving_code_review.application.ports.memory_persistence import FeedbackRunContext
from self_improving_code_review.domain.common.enums import FeedbackLabel, Severity
from self_improving_code_review.domain.common.values import (
    BundleId,
    FeedbackId,
    FindingId,
    NormalizedPath,
    ProjectId,
    RunId,
)
from self_improving_code_review.domain.feedback.models import FeedbackRecord, IssueLocation

__all__ = ["SqliteFeedbackRepository"]

_COLUMNS = (
    "feedback_id, run_id, finding_id, label, file, start_line, symbol, issue_type, "
    "severity, description, evidence, reason, actor_id, supersedes_feedback_id, "
    "idempotency_key, created_at"
)


class SqliteFeedbackRepository:
    """フィードバック永続化ポートの SQLite 実装."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """同一作業単位で使用する SQLite 接続を保持する."""
        self._connection = connection

    def save(self, record: FeedbackRecord) -> None:
        """フィードバックを 1 件登録する."""
        location = record.location
        self._connection.execute(
            # 列名はモジュール内の固定定数であり、値は全てプレースホルダーへ束縛する。
            f"INSERT INTO feedback({_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",  # nosec B608
            (
                str(record.feedback_id),
                str(record.run_id),
                str(record.finding_id) if record.finding_id is not None else None,
                record.label.value,
                location.file.value if location is not None else None,
                location.start_line if location is not None else None,
                location.symbol if location is not None else None,
                record.issue_type,
                record.severity.value if record.severity is not None else None,
                record.description,
                record.evidence,
                record.reason,
                record.actor_id,
                str(record.supersedes_feedback_id)
                if record.supersedes_feedback_id is not None
                else None,
                record.idempotency_key,
                record.created_at.isoformat(),
            ),
        )

    def find_by_idempotency_key(self, key: str) -> FeedbackRecord | None:
        """``key`` に一致するフィードバックを返す。存在しない場合は None を返す."""
        row = self._connection.execute(
            # 列名はモジュール内の固定定数であり、検索値はプレースホルダーへ束縛する。
            f"SELECT {_COLUMNS} FROM feedback WHERE idempotency_key = ?",  # nosec B608
            (key,),
        ).fetchone()
        return None if row is None else _row_to_feedback(row)

    def find_by_ids(self, feedback_ids: tuple[FeedbackId, ...]) -> tuple[FeedbackRecord, ...]:
        """指定した識別子のフィードバックを識別子順で返す."""
        if not feedback_ids:
            return ()
        placeholders = ",".join("?" for _ in feedback_ids)
        rows = self._connection.execute(
            # IN 句には件数分の疑問符だけを生成し、識別子は別引数へ束縛する。
            f"SELECT {_COLUMNS} FROM feedback WHERE feedback_id IN ({placeholders}) "  # nosec B608
            "ORDER BY feedback_id",
            tuple(str(item) for item in feedback_ids),
        ).fetchall()
        return tuple(_row_to_feedback(row) for row in rows)

    def exists_finding(self, run_id: RunId, finding_id: str) -> bool:
        """``run_id`` に ``finding_id`` が属する場合は True を返す."""
        row = self._connection.execute(
            "SELECT 1 FROM findings WHERE run_id = ? AND finding_id = ?",
            (str(run_id), finding_id),
        ).fetchone()
        return row is not None

    def find_run_context(self, run_id: RunId) -> FeedbackRunContext | None:
        """対象実行のプロジェクトとソース改訂を返す."""
        row = self._connection.execute(
            "SELECT project_id, source_revision FROM review_runs WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
        if row is None:
            return None
        return FeedbackRunContext(
            project_id=ProjectId(str(row[0])),
            source_revision=str(row[1]) if row[1] is not None else None,
            is_test_evaluation=self._is_test_evaluation(run_id),
        )

    def _is_test_evaluation(self, run_id: RunId) -> bool:
        """実行が Test 正解データの評価に使用された場合は True を返す."""
        row = self._connection.execute(
            "SELECT 1 FROM evaluation_review_runs evr "
            "JOIN evaluation_runs er ON er.evaluation_id=evr.evaluation_id "
            "JOIN ground_truth_sets gs ON gs.set_id=er.set_id "
            "WHERE evr.run_id=? AND gs.split=? LIMIT 1",
            (str(run_id), "test"),
        ).fetchone()
        return row is not None

    def find_finding_context(self, run_id: RunId, finding_id: str) -> str | None:
        """検証済み指摘から候補抽出に必要な項目だけを返す."""
        row = self._connection.execute(
            "SELECT path, start_line, end_line, category, issue_type, severity, title, "
            "description, evidence_quote, evidence_reason FROM findings "
            "WHERE run_id = ? AND finding_id = ?",
            (str(run_id), finding_id),
        ).fetchone()
        if row is None:
            return None
        labels = (
            "file",
            "start_line",
            "end_line",
            "category",
            "issue_type",
            "severity",
            "title",
            "description",
            "evidence_quote",
            "evidence_reason",
        )
        return "\n".join(f"{label}: {value}" for label, value in zip(labels, row, strict=True))


def _row_to_feedback(row: tuple[object, ...]) -> FeedbackRecord:
    """SQLite 行をフィードバックモデルへ復元する."""
    location: IssueLocation | None = None
    if row[4] is not None:
        location = IssueLocation(
            file=NormalizedPath(str(row[4])),
            start_line=int(str(row[5])) if row[5] is not None else None,
            symbol=str(row[6]) if row[6] is not None else None,
        )
    return FeedbackRecord(
        feedback_id=FeedbackId(str(row[0])),
        run_id=RunId(str(row[1])),
        label=FeedbackLabel(str(row[3])),
        reason=str(row[11]),
        actor_id=str(row[12]),
        created_at=datetime.fromisoformat(str(row[15])),
        idempotency_key=str(row[14]),
        finding_id=_parse_finding_id(row[1], row[2]),
        location=location,
        issue_type=str(row[7]) if row[7] is not None else None,
        severity=Severity(str(row[8])) if row[8] is not None else None,
        description=str(row[9]) if row[9] is not None else None,
        evidence=str(row[10]) if row[10] is not None else None,
        supersedes_feedback_id=FeedbackId(str(row[13])) if row[13] is not None else None,
    )


def _parse_finding_id(raw_run_id: object, raw_finding_id: object) -> FindingId | None:
    """``B001-F002`` 形式の保存値を指摘識別子へ復元する."""
    if raw_finding_id is None:
        return None
    bundle_part, _, finding_part = str(raw_finding_id).partition("-F")
    return FindingId(
        bundle_id=BundleId(
            run_id=RunId(str(raw_run_id)),
            sequence=int(bundle_part.removeprefix("B")),
        ),
        sequence=int(finding_part),
    )
