"""M2 比較群 B/C 評価コマンドの構成起点."""

from __future__ import annotations

from argparse import Namespace
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from self_improving_code_review.application.config.settings import AppConfig, load_config
from self_improving_code_review.application.evaluation.ground_truth import GroundTruthService
from self_improving_code_review.application.evaluation.run_evaluation import (
    ArmReport,
    EvaluationRunner,
)
from self_improving_code_review.application.ports.diff_provider import DiffSource
from self_improving_code_review.cli.output import write_document
from self_improving_code_review.cli.review_commands import (
    _validate_project_context,
    _within_repository,
    run_configured_review,
)
from self_improving_code_review.domain.common.enums import (
    DiffSourceKind,
    EvaluationArm,
    ReviewMode,
    RunStatus,
)
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.domain.evaluation.models import GroundTruthIssue
from self_improving_code_review.infrastructure.persistence import SqliteUnitOfWorkFactory
from self_improving_code_review.infrastructure.persistence.evaluation_store import EvaluationStore
from self_improving_code_review.infrastructure.system import SystemClock, Uuid4IdGenerator
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.authorization import AllowListActorAuthorizer

__all__ = ["execute_evaluation_command"]

_INCOMPLETE_REVIEW_RUNS = "INCOMPLETE_REVIEW_RUNS"


@dataclass(frozen=True, slots=True)
class _PendingReport:
    """全レビュー成功後まで永続化を保留する比較群報告."""

    report: ArmReport
    config_hash: str
    rule_snapshot_id: str | None
    memory_snapshot_id: str | None
    started_at: datetime
    finished_at: datetime
    review_run_ids: tuple[str, ...]


class _IncompleteReviewRunsError(Exception):
    """比較評価へ使用できないレビュー実行を安全な識別情報だけで通知する."""

    def __init__(self, run_id: str, status: RunStatus) -> None:
        """未完了実行の識別子と状態を保持する."""
        super().__init__(_INCOMPLETE_REVIEW_RUNS)
        self.run_id = run_id
        self.status = status


def execute_evaluation_command(arguments: Namespace) -> int:
    """固定正解データに対して比較群を規定回数実行する."""
    repository = Path(arguments.repository).resolve()
    config_path = Path(arguments.config) if arguments.config else None
    config = load_config(config_path)
    database_path = _within_repository(repository, config.database.path, "データベースのパス")
    SqliteUnitOfWorkFactory(database_path)
    _validate_project_context(repository, config, database_path)
    project_id = ProjectId(config.project_id) if config.governance.enabled else None
    store = EvaluationStore(database_path, project_id)
    evaluation_set = store.load_published(arguments.split, arguments.set_version)
    population = GroundTruthService(
        authorizer=AllowListActorAuthorizer(maintainers=(), is_production=False),
        clock=SystemClock(),
    ).measure_population(evaluation_set.issues, evaluation_set.change_refs)
    if not population.is_satisfied:
        write_document(
            {
                "schema_version": 1,
                "set_id": str(evaluation_set.set_id),
                "status": "insufficient-population",
                "unmet_requirements": list(population.unmet_requirements),
            },
            None,
        )
        return 1

    arms = _parse_arms(arguments.arms)
    reports: dict[EvaluationArm, list[ArmReport]] = defaultdict(list)
    pending_reports: list[_PendingReport] = []
    clock = SystemClock()
    id_generator = Uuid4IdGenerator()
    runner = EvaluationRunner()
    for arm in arms:
        for repeat in range(1, arguments.repeat + 1):
            started_at = clock.now_utc()
            try:
                (
                    report,
                    rule_snapshot_id,
                    memory_snapshot_id,
                    effective_config_hash,
                    review_run_ids,
                ) = _evaluate_once(
                    runner,
                    arm,
                    repeat,
                    repository,
                    config,
                    config_path,
                    evaluation_set.issues,
                    evaluation_set.change_refs,
                    set_id=str(evaluation_set.set_id),
                )
            except _IncompleteReviewRunsError as exc:
                write_document(
                    {
                        "schema_version": 1,
                        "set_id": str(evaluation_set.set_id),
                        "set_hash": evaluation_set.set_hash,
                        "status": _INCOMPLETE_REVIEW_RUNS,
                        "incomplete_review_runs": [
                            {"run_id": exc.run_id, "status": exc.status.value}
                        ],
                    },
                    None,
                )
                return 2
            pending_reports.append(
                _PendingReport(
                    report=report,
                    config_hash=effective_config_hash,
                    rule_snapshot_id=rule_snapshot_id,
                    memory_snapshot_id=memory_snapshot_id,
                    started_at=started_at,
                    finished_at=clock.now_utc(),
                    review_run_ids=review_run_ids,
                )
            )
            reports[arm].append(report)

    verdict = None
    if EvaluationArm.RULES in reports and EvaluationArm.MEMORY in reports:
        verdict = runner.compare(
            tuple(reports[EvaluationArm.RULES]),
            tuple(reports[EvaluationArm.MEMORY]),
        )
    for pending in pending_reports:
        store.save_report(
            id_generator.new_evaluation_id(),
            evaluation_set.set_id,
            pending.report,
            config_hash=pending.config_hash,
            rule_snapshot_id=pending.rule_snapshot_id,
            memory_snapshot_id=pending.memory_snapshot_id,
            started_at=pending.started_at,
            finished_at=pending.finished_at,
            review_run_ids=pending.review_run_ids,
        )
    write_document(
        {
            "schema_version": 1,
            "set_id": str(evaluation_set.set_id),
            "set_hash": evaluation_set.set_hash,
            "reports": [_report_document(item) for values in reports.values() for item in values],
            "verdict": (
                {
                    "is_successful": verdict.is_successful,
                    "reason_codes": list(verdict.reason_codes),
                    "lost_critical_issue_ids": list(verdict.lost_critical_issue_ids),
                }
                if verdict is not None
                else None
            ),
        },
        None,
    )
    return 0 if verdict is None or verdict.is_successful else 2


