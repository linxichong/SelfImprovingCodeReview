"""M3 の固定入力に基づく厳格な再開判定を検証する."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from self_improving_code_review.application.ports.context_source import SourceFingerprint
from self_improving_code_review.application.review.bundle_review_models import BundleReview
from self_improving_code_review.application.review.models import (
    BundleOutcome,
    ReviewMetrics,
    ReviewResult,
    RunRecord,
    RunVersions,
)
from self_improving_code_review.application.review.resume_review import (
    BundleResumeIdentity,
    ResumeIdentity,
    ResumeReview,
    StoredResumeIdentity,
    context_source_hash,
)
from self_improving_code_review.application.review.review_aggregation import (
    find_changed_source_sequences,
)
from self_improving_code_review.domain.common.enums import (
    BundleStatus,
    DiffSourceKind,
    ReviewMode,
    RunStatus,
)
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_lines, hash_text
from self_improving_code_review.domain.common.values import (
    BundleId,
    NormalizedPath,
    ProjectId,
    RunId,
)

RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")


class Source:
    """再開判定へ固定内容を返す追加ソース模擬."""

    def __init__(
        self,
        text: str | None,
        *,
        revision: str | None = None,
        error: bool = False,
    ) -> None:
        """返却本文、固定版、失敗有無を保持する."""
        self.source_revision = revision
        self._text = text
        self._error = error

    @property
    def fingerprints(self) -> tuple[SourceFingerprint, ...]:
        """この模擬では読取履歴を返さない."""
        return ()

    def read_text(self, _path: NormalizedPath) -> str | None:
        """設定済み本文を返すか入力例外を送出する."""
        if self._error:
            raise InvalidInputError("changed")
        return self._text

    def list_paths(
        self,
        _path_scope: tuple[NormalizedPath, ...],
        *,
        max_files: int,
    ) -> tuple[NormalizedPath, ...]:
        """空のパス集合を返す."""
        del max_files
        return ()


@pytest.mark.case("UT-M3-011")
def test_resume_accepts_matching_retryable_failure(tmp_path: Path) -> None:
    result, expected, stored = _base_state()

    assert ResumeReview(None).can_resume(
        result,
        stored,
        expected,
        repository=tmp_path,
        source_revision=None,
        source_kind=DiffSourceKind.WORKSPACE,
    )


@pytest.mark.case("UT-M3-011")
def test_resume_rejects_nonretryable_or_missing_state(tmp_path: Path) -> None:
    result, expected, stored = _base_state()
    nonretryable = replace(
        result,
        bundle_outcomes=(replace(result.bundle_outcomes[0], error_code="InvalidInputError"),),
    )

    assert not ResumeReview(None).can_resume(
        nonretryable,
        stored,
        expected,
        repository=tmp_path,
        source_revision=None,
        source_kind=DiffSourceKind.WORKSPACE,
    )
    completed = replace(
        result,
        run=replace(result.run, status=RunStatus.SUCCESS),
    )
    assert not ResumeReview(None).can_resume(
        completed,
        stored,
        expected,
        repository=tmp_path,
        source_revision=None,
        source_kind=DiffSourceKind.WORKSPACE,
    )
    assert not ResumeReview(None).can_resume(
        result,
        None,
        expected,
        repository=tmp_path,
        source_revision=None,
        source_kind=DiffSourceKind.WORKSPACE,
    )


@pytest.mark.case("UT-M3-011")
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pipeline_config_hash", "other"),
        ("diff_hash", "other"),
        ("plan_hash", "other"),
    ],
)
def test_resume_rejects_run_identity_mismatch(tmp_path: Path, field: str, value: str) -> None:
    result, expected, stored = _base_state()
    changed = replace(stored, **{field: value})

    assert not ResumeReview(None).can_resume(
        result,
        changed,
        expected,
        repository=tmp_path,
        source_revision=None,
        source_kind=DiffSourceKind.WORKSPACE,
    )


@pytest.mark.case("UT-M3-011")
def test_resume_requires_memory_snapshot_and_matching_bundle_inputs(tmp_path: Path) -> None:
    result, expected, stored = _base_state()
    memory_expected = replace(expected, requires_memory_snapshot=True)
    different_bundle = replace(
        stored,
        bundles=(replace(stored.bundles[0], input_hash="other"),),
    )

    assert not ResumeReview(None).can_resume(
        result,
        stored,
        memory_expected,
        repository=tmp_path,
        source_revision=None,
        source_kind=DiffSourceKind.WORKSPACE,
    )
    with_snapshot = replace(
        result,
        run=replace(
            result.run,
            versions=replace(result.run.versions, memory_snapshot_id="snapshot"),
        ),
    )
    assert ResumeReview(None).can_resume(
        with_snapshot,
        stored,
        memory_expected,
        repository=tmp_path,
        source_revision=None,
        source_kind=DiffSourceKind.WORKSPACE,
    )
    assert not ResumeReview(None).can_resume(
        result,
        different_bundle,
        expected,
        repository=tmp_path,
        source_revision=None,
        source_kind=DiffSourceKind.WORKSPACE,
    )


@pytest.mark.case("UT-M3-011")
@pytest.mark.parametrize("mismatch", ["missing", "result", "checkpoint"])
def test_resume_validates_success_result_hash(tmp_path: Path, mismatch: str) -> None:
    result, expected, stored = _state_with_success()
    identities = list(stored.bundles)
    if mismatch == "missing":
        identities = identities[1:]
    elif mismatch == "result":
        identities[0] = replace(identities[0], result_hash="other")
    else:
        identities[0] = replace(identities[0], checkpoint_result_hash="other")

    assert not ResumeReview(None).can_resume(
        result,
        replace(stored, bundles=tuple(identities)),
        expected,
        repository=tmp_path,
        source_revision=None,
        source_kind=DiffSourceKind.WORKSPACE,
    )


@pytest.mark.case("UT-M3-011")
def test_resume_accepts_verified_success_result_hash(tmp_path: Path) -> None:
    result, expected, stored = _state_with_success()

    assert ResumeReview(None).can_resume(
        result,
        stored,
        expected,
        repository=tmp_path,
        source_revision=None,
        source_kind=DiffSourceKind.WORKSPACE,
    )


@pytest.mark.case("UT-M3-011")
def test_resume_validates_context_source_revision_content_and_set_hash(tmp_path: Path) -> None:
    result, expected, stored = _base_state()
    fingerprint = SourceFingerprint(
        1,
        NormalizedPath("src/app.py"),
        None,
        hash_text("value = 1"),
    )
    with_source = replace(
        stored,
        source_files=(fingerprint,),
        context_source_hash=context_source_hash((fingerprint,)),
    )

    assert not ResumeReview(None).can_resume(
        result,
        with_source,
        expected,
        repository=tmp_path,
        source_revision=None,
        source_kind=DiffSourceKind.WORKSPACE,
    )
    for source in (
        Source("value = 1", revision="a" * 40),
        Source(None),
        Source("value = 2"),
        Source("value = 1", error=True),
    ):
        assert not ResumeReview(lambda *_args, _source=source: _source).can_resume(
            result,
            with_source,
            expected,
            repository=tmp_path,
            source_revision=None,
            source_kind=DiffSourceKind.WORKSPACE,
        )
    assert not ResumeReview(lambda *_args: Source("value = 1")).can_resume(
        result,
        replace(with_source, context_source_hash="other"),
        expected,
        repository=tmp_path,
        source_revision=None,
        source_kind=DiffSourceKind.WORKSPACE,
    )
    assert ResumeReview(lambda *_args: Source("value = 1")).can_resume(
        result,
        with_source,
        expected,
        repository=tmp_path,
        source_revision=None,
        source_kind=DiffSourceKind.WORKSPACE,
    )


@pytest.mark.case("UT-M3-011")
def test_resume_rejects_invalid_empty_context_hash(tmp_path: Path) -> None:
    result, expected, stored = _base_state()

    assert not ResumeReview(None).can_resume(
        result,
        replace(stored, context_source_hash="other"),
        expected,
        repository=tmp_path,
        source_revision=None,
        source_kind=DiffSourceKind.WORKSPACE,
    )


@pytest.mark.case("UT-M3-011")
def test_fixed_revision_fingerprints_do_not_trigger_worktree_change() -> None:
    """固定コミットの同一性は作業ツリー版混在検査の対象外とする。"""
    review = SimpleNamespace(
        source_fingerprints=(
            SourceFingerprint(
                1,
                NormalizedPath("src/app.py"),
                "a" * 40,
                hash_text("value = 1"),
            ),
        )
    )

    assert find_changed_source_sequences(cast(tuple[BundleReview, ...], (review,))) == frozenset()


def _base_state() -> tuple[ReviewResult, ResumeIdentity, StoredResumeIdentity]:
    failed = BundleOutcome(
        BundleId(RUN_ID, 1),
        BundleStatus.FAILED,
        (),
        error_code="LlmClientError",
    )
    result = _result((failed,))
    bundle = BundleResumeIdentity(1, "input-1", "general")
    expected = ResumeIdentity("config", "diff", "plan", (bundle,), False)
    stored = StoredResumeIdentity(
        "config",
        "diff",
        "plan",
        context_source_hash(()),
        (bundle,),
        (),
    )
    return result, expected, stored


def _state_with_success() -> tuple[ReviewResult, ResumeIdentity, StoredResumeIdentity]:
    success = BundleOutcome(BundleId(RUN_ID, 1), BundleStatus.SUCCESS, ())
    failed = BundleOutcome(
        BundleId(RUN_ID, 2),
        BundleStatus.FAILED,
        (),
        error_code="InvalidLlmOutputError",
    )
    result = _result((success, failed))
    success_hash = hash_lines(("success", ""))
    bundles = (
        BundleResumeIdentity(1, "input-1", "general", success_hash, success_hash),
        BundleResumeIdentity(2, "input-2", "security"),
    )
    expected = ResumeIdentity(
        "config",
        "diff",
        "plan",
        tuple(replace(item, result_hash=None, checkpoint_result_hash=None) for item in bundles),
        False,
    )
    stored = StoredResumeIdentity(
        "config",
        "diff",
        "plan",
        context_source_hash(()),
        bundles,
        (),
    )
    return result, expected, stored


def _result(outcomes: tuple[BundleOutcome, ...]) -> ReviewResult:
    versions = RunVersions(
        package_version="test",
        config_hash="config",
        prompt_version="1",
        prompt_hash="prompt",
        rule_snapshot_id="rules",
        rule_set_hash="rule-hash",
        is_rule_set_empty=True,
        secret_engine_version="scanner",
    )
    metrics = ReviewMetrics(
        input_tokens=0,
        output_tokens=0,
        latency_ms=0,
        cost_usd=Decimal("0"),
        candidate_count=0,
        main_finding_count=0,
        reference_finding_count=0,
        invalid_position_count=0,
        invalid_evidence_count=0,
        duplicate_count=0,
        bundle_count=len(outcomes),
        failed_bundle_count=sum(item.status is BundleStatus.FAILED for item in outcomes),
    )
    run = RunRecord(
        run_id=RUN_ID,
        project_id=ProjectId("test-project"),
        mode=ReviewMode.RULES,
        status=RunStatus.PARTIAL_SUCCESS,
        source_revision=None,
        model="test",
        versions=versions,
        idempotency_key="key",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return ReviewResult(run, (), (), (), metrics, outcomes)
