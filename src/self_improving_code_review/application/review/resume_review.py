"""固定入力に基づくレビュー実行の厳格な再開判定."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from self_improving_code_review.application.ports.context_source import (
    ContextSourceFactory,
    SourceFingerprint,
)
from self_improving_code_review.application.review.models import BundleOutcome, ReviewResult
from self_improving_code_review.domain.common.enums import (
    BundleStatus,
    DiffSourceKind,
    RunStatus,
)
from self_improving_code_review.domain.common.errors import GitCommandError, InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_lines, hash_text

__all__ = [
    "BundleResumeIdentity",
    "ResumeIdentity",
    "ResumeReview",
    "StoredResumeIdentity",
]

_RETRYABLE_ERROR_CODES = {"LlmClientError", "InvalidLlmOutputError", "DatabaseError"}


@dataclass(frozen=True, slots=True)
class BundleResumeIdentity:
    """レビュー単位の固定入力と保存結果の同一性情報."""

    sequence: int
    input_hash: str
    reviewer_type: str
    result_hash: str | None = None
    checkpoint_result_hash: str | None = None


@dataclass(frozen=True, slots=True)
class ResumeIdentity:
    """現在入力から決定的に作成した再開条件."""

    pipeline_config_hash: str
    diff_hash: str
    plan_hash: str
    bundles: tuple[BundleResumeIdentity, ...]
    requires_memory_snapshot: bool


@dataclass(frozen=True, slots=True)
class StoredResumeIdentity:
    """データベースから復元した過去実行の再開条件."""

    pipeline_config_hash: str | None
    diff_hash: str | None
    plan_hash: str | None
    context_source_hash: str | None
    bundles: tuple[BundleResumeIdentity, ...]
    source_files: tuple[SourceFingerprint, ...]


class ResumeReview:
    """保存済み実行が固定入力と完全一致する場合だけ再開を許可する."""

    def __init__(self, context_source_factory: ContextSourceFactory | None) -> None:
        """追加ソースの再検証に使用する入力元生成処理を保持する."""
        self._context_source_factory = context_source_factory

    def can_resume(
        self,
        result: ReviewResult,
        stored: StoredResumeIdentity | None,
        expected: ResumeIdentity,
        *,
        repository: Path,
        source_revision: str | None,
        source_kind: DiffSourceKind,
    ) -> bool:
        """状態、ハッシュ、完了結果、追加ソースが一致する場合は True を返す."""
        if (
            result.run.status not in {RunStatus.PARTIAL_SUCCESS, RunStatus.FAILED}
            or not _has_only_retryable_failures(result)
            or stored is None
        ):
            return False
        if (
            stored.pipeline_config_hash != expected.pipeline_config_hash
            or stored.diff_hash != expected.diff_hash
            or stored.plan_hash != expected.plan_hash
        ):
            return False
        if expected.requires_memory_snapshot and result.run.versions.memory_snapshot_id is None:
            return False
        expected_bundles = {
            (item.sequence, item.input_hash, item.reviewer_type) for item in expected.bundles
        }
        stored_bundles = {
            (item.sequence, item.input_hash, item.reviewer_type) for item in stored.bundles
        }
        if stored_bundles != expected_bundles:
            return False
        stored_by_sequence = {item.sequence: item for item in stored.bundles}
        for outcome in result.bundle_outcomes:
            if outcome.status is not BundleStatus.SUCCESS:
                continue
            identity = stored_by_sequence.get(outcome.bundle_id.sequence)
            result_hash = _outcome_hash(outcome)
            if (
                identity is None
                or identity.result_hash != result_hash
                or identity.checkpoint_result_hash != result_hash
            ):
                return False
        return self._source_files_match(
            stored,
            repository=repository,
            source_revision=source_revision,
            source_kind=source_kind,
        )

    def _source_files_match(
        self,
        stored: StoredResumeIdentity,
        *,
        repository: Path,
        source_revision: str | None,
        source_kind: DiffSourceKind,
    ) -> bool:
        """過去に読み取った全パスを再読込し、内容ハッシュを照合する."""
        if not stored.source_files:
            return stored.context_source_hash == hash_lines(("context-source-v1",))
        if self._context_source_factory is None:
            return False
        try:
            source = self._context_source_factory(repository, source_revision, source_kind)
            for expected in stored.source_files:
                if expected.source_revision != source.source_revision:
                    return False
                text = source.read_text(expected.path)
                if text is None or hash_text(text) != expected.content_hash:
                    return False
        except GitCommandError, InvalidInputError:
            return False
        return stored.context_source_hash == context_source_hash(stored.source_files)


def context_source_hash(files: tuple[SourceFingerprint, ...]) -> str:
    """追加ソース集合をパスと版と内容ハッシュから安定ハッシュ化する."""
    return hash_lines(
        (
            "context-source-v1",
            *(
                f"{item.bundle_sequence}:{item.path.value}:"
                f"{item.source_revision or 'workspace'}:{item.content_hash}"
                for item in sorted(
                    files,
                    key=lambda item: (item.bundle_sequence, item.path.value),
                )
            ),
        )
    )


def _has_only_retryable_failures(result: ReviewResult) -> bool:
    """部分成功または失敗が再試行可能な失敗だけなら True を返す."""
    failed = tuple(
        outcome for outcome in result.bundle_outcomes if outcome.status is BundleStatus.FAILED
    )
    return bool(failed) and all(outcome.error_code in _RETRYABLE_ERROR_CODES for outcome in failed)


def _outcome_hash(outcome: BundleOutcome) -> str:
    """保存結果を本文なしで照合するハッシュを返す."""
    return hash_lines(
        (
            outcome.status.value,
            outcome.error_code or "",
            *(str(finding.finding_id) for finding in outcome.findings),
        )
    )
