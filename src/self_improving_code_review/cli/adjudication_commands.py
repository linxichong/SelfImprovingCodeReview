"""M2 評価照合の盲化出力と人工裁定コマンド."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from self_improving_code_review.application.config.settings import load_config
from self_improving_code_review.application.evaluation.run_evaluation import (
    ArmReport,
    EvaluationRunner,
)
from self_improving_code_review.cli.output import write_document
from self_improving_code_review.cli.review_commands import (
    _validate_project_context,
    _within_repository,
)
from self_improving_code_review.domain.common.enums import EvaluationArm, MatchResult, Permission
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.infrastructure.persistence import SqliteUnitOfWorkFactory
from self_improving_code_review.infrastructure.persistence.evaluation_store import EvaluationStore
from self_improving_code_review.infrastructure.system import SystemClock
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.authorization import AllowListActorAuthorizer

__all__ = ["execute_adjudication_command"]


def execute_adjudication_command(arguments: Namespace) -> int:
    """盲化出力または人工裁定を一回実行する."""
    repository = Path(arguments.repository).resolve()
    config = load_config(Path(arguments.config) if arguments.config else None)
    database_path = _within_repository(repository, config.database.path, "データベースのパス")
    SqliteUnitOfWorkFactory(database_path)
    _validate_project_context(repository, config, database_path)
    project_id = ProjectId(config.project_id) if config.governance.enabled else None
    store = EvaluationStore(database_path, project_id)
    if arguments.adjudication_command == "export":
        pending = store.list_pending_adjudications(arguments.split, arguments.set_version)
        output = (
            _within_repository(repository, Path(arguments.output), "裁定出力ファイル")
            if arguments.output
            else None
        )
        write_document(
            {
                "schema_version": 1,
                "set_id": f"{arguments.split}-v{arguments.set_version}",
                "pending_count": len(pending),
                "items": [
                    {
                        "adjudication_token": item.token,
                        "finding": item.finding,
                        "candidate_ground_truth_issues": list(item.candidate_ground_truth_issues),
                    }
                    for item in pending
                ],
            },
            output,
        )
        return 0

    if arguments.adjudication_command == "report":
        reports = store.load_reports(arguments.split, arguments.set_version)
        if EvaluationArm.RULES not in reports or EvaluationArm.MEMORY not in reports:
            raise InvalidInputError(Messages.ADJUDICATION_REPORTS_REQUIRED)
        verdict = EvaluationRunner().compare(
            reports[EvaluationArm.RULES],
            reports[EvaluationArm.MEMORY],
        )
        write_document(
            {
                "schema_version": 1,
                "set_id": f"{arguments.split}-v{arguments.set_version}",
                "reports": [
                    _report_document(report)
                    for arm in (EvaluationArm.RULES, EvaluationArm.MEMORY)
                    for report in reports[arm]
                ],
                "is_successful": verdict.is_successful,
                "reason_codes": list(verdict.reason_codes),
                "lost_critical_issue_ids": list(verdict.lost_critical_issue_ids),
            },
            None,
        )
        return 0 if verdict.is_successful else 2

    authorizer = AllowListActorAuthorizer(
        maintainers=config.memory.maintainers,
        is_production=config.memory.is_production,
    )
    authorizer.require(arguments.actor, Permission.ADJUDICATE_EVALUATION)
    outcome = store.resolve_adjudication(
        arguments.token,
        MatchResult(arguments.result),
        issue_id=arguments.issue_id,
        actor=arguments.actor,
        reason=arguments.reason,
        adjudicated_at=SystemClock().now_utc(),
    )
    write_document(
        {
            "schema_version": 1,
            "adjudication_token": outcome.token,
            "result": outcome.result.value,
            "remaining_count": outcome.remaining_count,
        },
        None,
    )
    return 0


def _report_document(report: ArmReport) -> dict[str, object]:
    """裁定反映後の比較群別測定値を JSON 文書へ変換する."""
    return {
        "arm": report.arm.value,
        "repeat": report.repeat,
        "true_positive": report.counts.true_positive,
        "false_positive": report.counts.false_positive,
        "false_negative": report.counts.false_negative,
        "duplicate": report.counts.duplicate,
        "review_required": report.counts.review_required,
        "precision": report.precision.value,
        "recall": report.recall.value,
        "critical_recall": report.critical_recall.value,
        "critical_issue_ids": sorted(report.critical_issue_ids),
        "input_tokens": report.input_tokens,
        "output_tokens": report.output_tokens,
        "latency_ms": report.latency_ms,
        "cost_usd": str(report.cost_usd),
    }
