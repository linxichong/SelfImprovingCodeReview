"""既存のレビュー・評価 JSON 出力を読み込む."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from self_improving_code_review.infrastructure.persistence.web_read_repository import Row

MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_REPORT_FILES = 150


class ReportDirectory:
    """指定ディレクトリの既知の JSON レポートを読み取り専用で参照する."""

    def __init__(self, directory: Path) -> None:
        """対象ディレクトリを起動時に固定する."""
        self.directory = directory.resolve()

    def entries(self) -> list[Row]:
        """認識できるレポートの一覧を新しい順で返す."""
        if not self.directory.is_dir():
            return []
        candidates = [
            (path.stat().st_mtime, path.name)
            for path in self.directory.glob("*.json")
            if not path.is_symlink() and path.is_file()
        ]
        reports = []
        for modified, name in sorted(candidates, reverse=True)[:MAX_REPORT_FILES]:
            report = self.get(name)
            if report is not None:
                reports.append(
                    {
                        "name": name,
                        "kind": report["kind"],
                        "modified": datetime.fromtimestamp(modified, UTC).isoformat(),
                        "label": report["label"],
                    }
                )
        return reports

    def get(self, name: str) -> Row | None:
        """指示された安全なファイル名の既知レポートを返す."""
        if not name or Path(name).name != name or not name.endswith(".json"):
            return None
        path = self.directory / name
        if path.is_symlink() or not path.is_file() or path.parent != self.directory:
            return None
        try:
            with path.open("rb") as handle:
                content = handle.read(MAX_REPORT_BYTES + 1)
            if len(content) > MAX_REPORT_BYTES:
                return None
            data = json.loads(content)
        except FileNotFoundError, UnicodeError, ValueError, RecursionError:
            return None
        return _classify(data)


def _classify(data: object) -> Row | None:
    """既存出力を構造によって識別する."""
    if not isinstance(data, dict):
        return None
    run = data.get("run")
    if (
        isinstance(run, dict)
        and isinstance(run.get("run_id"), str)
        and isinstance(data.get("findings"), list)
    ):
        return {"kind": "review", "label": run.get("project_id"), "data": data}
    if isinstance(data.get("set_id"), str) and isinstance(data.get("reports"), list):
        return {"kind": "evaluation", "label": data["set_id"], "data": data}
    rows = data.get("rows")
    if isinstance(rows, list) and rows:
        first = rows[0]
        if isinstance(first, dict) and isinstance(first.get("summary"), dict):
            summary = first["summary"]
            if {"tool", "instance", "reference_n", "repeat"} <= summary.keys():
                return {"kind": "benchmark", "label": "AACR-Bench", "data": data}
    return None
