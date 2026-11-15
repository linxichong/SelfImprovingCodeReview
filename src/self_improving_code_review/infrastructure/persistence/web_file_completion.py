"""記録済みの実行結果からファイルごとの完了状態を組み立てる."""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from self_improving_code_review.domain.common.enums import BundleStatus

FILE_PAGE_SIZE = 40


class FileCompletionStatus(StrEnum):
    """ファイルの集計済み完了状態。表示順は未完了を優先する."""

    BLOCKED = "blocked"
    FAILED = "failed"
    PARTIAL = "partial"
    PENDING = "pending"
    SKIPPED = "skipped"
    COMPLETED = "completed"
    FILTERED = "filtered"


@dataclass(frozen=True, slots=True)
class RecordedUnit:
    """ファイルに対応する処理単位の保存済み結果."""

    bundle_id: str
    status: BundleStatus
    error_code: str | None


@dataclass(frozen=True, slots=True)
class FileCompletion:
    """ファイルの選別、処理状況、採用された指摘件数."""

    path: str
    decision: str
    reason: str | None
    status: FileCompletionStatus
    units: tuple[RecordedUnit, ...]
    finding_count: int


@dataclass(frozen=True, slots=True)
class FileCompletionPage:
    """ファイルの一覧と全ページにわたる件数."""

    files: tuple[FileCompletion, ...]
    counts: dict[FileCompletionStatus, int]
    current: int
    last: int


def read_file_completion(
    connection: sqlite3.Connection, run_id: str, page: int
) -> FileCompletionPage:
    """同じ読み取りスナップショットで処理単位、除外、指摘を照合する."""
    units_by_path: dict[str, list[RecordedUnit]] = {}
    decisions: dict[str, tuple[str, str | None]] = {}
    for row in connection.execute(
        """
        SELECT f.path, f.decision, f.reason, b.bundle_id, b.status, b.error_code
        FROM bundle_files f
        JOIN bundles b ON b.run_id = f.run_id AND b.bundle_id = f.bundle_id
        WHERE f.run_id = ? ORDER BY f.path, b.sequence
        """,
        (run_id,),
    ):
        path = row["path"]
        decisions.setdefault(path, (row["decision"], row["reason"]))
        units_by_path.setdefault(path, []).append(
            RecordedUnit(row["bundle_id"], BundleStatus(row["status"]), row["error_code"])
        )
    for row in connection.execute(
        "SELECT path, decision, reason FROM filtered_files WHERE run_id = ?", (run_id,)
    ):
        decisions.setdefault(row["path"], (row["decision"], row["reason"]))
    finding_counts = dict(
        connection.execute(
            "SELECT path, COUNT(*) FROM findings WHERE run_id = ? AND is_main = 1 GROUP BY path",
            (run_id,),
        ).fetchall()
    )
    order = {status: index for index, status in enumerate(FileCompletionStatus)}
    entries = []
    for path, (decision, reason) in decisions.items():
        units = tuple(units_by_path.get(path, ()))
        entries.append(
            FileCompletion(
                path, decision, reason, _file_status(units), units, finding_counts.get(path, 0)
            )
        )
    entries.sort(key=lambda item: (order[item.status], item.path))
    counts = dict(Counter(item.status for item in entries))
    last = max(1, (len(entries) + FILE_PAGE_SIZE - 1) // FILE_PAGE_SIZE)
    current = min(max(1, page), last)
    start = (current - 1) * FILE_PAGE_SIZE
    return FileCompletionPage(tuple(entries[start : start + FILE_PAGE_SIZE]), counts, current, last)


def _file_status(units: tuple[RecordedUnit, ...]) -> FileCompletionStatus:
    """指摘の有無に依存せず、ファイルに属する全単位の状態から完了を決める."""
    if not units:
        return FileCompletionStatus.FILTERED
    statuses = {unit.status for unit in units}
    if statuses == {BundleStatus.SUCCESS}:
        return FileCompletionStatus.COMPLETED
    if statuses & {BundleStatus.SUCCESS, BundleStatus.PARTIAL_SUCCESS}:
        return FileCompletionStatus.PARTIAL
    if any(unit.error_code == "SECRET_DETECTED" for unit in units):
        return FileCompletionStatus.BLOCKED
    if BundleStatus.FAILED in statuses:
        return FileCompletionStatus.FAILED
    if BundleStatus.PENDING in statuses:
        return FileCompletionStatus.PENDING
    return FileCompletionStatus.SKIPPED
