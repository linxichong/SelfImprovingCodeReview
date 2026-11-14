"""LLM呼出の安全な使用量と状態を同一接続で保存する."""

from __future__ import annotations

import sqlite3

from self_improving_code_review.application.ports.unit_of_work import LlmCallRecord
from self_improving_code_review.domain.common.errors import (
    DatabaseError,
)
from self_improving_code_review.messages import Messages


class SqliteLlmCallRepository:
    """LLM 呼び出し測定値の SQLite 実装."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """LLM 呼び出しの状態と使用量を保存する接続を保持する."""
        self._connection = connection

    def save(self, record: LlmCallRecord) -> int:
        """LLM 呼び出し測定値を 1 件登録する."""
        cursor = self._connection.execute(
            """
            INSERT INTO llm_calls(
                run_id, bundle_sequence, request_hash, model, status,
                input_tokens, output_tokens, latency_ms, cost_usd, attempt,
                call_kind, turn_index
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(record.run_id),
                record.bundle_sequence,
                record.request_hash,
                record.model,
                record.status,
                record.input_tokens,
                record.output_tokens,
                record.latency_ms,
                str(record.cost_usd),
                record.attempt,
                record.call_kind,
                record.turn_index,
            ),
        )
        if cursor.lastrowid is None:
            message = Messages.LLM_CALL_ID_UNAVAILABLE
            raise DatabaseError(message)
        return int(cursor.lastrowid)
