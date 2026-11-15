from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from self_improving_code_review.application.config.settings import AppConfig, ScmConfig
from self_improving_code_review.application.ports.diff_provider import DiffSource
from self_improving_code_review.application.review.models import (
    ReviewMetrics,
    ReviewResult,
    RunRecord,
    RunVersions,
)
from self_improving_code_review.application.scm.models import PublicationResult
from self_improving_code_review.application.scm.plan_publications import plan_publications
from self_improving_code_review.application.scm.review_change import ScmReviewCoordinator
from self_improving_code_review.application.scm_metrics import ScmApiMetrics
from self_improving_code_review.domain.common.enums import (
    DiffSourceKind,
    ReflectionStatus,
    ReviewMode,
    RunStatus,
    Severity,
    ValidationStatus,
)
from self_improving_code_review.domain.common.errors import (
    InvalidInputError,
    PolicyViolationError,
)
from self_improving_code_review.domain.common.values import (
    BundleId,
    FindingId,
    LineRange,
    NormalizedPath,
    ProjectId,
    RunId,
)
from self_improving_code_review.domain.finding.models import (
    CandidateFinding,
    Evidence,
    Finding,
    ValidationOutcome,
)
from self_improving_code_review.domain.scm import (
    ChangeRef,
    PublicationKind,
    PublicationOperation,
    PublicationStatus,
    ScmChange,
    ScmChangeState,
    ScmProvider,
)
from self_improving_code_review.security.models import (
    ScanProfile,
    ScanResult,
    SecretFinding,
    SourcePayload,
)

_RUN_ID = RunId("00000000-0000-4000-8000-000000000001")
_HEAD_SHA = "2" * 40


@pytest.mark.case("UT-M4-003", "UT-M4-005")
def test_planner_uses_stable_severity_order_and_moves_overflow_to_summary() -> None:
    low = _finding(1, "src/z.py", 30, Severity.LOW, is_main=True)
    high = _finding(2, "src/a.py", 10, Severity.HIGH, is_main=True)
    reference = _finding(3, "src/ref.py", 20, Severity.MEDIUM, is_main=False)

    plans = plan_publications(
        _change(),
        _result((low, high), (reference,)),
        max_inline_comments=1,
    )

    assert [plan.kind for plan in plans] == [
        PublicationKind.INLINE,
        PublicationKind.SUMMARY,
    ]
    assert plans[0].finding_id == "B001-F002"
    assert plans[0].request.position is not None
    assert plans[0].request.position.path == NormalizedPath("src/a.py")
    assert "说明: 説明 2" in plans[0].request.body
    assert "依据: 根拠" in plans[0].request.body
    assert "建议: 修正 2" in plans[0].request.body
    assert "规则 ID: GENERAL.TEST" in plans[0].request.body
    assert len(plans[0].publication_id) == 64
    assert (
        f"<!-- self-reviewer:publication={plans[0].publication_id};head={_HEAD_SHA} -->"
        in plans[0].request.body
    )
    assert "src/z.py:30" in plans[1].request.body
    assert "src/ref.py:20" in plans[1].request.body
    assert "依据: 根拠" in plans[1].request.body
    assert "规则 ID: GENERAL.TEST" in plans[1].request.body
    assert all(plan.run_id == str(_RUN_ID) for plan in plans)
    assert all(len(plan.request_hash) == 64 for plan in plans)


@pytest.mark.case("UT-M4-003")
def test_planner_creates_summary_when_review_has_no_findings() -> None:
    plans = plan_publications(_change(), _result((), ()), max_inline_comments=20)

    assert len(plans) == 1
    assert plans[0].kind is PublicationKind.SUMMARY
    assert plans[0].finding_id is None
    assert "没有通过验证的审查发现" in plans[0].request.body
    assert (
        f"<!-- self-reviewer:publication={plans[0].publication_id};head={_HEAD_SHA} -->"
        in plans[0].request.body
    )


@pytest.mark.case("UT-M4-003")
def test_planner_omits_summary_when_all_findings_are_inline() -> None:
    finding = _finding(1, "src/app.py", 5, Severity.MEDIUM, is_main=True)

    plans = plan_publications(_change(), _result((finding,), ()), max_inline_comments=20)

    assert len(plans) == 1
    assert plans[0].kind is PublicationKind.INLINE
    assert "建议:" in plans[0].request.body


