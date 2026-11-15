"""プロジェクト別の審査出力を同一の採点入力へ変換する."""

import json
from collections.abc import Mapping
from typing import Literal

from pydantic import TypeAdapter

from self_improving_code_review.infrastructure.benchmark.models import (
    Finding,
    Status,
    Tool,
    Trial,
    Usage,
)
from self_improving_code_review.messages import Messages

_OBJECT = TypeAdapter(dict[str, object])
_LIST = TypeAdapter(list[dict[str, object]])


def convert(tool: Tool, payload: str, exit_code: int, elapsed_ms: int) -> Trial:
    """レビュー出力を状態、使用量、正規化済み指摘へ変換する."""
    data = _OBJECT.validate_python(json.loads(payload), strict=True)
    if tool == "sicr":
        run = _OBJECT.validate_python(data.get("run"), strict=True)
        state = run.get("status")
        usage = _usage(data.get("metrics"), elapsed_ms)
        status: Status = "complete" if state == "success" else "failed"
        if state == "blocked":
            status = "blocked"
        elif state == "partial-success":
            status = "partial-success"
        source = data.get("findings")
    else:
        state = data.get("status")
        status = "complete" if state == "complete" else "failed"
        if not isinstance(state, str):
            raise ValueError(Messages.BENCH_INVALID)
        if tool == "codex" and state in {"timeout", "invalid"}:
            status = "timeout" if state == "timeout" else "invalid"
        usage = _usage(data.get("summary" if tool == "ocr" else "usage"), elapsed_ms)
        source = data.get("comments" if tool == "ocr" else "findings")
    if exit_code and status == "complete":
        status = "failed"
    findings = (
        tuple(_finding(tool, row) for row in _LIST.validate_python(source, strict=True))
        if status == "complete"
        else ()
    )
    return Trial(status=status, findings=findings, usage=usage)


def _usage(value: object, elapsed_ms: int) -> Usage:
    """欠測値をゼロと区別し、壁時計の時間を記録する."""
    data = _OBJECT.validate_python(value, strict=True)
    return Usage.model_validate(
        {**data, "latency_ms": elapsed_ms},
    )


def _finding(tool: Literal["sicr", "ocr", "codex"], data: Mapping[str, object]) -> Finding:
    """パス、閉区間、本文をツール固有の列から取り出す."""
    if tool == "ocr":
        note = data.get("content")
    else:
        title = data.get("title" if tool == "sicr" else "summary")
        description = data.get("description")
        note = "\n".join(
            x.strip() for x in (title, description) if isinstance(x, str) and x.strip()
        )
    return Finding.model_validate(
        {
            "path": data.get("path" if tool == "ocr" else "file"),
            "note": note,
            "from_line": data.get("start_line"),
            "to_line": data.get("end_line"),
        }
    )
