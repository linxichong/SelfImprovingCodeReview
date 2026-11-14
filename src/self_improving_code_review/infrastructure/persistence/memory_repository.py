"""メモリの SQLite リポジトリ.

内容は ``(memory_id, version)`` で不変化する. 状態変更は楽観ロックで
保護し、同じバージョンからの二重更新を一方だけ成功させる.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from self_improving_code_review.application.ports.memory_persistence import MemoryEvidenceLink
from self_improving_code_review.domain.common.enums import (
    MemoryCategory,
    MemoryPolarity,
    MemoryStatus,
)
from self_improving_code_review.domain.common.errors import (
    ConcurrencyConflictError,
    DatabaseError,
)
from self_improving_code_review.domain.common.values import (
    MemoryId,
    MemoryVersion,
    ProjectId,
)
from self_improving_code_review.domain.memory.models import (
    MemoryContent,
    MemoryRecord,
    MemoryTransition,
)
from self_improving_code_review.domain.memory.scope import MemoryScope
from self_improving_code_review.messages import Messages

__all__ = ["SqliteMemoryRepository"]

_VERSION_COLUMNS = (
    "memory_id, version, category, scope_json, fact, review_instruction, polarity, "
    "confidence, evidence_summary, content_hash, created_by, created_at, "
    "valid_from, review_after, framework_version_constraints_json"
)
_VERSION_PLACEHOLDERS = ",".join("?" for _index in range(15))
_MEMORY_COLUMNS = (
    "memory_id, project_id, current_version, current_status, lock_version, created_at, updated_at"
)
_MEMORY_SELECT_COLUMNS = (
    # 列名はモジュール内の固定定数だけを連結し、外部入力を SQL へ埋め込まない。
    f"{_MEMORY_COLUMNS}, (SELECT MAX(created_at) FROM memory_transitions "  # nosec B608
    "WHERE memory_transitions.memory_id = memories.memory_id "
    "AND to_status = 'active') AS confirmed_at"
)


class SqliteMemoryRepository:
    """メモリ永続化ポートの SQLite 実装."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """同一作業単位で使用する SQLite 接続を保持する."""
        self._connection = connection

    def create(
        self,
        record: MemoryRecord,
        content: MemoryContent,
        evidence: tuple[MemoryEvidenceLink, ...],
        transition: MemoryTransition,
    ) -> None:
        """論理メモリとバージョン 1 を同一トランザクションで登録する."""
        self._connection.execute(
            # 列名と疑問符はモジュール内の固定定数であり、値は別引数へ束縛する。
            f"INSERT INTO memory_versions({_VERSION_COLUMNS}) VALUES({_VERSION_PLACEHOLDERS})",  # nosec B608
            _version_row(content),
        )
        self._connection.execute(
            # 列名はモジュール内の固定定数であり、値は全てプレースホルダーへ束縛する。
            f"INSERT INTO memories({_MEMORY_COLUMNS}) VALUES(?,?,?,?,?,?,?)",  # nosec B608
            (
                str(record.memory_id),
                str(record.project_id),
                record.current_version.value,
                record.current_status.value,
                record.lock_version,
                record.created_at.isoformat(),
                record.updated_at.isoformat(),
            ),
        )
        self._connection.executemany(
            "INSERT INTO memory_evidence VALUES(?,?,?,?,?,?)",
            [
                (
                    str(content.memory_id),
                    content.version.value,
                    str(item.feedback_id),
                    None,
                    None,
                    item.relation,
                )
                for item in evidence
            ],
        )
        self._insert_transition(transition)

    def add_version(
        self,
        memory_id: MemoryId,
        content: MemoryContent,
        expected_lock_version: int,
    ) -> None:
        """現在バージョンを上書きせず新しいバージョンを追加する.

        Raises:
            ConcurrencyConflictError: 想定するロックバージョンと一致しない場合.
        """
        self._connection.execute(
            # 列名と疑問符はモジュール内の固定定数であり、値は別引数へ束縛する。
            f"INSERT INTO memory_versions({_VERSION_COLUMNS}) VALUES({_VERSION_PLACEHOLDERS})",  # nosec B608
            _version_row(content),
        )
        cursor = self._connection.execute(
            "UPDATE memories SET current_version = ?, lock_version = lock_version + 1, "
            "updated_at = ? WHERE memory_id = ? AND lock_version = ?",
            (
                content.version.value,
                content.created_at.isoformat(),
                str(memory_id),
                expected_lock_version,
            ),
        )
        if cursor.rowcount != 1:
            message = Messages.memory_modified_concurrently(str(memory_id))
            raise ConcurrencyConflictError(message)

    def change_status(
        self,
        transition: MemoryTransition,
        expected_lock_version: int,
    ) -> None:
        """状態を遷移させ、監査記録を残す.

        Raises:
            ConcurrencyConflictError: 想定するロックバージョンと一致しない場合.
        """
        cursor = self._connection.execute(
            "UPDATE memories SET current_status = ?, lock_version = lock_version + 1, "
            "updated_at = ? WHERE memory_id = ? AND lock_version = ?",
            (
                transition.to_status.value,
                transition.created_at.isoformat(),
                str(transition.memory_id),
                expected_lock_version,
            ),
        )
        if cursor.rowcount != 1:
            message = Messages.memory_modified_concurrently(str(transition.memory_id))
            raise ConcurrencyConflictError(message)
        self._insert_transition(transition)

    def add_version_and_change_status(
        self,
        memory_id: MemoryId,
        content: MemoryContent,
        transition: MemoryTransition,
        expected_lock_version: int,
    ) -> None:
        """新しい内容バージョンと状態遷移を一つのトランザクションで反映する."""
        self.add_version(memory_id, content, expected_lock_version)
        self.change_status(transition, expected_lock_version + 1)

    def find(self, memory_id: MemoryId) -> tuple[MemoryRecord, MemoryContent] | None:
        """メモリと現在バージョンの内容を返す。存在しない場合は None を返す."""
        row = self._connection.execute(
            # 列名はモジュール内の固定定数であり、検索値はプレースホルダーへ束縛する。
            f"SELECT {_MEMORY_SELECT_COLUMNS} FROM memories WHERE memory_id = ?",  # nosec B608
            (str(memory_id),),
        ).fetchone()
        if row is None:
            return None
        record = _row_to_memory(row)
        return record, self._require_version(record.memory_id, record.current_version)

    def list_by_status(
        self,
        project_id: ProjectId,
        status: MemoryStatus,
    ) -> tuple[tuple[MemoryRecord, MemoryContent], ...]:
        """状態が一致するメモリを識別子順で返す."""
        rows = self._connection.execute(
            # 列名はモジュール内の固定定数であり、検索値はプレースホルダーへ束縛する。
            f"SELECT {_MEMORY_SELECT_COLUMNS} FROM memories "  # nosec B608
            "WHERE project_id = ? AND current_status = ? ORDER BY memory_id",
            (str(project_id), status.value),
        ).fetchall()
        records = tuple(_row_to_memory(row) for row in rows)
        return tuple(
            (record, self._require_version(record.memory_id, record.current_version))
            for record in records
        )

    def _insert_transition(self, transition: MemoryTransition) -> None:
        """状態遷移の監査記録を 1 件登録する."""
        self._connection.execute(
            "INSERT INTO memory_transitions("
            "memory_id, memory_version, from_status, to_status, actor_id, reason, created_at"
            ") VALUES(?,?,?,?,?,?,?)",
            (
                str(transition.memory_id),
                transition.memory_version.value,
                transition.from_status.value if transition.from_status is not None else None,
                transition.to_status.value,
                transition.actor_id,
                transition.reason,
                transition.created_at.isoformat(),
            ),
        )

    def _require_version(self, memory_id: MemoryId, version: MemoryVersion) -> MemoryContent:
        """指定バージョンの内容を読み込み、内容ハッシュを再検証する.

        Raises:
            DatabaseError: バージョンが存在しない、または内容ハッシュが
                保存値と一致しない場合.
        """
        row = self._connection.execute(
            # 列名はモジュール内の固定定数であり、検索値はプレースホルダーへ束縛する。
            f"SELECT {_VERSION_COLUMNS} FROM memory_versions WHERE memory_id = ? AND version = ?",  # nosec B608
            (str(memory_id), version.value),
        ).fetchone()
        if row is None:
            message = Messages.memory_version_not_found(str(memory_id), str(version))
            raise DatabaseError(message)
        content = _row_to_content(row)
        if content.content_hash != str(row[9]):
            message = Messages.memory_content_hash_mismatch(str(memory_id), str(version))
            raise DatabaseError(message)
        return content


def _version_row(content: MemoryContent) -> tuple[object, ...]:
    """メモリ内容を SQLite 行へ変換する."""
    document = content.to_document()
    return (
        str(content.memory_id),
        content.version.value,
        content.category.value,
        json.dumps(document["scope"], sort_keys=True, separators=(",", ":")),
        content.fact,
        content.review_instruction,
        content.polarity.value,
        content.confidence,
        content.evidence_summary,
        content.content_hash,
        content.created_by,
        content.created_at.isoformat(),
        content.valid_from.isoformat() if content.valid_from is not None else None,
        content.review_after.isoformat() if content.review_after is not None else None,
        json.dumps(content.framework_version_constraints, separators=(",", ":")),
    )


def _row_to_memory(row: tuple[object, ...]) -> MemoryRecord:
    """SQLite 行を論理メモリへ復元する."""
    return MemoryRecord(
        memory_id=MemoryId(str(row[0])),
        project_id=ProjectId(str(row[1])),
        current_version=MemoryVersion(int(str(row[2]))),
        current_status=MemoryStatus(str(row[3])),
        lock_version=int(str(row[4])),
        created_at=datetime.fromisoformat(str(row[5])),
        updated_at=datetime.fromisoformat(str(row[6])),
        confirmed_at=datetime.fromisoformat(str(row[7])) if row[7] is not None else None,
    )


def _row_to_content(row: tuple[object, ...]) -> MemoryContent:
    """SQLite 行をメモリ内容へ復元する."""
    scope_document = json.loads(str(row[3]))
    return MemoryContent(
        memory_id=MemoryId(str(row[0])),
        version=MemoryVersion(int(str(row[1]))),
        category=MemoryCategory(str(row[2])),
        scope=MemoryScope(
            languages=tuple(scope_document.get("languages", ())),
            frameworks=tuple(scope_document.get("frameworks", ())),
            paths=tuple(scope_document.get("paths", ())),
            symbols=tuple(scope_document.get("symbols", ())),
            keywords=tuple(scope_document.get("keywords", ())),
            risk_tags=tuple(scope_document.get("risk_tags", ())),
        ),
        fact=str(row[4]),
        review_instruction=str(row[5]),
        polarity=MemoryPolarity(str(row[6])),
        confidence=float(str(row[7])),
        evidence_summary=str(row[8]),
        created_by=str(row[10]),
        created_at=datetime.fromisoformat(str(row[11])),
        valid_from=datetime.fromisoformat(str(row[12])) if row[12] is not None else None,
        review_after=datetime.fromisoformat(str(row[13])) if row[13] is not None else None,
        framework_version_constraints=tuple(str(item) for item in json.loads(str(row[14]))),
    )
