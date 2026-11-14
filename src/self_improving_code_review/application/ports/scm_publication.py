"""SCM 公開とレビュー実行を調停層から分離するポート."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from self_improving_code_review.application.config.settings import AppConfig
from self_improving_code_review.application.ports.diff_provider import DiffSource
from self_improving_code_review.application.review.models import ReviewResult
from self_improving_code_review.application.scm.models import (
    PublicationPlan,
    PublicationResult,
)
from self_improving_code_review.domain.common.enums import ReviewMode
from self_improving_code_review.domain.scm.models import ScmChange

__all__ = ["PublicationPublisher", "ReviewExecutor"]


class ReviewExecutor(Protocol):
    """既存 M3 レビューパイプラインを SCM 調停から呼び出す."""

    def execute(
        self,
        repository: Path,
        config: AppConfig,
        diff_source: DiffSource,
        mode: ReviewMode,
        *,
        config_path: Path | None,
        execution_nonce: str | None,
    ) -> ReviewResult:
        """固定済み差分でレビューを実行する."""
        ...


class PublicationPublisher(Protocol):
    """冪等な SCM コメント公開処理."""

    def save_plans(
        self,
        change: ScmChange,
        plans: tuple[PublicationPlan, ...],
    ) -> None:
        """ドライランの計画を外部 API を呼ばずに保存する."""
        ...

    def block_plans(
        self,
        change: ScmChange,
        plans: tuple[PublicationPlan, ...],
    ) -> None:
        """機密情報検出で送信禁止となった計画を監査保存する."""
        ...

    def publish(
        self,
        change: ScmChange,
        plans: tuple[PublicationPlan, ...],
    ) -> tuple[PublicationResult, ...]:
        """計画を作成または更新し、外部コメント識別子を返す."""
        ...
