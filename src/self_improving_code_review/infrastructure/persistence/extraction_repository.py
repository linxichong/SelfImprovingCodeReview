"""メモリ候補抽出実行の SQLite リポジトリ.

``RUNNING`` への取得は Compare-And-Swap で行い、同じ抽出実行を
複数プロセスが同時に実行しないようにする.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal

from self_improving_code_review.application.ports.memory_persistence import (
    ExtractionEvidence,
    ExtractionRecord,
    MemoryEvidenceLink,
)
from self_improving_code_review.domain.common.enums import ExtractionStatus
from self_improving_code_review.domain.common.values import (
    ExtractionId,
    MemoryId,
    MemoryVersion,
    ProjectId,
)
from self_improving_code_review.domain.memory.models import (
    MemoryContent,
    MemoryRecord,
    MemoryTransition,
)
from self_improving_code_review.infrastructure.persistence.memory_repository import (
    SqliteMemoryRepository,
)

__all__ = ["SqliteExtractionRepository"]

_COLUMNS = (
    "extraction_id, project_id, source_revision, idempotency_key, status, attempt, "
    "prompt_version, prompt_hash, model, request_hash, input_tokens, output_tokens, "
    "latency_ms, cost_usd, safe_error_code, candidate_memory_id, candidate_version, "
    "started_at, finished_at"
)
_RESUMABLE_STATUSES = (ExtractionStatus.PENDING.value, ExtractionStatus.FAILED.value)


class SqliteExtractionRepository:
    """抽出実行永続化ポートの SQLite 実装."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """同一作業単位で使用する SQLite 接続を保持する."""
        self._connection = connection

    def create(self, record: ExtractionRecord, evidence: tuple[ExtractionEvidence, ...]) -> None:
        """抽出意図と根拠集合を登録する."""
        self._connection.execute(
            # 列名はモジュール内の固定定数であり、値は全てプレースホルダーへ束縛する。
            f"INSERT INTO memory_extraction_runs({_COLUMNS}) "  # nosec B608
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _to_row(record),
        )
        self._connection.executemany(
            "INSERT INTO memory_extraction_feedback VALUES(?,?,?)",
            [
                (
                    str(record.extraction_id),
                    str(item.feedback_id),
                    item.feedback_content_hash,
                )
                for item in evidence
            ],
        )

    def find_by_idempotency_key(self, key: str) -> ExtractionRecord | None:
        """``key`` に一致する抽出実行を返す。存在しない場合は None を返す."""
        row = self._connection.execute(
            # 列名はモジュール内の固定定数であり、検索値はプレースホルダーへ束縛する。
            f"SELECT {_COLUMNS} FROM memory_extraction_runs WHERE idempotency_key = ?",  # nosec B608
            (key,),
        ).fetchone()
        return None if row is None else _row_to_extraction(row)

    def acquire(self, extraction_id: ExtractionId, attempt: int) -> bool:
        """``PENDING`` または ``FAILED`` を ``RUNNING`` へ取得する.

        Returns:
            取得できた場合は True。他プロセスが実行中の場合は False。
        """
        cursor = self._connection.execute(
            "UPDATE memory_extraction_runs SET status = ?, attempt = ?, finished_at = NULL "
            "WHERE extraction_id = ? AND status IN (?, ?)",
            (
                ExtractionStatus.RUNNING.value,
                attempt,
                str(extraction_id),
                *_RESUMABLE_STATUSES,
            ),
        )
        return cursor.rowcount == 1

    def finalize(self, record: ExtractionRecord) -> None:
        """抽出実行の最終状態、使用量、候補参照を記録する."""
        self._connection.execute(
            "UPDATE memory_extraction_runs SET status = ?, attempt = ?, request_hash = ?, "
            "input_tokens = ?, output_tokens = ?, latency_ms = ?, cost_usd = ?, "
            "safe_error_code = ?, candidate_memory_id = ?, candidate_version = ?, "
            "finished_at = ? WHERE extraction_id = ?",
            (
                record.status.value,
                record.attempt,
                record.request_hash,
                record.input_tokens,
                record.output_tokens,
                record.latency_ms,
                str(record.cost_usd),
                record.safe_error_code,
                str(record.candidate_memory_id) if record.candidate_memory_id is not None else None,
                record.candidate_version.value if record.candidate_version is not None else None,
                record.finished_at.isoformat() if record.finished_at is not None else None,
                str(record.extraction_id),
            ),
        )

    def finalize_success(
        self,
        extraction: ExtractionRecord,
        memory: MemoryRecord,
        content: MemoryContent,
        evidence: tuple[MemoryEvidenceLink, ...],
        transition: MemoryTransition,
    ) -> None:
        """候補一式を作成し、同じトランザクションで抽出を成功にする."""
        SqliteMemoryRepository(self._connection).create(memory, content, evidence, transition)
        self.finalize(extraction)


def _to_row(record: ExtractionRecord) -> tuple[object, ...]:
    """抽出実行を SQLite 行へ変換する."""
    return (
        str(record.extraction_id),
        str(record.project_id),
        record.source_revision,
        record.idempotency_key,
        record.status.value,
        record.attempt,
        record.prompt_version,
        record.prompt_hash,
        record.model,
        record.request_hash,
        record.input_tokens,
        record.output_tokens,
        record.latency_ms,
        str(record.cost_usd),
        record.safe_error_code,
        str(record.candidate_memory_id) if record.candidate_memory_id is not None else None,
        record.candidate_version.value if record.candidate_version is not None else None,
        record.started_at.isoformat(),
        record.finished_at.isoformat() if record.finished_at is not None else None,
    )


def _row_to_extraction(row: tuple[object, ...]) -> ExtractionRecord:
    """SQLite 行を抽出実行モデルへ復元する."""
    return ExtractionRecord(
        extraction_id=ExtractionId(str(row[0])),
        project_id=ProjectId(str(row[1])),
        source_revision=str(row[2]) if row[2] is not None else None,
        idempotency_key=str(row[3]),
        status=ExtractionStatus(str(row[4])),
        attempt=int(str(row[5])),
        prompt_version=str(row[6]),
        prompt_hash=str(row[7]),
        model=str(row[8]),
        request_hash=str(row[9]) if row[9] is not None else None,
        input_tokens=int(str(row[10])),
        output_tokens=int(str(row[11])),
        latency_ms=int(str(row[12])),
        cost_usd=Decimal(str(row[13])),
        safe_error_code=str(row[14]) if row[14] is not None else None,
        candidate_memory_id=MemoryId(str(row[15])) if row[15] is not None else None,
        candidate_version=MemoryVersion(int(str(row[16]))) if row[16] is not None else None,
        started_at=datetime.fromisoformat(str(row[17])),
        finished_at=datetime.fromisoformat(str(row[18])) if row[18] is not None else None,
    )
