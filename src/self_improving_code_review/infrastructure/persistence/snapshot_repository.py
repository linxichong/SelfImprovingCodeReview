"""メモリスナップショットの SQLite リポジトリ.

スナップショットは作成時点の ``(memory_id, version, content_hash)`` を
固定参照する. 指摘には実際にプロンプトへ入れたメモリだけを関連付ける.
"""

from __future__ import annotations

import sqlite3

from self_improving_code_review.domain.common.values import MemoryId, MemoryVersion, RunId
from self_improving_code_review.domain.memory.models import MemorySnapshot

__all__ = ["SqliteMemorySnapshotRepository"]


class SqliteMemorySnapshotRepository:
    """メモリスナップショット永続化ポートの SQLite 実装."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """同一作業単位で使用する SQLite 接続を保持する."""
        self._connection = connection

    def save(self, snapshot: MemorySnapshot) -> None:
        """未登録の場合にスナップショットと各項目を登録する."""
        self._connection.execute(
            "INSERT OR IGNORE INTO memory_snapshots VALUES(?,?,?,?)",
            (
                snapshot.snapshot_id,
                str(snapshot.project_id),
                snapshot.snapshot_hash,
                snapshot.created_at.isoformat(),
            ),
        )
        self._connection.executemany(
            "INSERT OR IGNORE INTO memory_snapshot_items VALUES(?,?,?,?)",
            [
                (
                    snapshot.snapshot_id,
                    str(item.memory_id),
                    item.version.value,
                    item.content_hash,
                )
                for item in snapshot.items
            ],
        )

    def link_findings(
        self,
        snapshot: MemorySnapshot,
        run_id: RunId,
        finding_memory_ids: tuple[tuple[str, MemoryId, MemoryVersion], ...],
    ) -> None:
        """実際にプロンプトへ入れたメモリだけを指摘へ関連付ける."""
        fixed = {(str(item.memory_id), item.version.value) for item in snapshot.items}
        self._connection.executemany(
            "INSERT OR IGNORE INTO finding_memories VALUES(?,?,?,?,?)",
            [
                (
                    str(run_id),
                    finding_id,
                    snapshot.snapshot_id,
                    str(memory_id),
                    version.value,
                )
                for finding_id, memory_id, version in finding_memory_ids
                if (str(memory_id), version.value) in fixed
            ],
        )