@pytest.mark.case("UT-M4-001")
def test_scm_review_coordinator_runs_fixed_range_without_publishing(tmp_path: Path) -> None:
    executor = _Executor(_result((), ()))
    publisher = _Publisher()
    coordinator = _coordinator(executor=executor, publisher=publisher)

    outcome = coordinator.execute(
        tmp_path,
        _change().ref,
        mode=ReviewMode.RULES,
        config_path=tmp_path / "review.yaml",
        should_publish=False,
        execution_nonce="nonce",
    )

    assert outcome.publications == ()
    assert len(outcome.plans) == 1
    assert executor.source == DiffSource(
        kind=DiffSourceKind.RANGE,
        base_revision="1" * 40,
        head_revision=_HEAD_SHA,
    )
    assert executor.execution_nonce == "nonce"
    assert not publisher.calls
    assert len(publisher.plan_calls) == 1


@pytest.mark.case("UT-M4-001")
def test_scm_review_coordinator_publishes_after_head_revalidation(tmp_path: Path) -> None:
    publisher = _Publisher()
    client = _ScmClient(changes=[_change(), _change()])
    coordinator = _coordinator(client=client, publisher=publisher, publish_enabled=True)

    outcome = coordinator.execute(
        tmp_path,
        _change().ref,
        mode=ReviewMode.RULES,
        config_path=None,
        should_publish=True,
    )

    assert outcome.publications[0].status is PublicationStatus.PUBLISHED
    assert len(publisher.calls) == 1
    assert client.change_calls == 2


@pytest.mark.case("UT-M4-001")
@pytest.mark.parametrize(
    "case",
    [
        "disabled",
        "repository",
        "actor",
        "closed",
        "draft",
        "base",
    ],
)
def test_scm_review_coordinator_rejects_invalid_boundaries(
    tmp_path: Path,
    case: str,
) -> None:
    config = ScmConfig(
        enabled=True,
        repository_external_id="owner/repository",
        bot_actor_external_id="review-bot",
    )
    ref = _change().ref
    client = _ScmClient()
    diff = _DiffProvider("1" * 40)
    if case == "disabled":
        config = ScmConfig()
    elif case == "repository":
        ref = ChangeRef(ScmProvider.GITHUB, "other/repository", "42")
    elif case == "actor":
        client = _ScmClient(actor="another-bot")
    elif case == "closed":
        client = _ScmClient(changes=[replace(_change(), state=ScmChangeState.CLOSED)])
    elif case == "draft":
        client = _ScmClient(changes=[replace(_change(), is_draft=True)])
    else:
        diff = _DiffProvider("9" * 40)
    coordinator = _coordinator(config=config, client=client, diff=diff)

    with pytest.raises(InvalidInputError):
        coordinator.execute(
            tmp_path,
            ref,
            mode=ReviewMode.RULES,
            config_path=None,
            should_publish=False,
        )


def test_scm_review_coordinator_allows_draft_when_configured(tmp_path: Path) -> None:
    config = ScmConfig(
        enabled=True,
        repository_external_id="owner/repository",
        bot_actor_external_id="review-bot",
        include_drafts=True,
    )
    client = _ScmClient(changes=[replace(_change(), is_draft=True)])

    outcome = _coordinator(config=config, client=client).execute(
        tmp_path,
        _change().ref,
        mode=ReviewMode.RULES,
        config_path=None,
        should_publish=False,
    )

    assert outcome.change.is_draft


@pytest.mark.case("UT-M4-005")
def test_scm_review_coordinator_blocks_secret_in_publication(tmp_path: Path) -> None:
    publisher = _Publisher()
    coordinator = _coordinator(scanner=_Scanner(has_secret=True), publisher=publisher)

    with pytest.raises(PolicyViolationError):
        coordinator.execute(
            tmp_path,
            _change().ref,
            mode=ReviewMode.RULES,
            config_path=None,
            should_publish=False,
        )
    assert len(publisher.block_calls) == 1
    assert not publisher.plan_calls
    assert not publisher.calls


