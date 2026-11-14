"""検証済み指摘を既存の行形式で保存する."""

from __future__ import annotations

import json
import sqlite3

from self_improving_code_review.domain.common.values import (
    RunId,
)
from self_improving_code_review.domain.finding.models import (
    Finding,
)


class SqliteFindingRepository:
    """指摘永続化の SQLite 実装."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """検証済み指摘を保存する SQLite 接続を保持する."""
        self._connection = connection

    def save_all(self, run_id: RunId, findings: tuple[Finding, ...]) -> None:
        """検証済みの指摘を登録する."""
        self._connection.executemany(
            """
            INSERT INTO findings VALUES(
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            [_finding_row(run_id, finding) for finding in findings],
        )


def _finding_row(run_id: RunId, finding: Finding) -> tuple[object, ...]:
    """1件の指摘を SQLite 行として返す."""
    candidate = finding.candidate
    validation = finding.validation
    return (
        str(run_id),
        str(finding.finding_id),
        str(finding.bundle_id),
        candidate.file.value,
        candidate.line_range.start_line,
        candidate.line_range.end_line,
        candidate.category,
        candidate.issue_type,
        candidate.rule_id,
        candidate.primary_entity,
        candidate.severity.value,
        candidate.title,
        candidate.description,
        candidate.evidence.quote,
        candidate.evidence.reason,
        candidate.suggestion,
        finding.fingerprint,
        finding.enclosing_symbol,
        validation.position_status.value,
        validation.evidence_status.value,
        validation.reflection_status.value,
        json.dumps(validation.reason_codes),
        int(finding.is_main),
    )
