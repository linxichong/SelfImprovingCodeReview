"""SQLite バックアップを変更せずに検査するポート."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from self_improving_code_review.domain.common.values import ProjectId

__all__ = ["BackupInspection", "BackupInspector"]


@dataclass(frozen=True, slots=True)
class BackupInspection:
    """バックアップ検査の安全な判定値と件数を保持する."""

    checks: tuple[tuple[str, bool], ...]
    counts: tuple[tuple[str, int], ...]
    failure_codes: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        """全検査に合格し、失敗理由がない場合は真を返す."""
        return all(value for _name, value in self.checks) and not self.failure_codes


class BackupInspector(Protocol):
    """復元済み SQLite バックアップを読取り専用で検査する契約."""

    def inspect(self, database_path: Path, project_id: ProjectId) -> BackupInspection:
        """対象 DB の整合性、所属および保存済みハッシュを検査する."""