def _evaluate_once(
    runner: EvaluationRunner,
    arm: EvaluationArm,
    repeat: int,
    repository: Path,
    config: AppConfig,
    config_path: Path | None,
    issues: tuple[GroundTruthIssue, ...],
    change_refs: frozenset[str] | None = None,
    *,
    set_id: str,
) -> tuple[ArmReport, str | None, str | None, str, tuple[str, ...]]:
    """比較群一回分を変更参照ごとに実行して集約する."""
    mode = {
        EvaluationArm.BASELINE: ReviewMode.BASELINE,
        EvaluationArm.RULES: ReviewMode.RULES,
        EvaluationArm.MEMORY: ReviewMode.MEMORY,
    }[arm]
    memory_config = config.memory.model_copy(
        update={"is_injection_enabled": arm is EvaluationArm.MEMORY}
    )
    arm_config = config.model_copy(
        update={
            "review": config.review.model_copy(update={"mode": mode.value}),
            "memory": memory_config,
        }
    )
    by_change: dict[str, list[GroundTruthIssue]] = defaultdict(list)
    for change_ref in change_refs or frozenset(issue.change_ref for issue in issues):
        by_change[change_ref] = []
    for issue in issues:
        by_change[issue.change_ref].append(issue)
    cases = []
    input_tokens = output_tokens = latency_ms = 0
    cost_usd = Decimal("0")
    rule_snapshot_id: str | None = None
    memory_snapshot_id: str | None = None
    review_run_ids: list[str] = []
    for change_ref in sorted(by_change):
        result = run_configured_review(
            repository,
            arm_config,
            DiffSource(kind=DiffSourceKind.COMMIT, commit=change_ref),
            mode,
            config_path=config_path,
            execution_nonce=f"evaluation:{set_id}:{arm.value}:{repeat}:{change_ref}",
        )
        if result.run.status is not RunStatus.SUCCESS:
            raise _IncompleteReviewRunsError(str(result.run.run_id), result.run.status)
        cases.append((result.main_findings, tuple(by_change[change_ref])))
        review_run_ids.append(str(result.run.run_id))
        input_tokens += result.metrics.input_tokens
        output_tokens += result.metrics.output_tokens
        latency_ms += result.metrics.latency_ms
        cost_usd += result.metrics.cost_usd
        rule_snapshot_id = rule_snapshot_id or result.run.versions.rule_snapshot_id
        memory_snapshot_id = memory_snapshot_id or result.run.versions.memory_snapshot_id
    return (
        runner.evaluate_cases(
            arm,
            repeat,
            tuple(cases),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
        ),
        rule_snapshot_id,
        memory_snapshot_id,
        arm_config.content_hash,
        tuple(review_run_ids),
    )


def _parse_arms(raw: str) -> tuple[EvaluationArm, ...]:
    """カンマ区切りの比較群を重複なしで返す."""
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not values:
        raise InvalidInputError(Messages.EVAL_ARMS_REQUIRED)
    try:
        arms = tuple(dict.fromkeys(EvaluationArm(item) for item in values))
    except ValueError as exc:
        raise InvalidInputError(Messages.EVAL_ARMS_INVALID) from exc
    return arms


def _report_document(report: ArmReport) -> dict[str, object]:
    """比較群報告を JSON 出力用の文書へ変換する."""
    return {
        "arm": report.arm.value,
        "repeat": report.repeat,
        "true_positive": report.counts.true_positive,
        "false_positive": report.counts.false_positive,
        "false_negative": report.counts.false_negative,
        "review_required": report.counts.review_required,
        "precision": report.precision.value,
        "recall": report.recall.value,
        "critical_recall": report.critical_recall.value,
        "input_tokens": report.input_tokens,
        "output_tokens": report.output_tokens,
        "latency_ms": report.latency_ms,
        "cost_usd": str(report.cost_usd),
    }