def test_scm_review_coordinator_blocks_secret_without_persistence_port(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(scanner=_Scanner(has_secret=True))

    with pytest.raises(PolicyViolationError):
        coordinator.execute(
            tmp_path,
            _change().ref,
            mode=ReviewMode.RULES,
            config_path=None,
            should_publish=False,
        )


@pytest.mark.parametrize("status", [RunStatus.FAILED, RunStatus.BLOCKED])
def test_scm_review_coordinator_requires_success_before_publish(
    tmp_path: Path,
    status: RunStatus,
) -> None:
    review = _result((), ())
    failed = replace(review, run=replace(review.run, status=status))
    coordinator = _coordinator(
        executor=_Executor(failed),
        publisher=_Publisher(),
        publish_enabled=True,
    )

    with pytest.raises(InvalidInputError):
        coordinator.execute(
            tmp_path,
            _change().ref,
            mode=ReviewMode.RULES,
            config_path=None,
            should_publish=True,
        )


@pytest.mark.case("UT-M4-004")
def test_scm_review_coordinator_rejects_changed_head_before_publish(tmp_path: Path) -> None:
    changed = replace(_change(), head_sha="3" * 40)
    coordinator = _coordinator(
        client=_ScmClient(changes=[_change(), changed]),
        publisher=_Publisher(),
        publish_enabled=True,
    )

    with pytest.raises(InvalidInputError):
        coordinator.execute(
            tmp_path,
            _change().ref,
            mode=ReviewMode.RULES,
            config_path=None,
            should_publish=True,
        )


@pytest.mark.case("UT-M4-001")
def test_scm_review_coordinator_requires_publisher_when_enabled(tmp_path: Path) -> None:
    coordinator = _coordinator(publish_enabled=True)

    with pytest.raises(InvalidInputError):
        coordinator.execute(
            tmp_path,
            _change().ref,
            mode=ReviewMode.RULES,
            config_path=None,
            should_publish=True,
        )


def _change() -> ScmChange:
    return ScmChange(
        ref=ChangeRef(ScmProvider.GITHUB, "owner/repository", "42"),
        base_sha="1" * 40,
        head_sha=_HEAD_SHA,
        state=ScmChangeState.OPEN,
        is_draft=False,
        source_repository_external_id="owner/repository",
        updated_at=datetime(2026, 9, 25, tzinfo=UTC),
    )


def _finding(
    sequence: int,
    path: str,
    line: int,
    severity: Severity,
    *,
    is_main: bool,
) -> Finding:
    bundle_id = BundleId(_RUN_ID, 1)
    return Finding(
        finding_id=FindingId(bundle_id, sequence),
        bundle_id=bundle_id,
        candidate=CandidateFinding(
            file=NormalizedPath(path),
            line_range=LineRange(line, line),
            category="correctness",
            issue_type="test-issue",
            rule_id="GENERAL.TEST",
            primary_entity="target",
            severity=severity,
            title=f"指摘 {sequence}",
            description=f"説明 {sequence}",
            evidence=Evidence(quote="changed()", reason="根拠"),
            suggestion=f"修正 {sequence}",
            needs_context=False,
        ),
        fingerprint=f"fingerprint-{sequence}",
        validation=ValidationOutcome(
            position_status=(ValidationStatus.VALID if is_main else ValidationStatus.NEEDS_CONTEXT),
            evidence_status=ValidationStatus.VALID,
            reflection_status=ReflectionStatus.REFLECTION_SKIPPED,
        ),
        enclosing_symbol="target",
    )


def _result(
    main: tuple[Finding, ...],
    references: tuple[Finding, ...],
) -> ReviewResult:
    run = RunRecord(
        run_id=_RUN_ID,
        project_id=ProjectId("project"),
        mode=ReviewMode.RULES,
        status=RunStatus.SUCCESS,
        source_revision=_HEAD_SHA,
        model="dataset",
        versions=RunVersions(
            package_version="0.1.0",
            config_hash="config",
            prompt_version="v1",
            prompt_hash="prompt",
            rule_snapshot_id="rules",
            rule_set_hash="rule-hash",
            is_rule_set_empty=False,
            secret_engine_version="1.5.0",
        ),
        idempotency_key="key",
        started_at=datetime(2026, 9, 25, tzinfo=UTC),
        finished_at=datetime(2026, 9, 25, tzinfo=UTC),
    )
    metrics = ReviewMetrics(
        input_tokens=0,
        output_tokens=0,
        latency_ms=0,
        cost_usd=Decimal("0"),
        candidate_count=len(main) + len(references),
        main_finding_count=len(main),
        reference_finding_count=len(references),
        invalid_position_count=len(references),
        invalid_evidence_count=0,
        duplicate_count=0,
        bundle_count=1,
        failed_bundle_count=0,
    )
    return ReviewResult(run, main, references, (), metrics, ())


@dataclass
class _ScmClient:
    """調停器テスト用に認証主体と変更要求を返す."""

    actor: str = "review-bot"
    changes: list[ScmChange] | None = None
    change_calls: int = 0

    def get_authenticated_actor(self) -> str:
        """固定の認証主体を返す."""
        return self.actor

    def get_change(self, ref: ChangeRef) -> ScmChange:
        """呼出順に固定済み変更要求を返す."""
        del ref
        values = self.changes or [_change()]
        index = min(self.change_calls, len(values) - 1)
        self.change_calls += 1
        return values[index]

    def get_metrics(self) -> ScmApiMetrics:
        """変更要求取得を API 呼出回数として返す."""
        return ScmApiMetrics(call_count=self.change_calls)


@dataclass
class _DiffProvider:
    """ローカル HEAD だけを提供する差分ポート代替."""

    head_sha: str | None

    def resolve_head_sha(self, repository_path: Path) -> str | None:
        """設定された HEAD を返す."""
        del repository_path
        return self.head_sha


@dataclass
class _Executor:
    """レビュー結果と固定された差分入力を記録する."""

    result: ReviewResult
    source: DiffSource | None = None
    execution_nonce: str | None = None

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
        """入力を記録して固定結果を返す."""
        del repository, config, mode, config_path
        self.source = diff_source
        self.execution_nonce = execution_nonce
        return self.result


@dataclass
class _Scanner:
    """公開本文の機密情報検査結果を制御する."""

    has_secret: bool = False

    def scan(self, payload: SourcePayload, profile: ScanProfile) -> ScanResult:
        """指定に応じて検出ありまたは合格を返す."""
        findings = (SecretFinding("test", "publication", 1),) if self.has_secret else ()
        return ScanResult(payload.content_hash, "test", profile, findings)


@dataclass
class _Publisher:
    """公開要求を記録して成功結果を返す."""

    calls: list[tuple[ScmChange, tuple[object, ...]]] | None = None
    plan_calls: list[tuple[ScmChange, tuple[object, ...]]] | None = None
    block_calls: list[tuple[ScmChange, tuple[object, ...]]] | None = None

    def __post_init__(self) -> None:
        """可変な呼出記録をインスタンスごとに初期化する."""
        if self.calls is None:
            self.calls = []
        if self.plan_calls is None:
            self.plan_calls = []
        if self.block_calls is None:
            self.block_calls = []

    def save_plans(self, change: ScmChange, plans: tuple[object, ...]) -> None:
        """ドライラン計画を記録する."""
        assert self.plan_calls is not None
        self.plan_calls.append((change, plans))

    def block_plans(self, change: ScmChange, plans: tuple[object, ...]) -> None:
        """機密情報で遮断された計画を記録する."""
        assert self.block_calls is not None
        self.block_calls.append((change, plans))

    def publish(
        self,
        change: ScmChange,
        plans: tuple[object, ...],
    ) -> tuple[PublicationResult, ...]:
        """公開呼出を記録し、成功結果を返す."""
        assert self.calls is not None
        self.calls.append((change, plans))
        return (
            PublicationResult(
                fingerprint_hash="fingerprint",
                status=PublicationStatus.PUBLISHED,
                operation=PublicationOperation.CREATED,
                external_comment_id="10",
            ),
        )


def _coordinator(
    *,
    config: ScmConfig | None = None,
    client: _ScmClient | None = None,
    diff: _DiffProvider | None = None,
    executor: _Executor | None = None,
    scanner: _Scanner | None = None,
    publisher: _Publisher | None = None,
    publish_enabled: bool = False,
) -> ScmReviewCoordinator:
    """標準的な有効 SCM 設定で調停器を構築する."""
    scm = config or ScmConfig(
        enabled=True,
        repository_external_id="owner/repository",
        bot_actor_external_id="review-bot",
        publish_enabled=publish_enabled,
    )
    return ScmReviewCoordinator(
        config=AppConfig(scm=scm),
        scm_client=client or _ScmClient(),
        diff_provider=diff or _DiffProvider("1" * 40),
        review_executor=executor or _Executor(_result((), ())),
        secret_scanner=scanner or _Scanner(),
        publisher=publisher,
    )
