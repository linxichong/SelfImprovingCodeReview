"""固定ルールと採用指摘の関連を同一接続で保存する."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from self_improving_code_review.domain.finding.models import (
    Finding,
)
from self_improving_code_review.domain.rule.models import RuleSnapshot


class SqliteRuleSnapshotRepository:
    """ルールスナップショット参照の SQLite 実装."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """ルールスナップショットと指摘の関連を保存する接続を保持する."""
        self._connection = connection

    def save(self, snapshot: RuleSnapshot) -> None:
        """未登録の場合にスナップショットと各項目を登録する."""
        self._connection.execute(
            "INSERT OR IGNORE INTO rule_snapshots VALUES(?,?,?,?)",
            (
                snapshot.snapshot_id,
                snapshot.git_sha,
                snapshot.rule_set_hash,
                datetime.now(UTC).isoformat(),
            ),
        )
        self._connection.executemany(
            "INSERT OR IGNORE INTO rule_snapshot_items VALUES(?,?,?,?)",
            [
                (
                    snapshot.snapshot_id,
                    item.rule_id,
                    item.path,
                    item.content_hash,
                )
                for item in snapshot.items
            ],
        )

    def link_findings(self, snapshot: RuleSnapshot, findings: tuple[Finding, ...]) -> None:
        """指摘と、それが参照する固定済みルールを関連付ける."""
        rows = [
            (
                str(finding.bundle_id.run_id),
                str(finding.finding_id),
                snapshot.snapshot_id,
                finding.candidate.rule_id,
            )
            for finding in findings
            if snapshot.contains_rule(finding.candidate.rule_id)
        ]
        self._connection.executemany(
            "INSERT OR IGNORE INTO finding_rules VALUES(?,?,?,?)",
            rows,
        )
