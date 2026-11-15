"""CLI プロセス境界と終了コードの対応付け."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Never

from self_improving_code_review.cli.adjudication_commands import execute_adjudication_command
from self_improving_code_review.cli.benchmark_commands import (
    add_benchmark_options,
    execute_benchmark,
)
from self_improving_code_review.cli.evaluation_commands import execute_evaluation_command
from self_improving_code_review.cli.feedback_commands import execute_feedback_command
from self_improving_code_review.cli.governance_commands import execute_governance_command
from self_improving_code_review.cli.ground_truth_commands import execute_ground_truth_command
from self_improving_code_review.cli.memory_commands import execute_memory_command
from self_improving_code_review.cli.output import write_error
from self_improving_code_review.cli.review_commands import execute_review_command
from self_improving_code_review.cli.rule_commands import execute_rules_check_command
from self_improving_code_review.cli.scm_commands import execute_scm_command
from self_improving_code_review.cli.web_commands import add_web_options, execute_web_command
from self_improving_code_review.domain.common.enums import (
    ConflictResolution,
    ConflictStatus,
    FeedbackLabel,
    MemoryStatus,
    ReviewMode,
    Severity,
)
from self_improving_code_review.domain.common.errors import (
    DomainError,
    InfrastructureError,
    InvalidDiffError,
    InvalidInputError,
    InvalidLlmOutputError,
    LlmClientError,
    PolicyViolationError,
)
from self_improving_code_review.messages import Messages

__all__ = ["main"]


class _ArgumentParser(argparse.ArgumentParser):
    """argparse の失敗を製品の不正入力契約へ変換する."""

    def error(self, message: str) -> Never:
        """終了コード 2 で終了せず、ドメイン入力エラーを送出する."""
        raise InvalidInputError(message)


def build_parser() -> argparse.ArgumentParser:
    """コマンドライン解析処理を返す."""
    parser = _ArgumentParser(
        prog="review",
        description=Messages.CLI_DESCRIPTION,
        add_help=False,
    )
    root_options = parser.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    root_options.add_argument(
        "-h",
        "--help",
        action="help",
        help=Messages.CLI_HELP_HELP,
    )
    commands = parser.add_subparsers(
        dest="command",
        required=True,
        title=Messages.CLI_COMMANDS_TITLE,
    )
    review = commands.add_parser(
        "review",
        help=Messages.CLI_REVIEW_HELP,
        description=Messages.CLI_REVIEW_DESCRIPTION,
        add_help=False,
    )
    review_options = review.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    review_options.add_argument(
        "-h",
        "--help",
        action="help",
        help=Messages.CLI_HELP_HELP,
    )
    review_options.add_argument(
        "--repository",
        default=".",
        metavar="PATH",
        help=Messages.CLI_REPOSITORY_HELP,
    )
    review_options.add_argument(
        "--project",
        metavar="PROJECT_ID",
        help=Messages.CLI_PROJECT_HELP,
    )
    review_options.add_argument(
        "--config",
        metavar="YAML",
        help=Messages.CLI_CONFIG_HELP,
    )
    review_options.add_argument(
        "--mode",
        choices=[item.value for item in ReviewMode],
        metavar="MODE",
        help=Messages.CLI_MODE_HELP,
    )
    review_options.add_argument(
        "--output",
        metavar="JSON",
        help=Messages.CLI_OUTPUT_HELP,
    )
    source_section = review.add_argument_group(
        title=Messages.CLI_DIFF_SOURCE_TITLE,
        description=Messages.CLI_DIFF_SOURCE_DESCRIPTION,
    )
    source = source_section.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--git-diff",
        action="store_true",
        help=Messages.CLI_GIT_DIFF_HELP,
    )
    source.add_argument(
        "--range",
        nargs=2,
        dest="range_revisions",
        metavar=("BASE", "HEAD"),
        help=Messages.CLI_RANGE_HELP,
    )
    source.add_argument(
        "--commit",
        metavar="SHA",
        help=Messages.CLI_COMMIT_HELP,
    )
    source.add_argument(
        "--diff-file",
        metavar="PATCH",
        help=Messages.CLI_DIFF_FILE_HELP,
    )
    rules = commands.add_parser(
        "rules",
        help=Messages.CLI_RULES_HELP,
        description=Messages.CLI_RULES_DESCRIPTION,
        add_help=False,
    )
    rule_commands = rules.add_subparsers(dest="rules_command", required=True)
    rules_options = rules.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    rules_options.add_argument("-h", "--help", action="help", help=Messages.CLI_HELP_HELP)
    check = rule_commands.add_parser(
        "check",
        help=Messages.CLI_RULES_CHECK_HELP,
        description=Messages.CLI_RULES_CHECK_DESCRIPTION,
        add_help=False,
    )
    check_options = check.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    check_options.add_argument("-h", "--help", action="help", help=Messages.CLI_HELP_HELP)
    check_options.add_argument(
        "--repository", default=".", metavar="PATH", help=Messages.CLI_REPOSITORY_HELP
    )
    check_options.add_argument("--config", metavar="YAML", help=Messages.CLI_CONFIG_HELP)
    check_options.add_argument("--file", metavar="PATH", help=Messages.CLI_RULE_FILE_HELP)
    check_options.add_argument("--output", metavar="JSON", help=Messages.CLI_OUTPUT_HELP)
    check_source_section = check.add_argument_group(
        title=Messages.CLI_DIFF_SOURCE_TITLE,
        description=Messages.CLI_DIFF_SOURCE_DESCRIPTION,
    )
    check_source = check_source_section.add_mutually_exclusive_group(required=True)
    check_source.add_argument(
        "--git-diff",
        action="store_true",
        help=Messages.CLI_GIT_DIFF_HELP,
    )
    check_source.add_argument(
        "--range",
        nargs=2,
        dest="range_revisions",
        metavar=("BASE", "HEAD"),
        help=Messages.CLI_RANGE_HELP,
    )
    check_source.add_argument(
        "--commit",
        metavar="SHA",
        help=Messages.CLI_COMMIT_HELP,
    )
    check_source.add_argument(
        "--diff-file",
        metavar="PATCH",
        help=Messages.CLI_DIFF_FILE_HELP,
    )
    _add_feedback_commands(commands)
    _add_memory_commands(commands)
    _add_ground_truth_commands(commands)
    _add_evaluation_command(commands)
    _add_adjudication_commands(commands)
    _add_scm_commands(commands)
    _add_governance_commands(commands)
    dashboard = commands.add_parser("dashboard", help=Messages.WEB_HELP, add_help=False)
    add_web_options(dashboard)
    benchmark = commands.add_parser("benchmark", help=Messages.BENCH_HELP, add_help=False)
    add_benchmark_options(benchmark)
    return parser


def _add_governance_commands(commands: argparse._SubParsersAction[_ArgumentParser]) -> None:
    """M5 プロジェクト統制サブコマンドを登録する."""
    governance = commands.add_parser(
        "governance",
        help=Messages.CLI_GOVERNANCE_HELP,
        description=Messages.CLI_GOVERNANCE_DESCRIPTION,
        add_help=False,
    )
    governance.add_argument_group(Messages.CLI_OPTIONS_TITLE).add_argument(
        "-h", "--help", action="help", help=Messages.CLI_HELP_HELP
    )
    governance_commands = governance.add_subparsers(
        dest="governance_command",
        required=True,
    )
    project = governance_commands.add_parser(
        "project",
        help=Messages.CLI_GOVERNANCE_PROJECT_HELP,
        add_help=False,
    )
    project.add_argument_group(Messages.CLI_OPTIONS_TITLE).add_argument(
        "-h", "--help", action="help", help=Messages.CLI_HELP_HELP
    )
    project_commands = project.add_subparsers(dest="project_command", required=True)

    list_command = project_commands.add_parser(
        "list",
        help=Messages.CLI_GOVERNANCE_PROJECT_LIST_HELP,
        add_help=False,
    )
    _add_governance_shared_options(list_command.add_argument_group(Messages.CLI_OPTIONS_TITLE))

    register = project_commands.add_parser(
        "register",
        help=Messages.CLI_GOVERNANCE_PROJECT_REGISTER_HELP,
        add_help=False,
    )
    register_options = register.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_governance_shared_options(register_options)
    _add_governance_actor_options(register_options)
    register_options.add_argument(
        "--project", required=True, metavar="PROJECT_ID", help=Messages.CLI_PROJECT_HELP
    )
    register_options.add_argument(
        "--name", required=True, metavar="NAME", help=Messages.CLI_GOVERNANCE_PROJECT_NAME_HELP
    )

    bind = project_commands.add_parser(
        "bind-repository",
        help=Messages.CLI_GOVERNANCE_BIND_REPOSITORY_HELP,
        add_help=False,
    )
    bind_options = bind.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_governance_shared_options(bind_options)
    _add_governance_actor_options(bind_options)
    bind_options.add_argument(
        "--project", required=True, metavar="PROJECT_ID", help=Messages.CLI_PROJECT_HELP
    )
    bind_options.add_argument(
        "--provider",
        required=True,
        metavar="PROVIDER",
        help=Messages.CLI_GOVERNANCE_PROVIDER_HELP,
    )
    bind_options.add_argument(
        "--external-repository",
        required=True,
        metavar="ID",
        help=Messages.CLI_GOVERNANCE_REPOSITORY_ID_HELP,
    )

    adopt = project_commands.add_parser(
        "adopt-existing",
        help=Messages.CLI_GOVERNANCE_ADOPT_EXISTING_HELP,
        add_help=False,
    )
    adopt_options = adopt.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_governance_shared_options(adopt_options)
    _add_governance_actor_options(adopt_options)
    adopt_options.add_argument(
        "--project", required=True, metavar="PROJECT_ID", help=Messages.CLI_PROJECT_HELP
    )
    adopt_options.add_argument(
        "--resource-type",
        required=True,
        choices=("ground-truth-set", "scm-change"),
        metavar="TYPE",
        help=Messages.CLI_GOVERNANCE_RESOURCE_TYPE_HELP,
    )
    adopt_options.add_argument(
        "--resource-id",
        required=True,
        metavar="ID",
        help=Messages.CLI_GOVERNANCE_RESOURCE_ID_HELP,
    )

    backup = governance_commands.add_parser(
        "backup",
        help=Messages.CLI_GOVERNANCE_BACKUP_HELP,
        add_help=False,
    )
    backup.add_argument_group(Messages.CLI_OPTIONS_TITLE).add_argument(
        "-h", "--help", action="help", help=Messages.CLI_HELP_HELP
    )
    backup_commands = backup.add_subparsers(dest="backup_command", required=True)
    verify = backup_commands.add_parser(
        "verify",
        help=Messages.CLI_GOVERNANCE_BACKUP_VERIFY_HELP,
        add_help=False,
    )
    verify_options = verify.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_governance_shared_options(verify_options)
    verify_options.add_argument(
        "--database",
        required=True,
        metavar="SQLITE_DB",
        help=Messages.CLI_GOVERNANCE_BACKUP_DATABASE_HELP,
    )
    verify_options.add_argument(
        "--project",
        required=True,
        metavar="PROJECT_ID",
        help=Messages.CLI_PROJECT_HELP,
    )
    verify_options.add_argument(
        "--actor",
        required=True,
        metavar="ACTOR_ID",
        help=Messages.CLI_GOVERNANCE_BACKUP_ACTOR_HELP,
    )
    verify_options.add_argument(
        "--output",
        required=True,
        metavar="JSON",
        help=Messages.CLI_OUTPUT_HELP,
    )


def _add_governance_shared_options(group: argparse._ArgumentGroup) -> None:
    """プロジェクト統制コマンドの共通パス引数を登録する."""
    group.add_argument("-h", "--help", action="help", help=Messages.CLI_HELP_HELP)
    group.add_argument(
        "--repository", default=".", metavar="PATH", help=Messages.CLI_REPOSITORY_HELP
    )
    group.add_argument("--config", metavar="YAML", help=Messages.CLI_CONFIG_HELP)


def _add_governance_actor_options(group: argparse._ArgumentGroup) -> None:
    """変更を伴う統制コマンドの監査引数を登録する."""
    group.add_argument(
        "--actor", required=True, metavar="ACTOR_ID", help=Messages.CLI_FEEDBACK_ACTOR_HELP
    )
    group.add_argument(
        "--reason", required=True, metavar="TEXT", help=Messages.CLI_GOVERNANCE_REASON_HELP
    )


def _add_scm_commands(commands: argparse._SubParsersAction[_ArgumentParser]) -> None:
    """SCM サブコマンドを登録する."""
    scm = commands.add_parser(
        "scm",
        help=Messages.CLI_SCM_HELP,
        description=Messages.CLI_SCM_DESCRIPTION,
        add_help=False,
    )
    scm.add_argument_group(Messages.CLI_OPTIONS_TITLE).add_argument(
        "-h", "--help", action="help", help=Messages.CLI_HELP_HELP
    )
    subcommands = scm.add_subparsers(dest="scm_command", required=True)
    review = subcommands.add_parser(
        "review",
        help=Messages.CLI_SCM_REVIEW_HELP,
        add_help=False,
    )
    options = review.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    options.add_argument("-h", "--help", action="help", help=Messages.CLI_HELP_HELP)
    options.add_argument(
        "--repository", default=".", metavar="PATH", help=Messages.CLI_REPOSITORY_HELP
    )
    options.add_argument("--config", metavar="YAML", help=Messages.CLI_CONFIG_HELP)
    options.add_argument(
        "--change",
        required=True,
        metavar="NUMBER",
        help=Messages.CLI_SCM_CHANGE_HELP,
    )
    options.add_argument(
        "--mode",
        choices=[item.value for item in ReviewMode],
        metavar="MODE",
        help=Messages.CLI_MODE_HELP,
    )
    options.add_argument("--output", metavar="JSON", help=Messages.CLI_OUTPUT_HELP)
    options.add_argument(
        "--publish",
        action="store_true",
        help=Messages.CLI_SCM_PUBLISH_HELP,
    )
    sync = subcommands.add_parser(
        "sync-feedback",
        help=Messages.CLI_SCM_SYNC_FEEDBACK_HELP,
        add_help=False,
    )
    sync_options = sync.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    sync_options.add_argument("-h", "--help", action="help", help=Messages.CLI_HELP_HELP)
    sync_options.add_argument(
        "--repository", default=".", metavar="PATH", help=Messages.CLI_REPOSITORY_HELP
    )
    sync_options.add_argument("--config", metavar="YAML", help=Messages.CLI_CONFIG_HELP)
    sync_options.add_argument(
        "--change",
        required=True,
        metavar="NUMBER",
        help=Messages.CLI_SCM_CHANGE_HELP,
    )
    sync_options.add_argument("--output", metavar="JSON", help=Messages.CLI_OUTPUT_HELP)


def _add_evaluation_command(commands: argparse._SubParsersAction[_ArgumentParser]) -> None:
    """``eval`` サブコマンドを登録する."""
    evaluation = commands.add_parser("eval", help=Messages.CLI_EVAL_HELP, add_help=False)
    options = evaluation.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_shared_options(options)
    options.add_argument("--arms", required=True, metavar="A,B,C", help=Messages.CLI_EVAL_ARMS_HELP)
    options.add_argument(
        "--split", required=True, choices=("development", "test"), help=Messages.CLI_EVAL_SPLIT_HELP
    )
    options.add_argument(
        "--set-version", required=True, type=int, metavar="VERSION", help=Messages.CLI_EVAL_SET_HELP
    )
    options.add_argument(
        "--repeat",
        required=True,
        type=int,
        choices=range(3, 101),
        help=Messages.CLI_EVAL_REPEAT_HELP,
    )


def _add_ground_truth_commands(
    commands: argparse._SubParsersAction[_ArgumentParser],
) -> None:
    """正解データ管理サブコマンドを登録する."""
    ground_truth = commands.add_parser(
        "ground-truth",
        help=Messages.CLI_GROUND_TRUTH_HELP,
        add_help=False,
    )
    subcommands = ground_truth.add_subparsers(dest="ground_truth_command", required=True)
    publish = subcommands.add_parser(
        "publish",
        help=Messages.CLI_GROUND_TRUTH_PUBLISH_HELP,
        add_help=False,
    )
    options = publish.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_shared_options(options)
    options.add_argument(
        "--input",
        required=True,
        metavar="JSON",
        help=Messages.CLI_GROUND_TRUTH_INPUT_HELP,
    )
    options.add_argument(
        "--actor",
        required=True,
        metavar="ACTOR_ID",
        help=Messages.CLI_GROUND_TRUTH_ACTOR_HELP,
    )


def _add_adjudication_commands(
    commands: argparse._SubParsersAction[_ArgumentParser],
) -> None:
    """人工裁定サブコマンドを登録する."""
    adjudication = commands.add_parser(
        "adjudication",
        help=Messages.CLI_ADJUDICATION_HELP,
        add_help=False,
    )
    subcommands = adjudication.add_subparsers(
        dest="adjudication_command",
        required=True,
    )
    export = subcommands.add_parser(
        "export",
        help=Messages.CLI_ADJUDICATION_EXPORT_HELP,
        add_help=False,
    )
    export_options = export.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_shared_options(export_options)
    export_options.add_argument(
        "--split",
        required=True,
        choices=("development", "test"),
        help=Messages.CLI_EVAL_SPLIT_HELP,
    )
    export_options.add_argument(
        "--set-version",
        required=True,
        type=int,
        metavar="VERSION",
        help=Messages.CLI_EVAL_SET_HELP,
    )
    export_options.add_argument("--output", metavar="JSON", help=Messages.CLI_OUTPUT_HELP)

    report = subcommands.add_parser(
        "report",
        help=Messages.CLI_ADJUDICATION_REPORT_HELP,
        add_help=False,
    )
    report_options = report.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_shared_options(report_options)
    report_options.add_argument(
        "--split",
        required=True,
        choices=("development", "test"),
        help=Messages.CLI_EVAL_SPLIT_HELP,
    )
    report_options.add_argument(
        "--set-version",
        required=True,
        type=int,
        metavar="VERSION",
        help=Messages.CLI_EVAL_SET_HELP,
    )

    resolve = subcommands.add_parser(
        "resolve",
        help=Messages.CLI_ADJUDICATION_RESOLVE_HELP,
        add_help=False,
    )
    resolve_options = resolve.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_shared_options(resolve_options)
    resolve_options.add_argument(
        "--token",
        required=True,
        help=Messages.CLI_ADJUDICATION_TOKEN_HELP,
    )
    resolve_options.add_argument(
        "--result",
        required=True,
        choices=("true-positive", "false-positive", "duplicate"),
        help=Messages.CLI_ADJUDICATION_RESULT_HELP,
    )
    resolve_options.add_argument("--issue-id", help=Messages.CLI_ADJUDICATION_ISSUE_HELP)
    resolve_options.add_argument(
        "--actor",
        required=True,
        help=Messages.CLI_FEEDBACK_ACTOR_HELP,
    )
    resolve_options.add_argument(
        "--reason",
        required=True,
        help=Messages.CLI_MEMORY_REASON_HELP,
    )


def _add_feedback_commands(
    commands: argparse._SubParsersAction[_ArgumentParser],
) -> None:
    """``feedback`` サブコマンドを登録する."""
    feedback = commands.add_parser(
        "feedback",
        help=Messages.CLI_FEEDBACK_HELP,
        description=Messages.CLI_FEEDBACK_DESCRIPTION,
        add_help=False,
    )
    feedback.add_argument_group(Messages.CLI_OPTIONS_TITLE).add_argument(
        "-h", "--help", action="help", help=Messages.CLI_HELP_HELP
    )
    feedback_commands = feedback.add_subparsers(dest="feedback_command", required=True)

    finding = feedback_commands.add_parser(
        "finding",
        help=Messages.CLI_FEEDBACK_FINDING_HELP,
        add_help=False,
    )
    finding_options = finding.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_shared_options(finding_options)
    finding_options.add_argument(
        "--run", required=True, metavar="RUN_ID", help=Messages.CLI_FEEDBACK_RUN_HELP
    )
    finding_options.add_argument(
        "--finding",
        required=True,
        metavar="FINDING_ID",
        help=Messages.CLI_FEEDBACK_FINDING_ID_HELP,
    )
    finding_options.add_argument(
        "--label",
        required=True,
        choices=[item.value for item in FeedbackLabel if not item.is_missed_issue],
        metavar="LABEL",
        help=Messages.CLI_FEEDBACK_LABEL_HELP,
    )
    finding_options.add_argument(
        "--reason", required=True, metavar="TEXT", help=Messages.CLI_FEEDBACK_REASON_HELP
    )
    finding_options.add_argument(
        "--actor", required=True, metavar="ACTOR_ID", help=Messages.CLI_FEEDBACK_ACTOR_HELP
    )

    missed = feedback_commands.add_parser(
        "missed-issue",
        help=Messages.CLI_FEEDBACK_MISSED_ISSUE_HELP,
        add_help=False,
    )
    missed_options = missed.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_shared_options(missed_options)
    missed_options.add_argument(
        "--run", required=True, metavar="RUN_ID", help=Messages.CLI_FEEDBACK_RUN_HELP
    )
    missed_options.add_argument(
        "--file", required=True, metavar="PATH", help=Messages.CLI_FEEDBACK_FILE_HELP
    )
    missed_options.add_argument(
        "--start-line", type=int, metavar="LINE", help=Messages.CLI_FEEDBACK_START_LINE_HELP
    )
    missed_options.add_argument("--symbol", metavar="NAME", help=Messages.CLI_FEEDBACK_SYMBOL_HELP)
    missed_options.add_argument(
        "--issue-type", required=True, metavar="TYPE", help=Messages.CLI_FEEDBACK_ISSUE_TYPE_HELP
    )
    missed_options.add_argument(
        "--severity",
        required=True,
        choices=[item.value for item in Severity],
        metavar="SEVERITY",
        help=Messages.CLI_FEEDBACK_SEVERITY_HELP,
    )
    missed_options.add_argument(
        "--description",
        required=True,
        metavar="TEXT",
        help=Messages.CLI_FEEDBACK_DESCRIPTION_HELP,
    )
    missed_options.add_argument(
        "--evidence", required=True, metavar="TEXT", help=Messages.CLI_FEEDBACK_EVIDENCE_HELP
    )
    missed_options.add_argument(
        "--actor", required=True, metavar="ACTOR_ID", help=Messages.CLI_FEEDBACK_ACTOR_HELP
    )


def _add_memory_commands(
    commands: argparse._SubParsersAction[_ArgumentParser],
) -> None:
    """``memory`` サブコマンドを登録する."""
    memory = commands.add_parser(
        "memory",
        help=Messages.CLI_MEMORY_HELP,
        description=Messages.CLI_MEMORY_DESCRIPTION,
        add_help=False,
    )
    memory.add_argument_group(Messages.CLI_OPTIONS_TITLE).add_argument(
        "-h", "--help", action="help", help=Messages.CLI_HELP_HELP
    )
    memory_commands = memory.add_subparsers(dest="memory_command", required=True)

    listing = memory_commands.add_parser("list", help=Messages.CLI_MEMORY_LIST_HELP, add_help=False)
    listing_options = listing.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_shared_options(listing_options)
    listing_options.add_argument("--project", metavar="PROJECT_ID", help=Messages.CLI_PROJECT_HELP)
    listing_options.add_argument(
        "--status",
        default=MemoryStatus.PENDING.value,
        choices=[item.value for item in MemoryStatus],
        metavar="STATUS",
        help=Messages.CLI_MEMORY_STATUS_HELP,
    )

    show = memory_commands.add_parser("show", help=Messages.CLI_MEMORY_SHOW_HELP, add_help=False)
    show_options = show.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_shared_options(show_options)
    show_options.add_argument("memory_id", metavar="MEMORY_ID", help=Messages.CLI_MEMORY_ID_HELP)

    approve = memory_commands.add_parser(
        "approve", help=Messages.CLI_MEMORY_APPROVE_HELP, add_help=False
    )
    approve_options = approve.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_shared_options(approve_options)
    approve_options.add_argument("memory_id", metavar="MEMORY_ID", help=Messages.CLI_MEMORY_ID_HELP)
    approve_options.add_argument(
        "--actor", required=True, metavar="ACTOR_ID", help=Messages.CLI_FEEDBACK_ACTOR_HELP
    )
    approve_options.add_argument(
        "--reason", required=True, metavar="TEXT", help=Messages.CLI_MEMORY_REASON_HELP
    )
    approve_options.add_argument(
        "--lock-version",
        required=True,
        type=int,
        metavar="VERSION",
        help=Messages.CLI_MEMORY_LOCK_VERSION_HELP,
    )
    approve_options.add_argument(
        "--scope-file", metavar="YAML", help=Messages.CLI_MEMORY_SCOPE_FILE_HELP
    )

    reject = memory_commands.add_parser(
        "reject", help=Messages.CLI_MEMORY_REJECT_HELP, add_help=False
    )
    reject_options = reject.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_shared_options(reject_options)
    reject_options.add_argument("memory_id", metavar="MEMORY_ID", help=Messages.CLI_MEMORY_ID_HELP)
    reject_options.add_argument(
        "--actor", required=True, metavar="ACTOR_ID", help=Messages.CLI_FEEDBACK_ACTOR_HELP
    )
    reject_options.add_argument(
        "--reason", required=True, metavar="TEXT", help=Messages.CLI_MEMORY_REASON_HELP
    )
    reject_options.add_argument(
        "--lock-version",
        required=True,
        type=int,
        metavar="VERSION",
        help=Messages.CLI_MEMORY_LOCK_VERSION_HELP,
    )

    _add_m5_memory_commands(memory_commands)


def _add_m5_memory_commands(
    commands: argparse._SubParsersAction[_ArgumentParser],
) -> None:
    """M5 のメモリ生命周期、競合解決、再検証コマンドを登録する."""
    for command_name, help_text in (
        ("archive", Messages.CLI_MEMORY_ARCHIVE_HELP),
        ("restore", Messages.CLI_MEMORY_RESTORE_HELP),
    ):
        parser = commands.add_parser(command_name, help=help_text, add_help=False)
        options = parser.add_argument_group(Messages.CLI_OPTIONS_TITLE)
        _add_shared_options(options)
        _add_memory_governance_target(options)

    supersede = commands.add_parser(
        "supersede", help=Messages.CLI_MEMORY_SUPERSEDE_HELP, add_help=False
    )
    supersede_options = supersede.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_shared_options(supersede_options)
    _add_memory_governance_target(supersede_options)
    supersede_options.add_argument(
        "--replacement",
        required=True,
        metavar="MEMORY_ID",
        help=Messages.CLI_MEMORY_REPLACEMENT_HELP,
    )

    conflict = commands.add_parser(
        "conflict", help=Messages.CLI_MEMORY_CONFLICT_HELP, add_help=False
    )
    conflict.add_argument_group(Messages.CLI_OPTIONS_TITLE).add_argument(
        "-h", "--help", action="help", help=Messages.CLI_HELP_HELP
    )
    conflict_commands = conflict.add_subparsers(dest="memory_action", required=True)
    conflict_list = conflict_commands.add_parser(
        "list", help=Messages.CLI_MEMORY_LIST_HELP, add_help=False
    )
    conflict_list_options = conflict_list.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_shared_options(conflict_list_options)
    conflict_list_options.add_argument(
        "--project", metavar="PROJECT_ID", help=Messages.CLI_PROJECT_HELP
    )
    conflict_list_options.add_argument(
        "--status",
        choices=[item.value for item in ConflictStatus],
        default=ConflictStatus.OPEN.value,
        help=Messages.CLI_MEMORY_CONFLICT_STATUS_HELP,
    )
    conflict_resolve = conflict_commands.add_parser(
        "resolve", help=Messages.CLI_MEMORY_CONFLICT_HELP, add_help=False
    )
    conflict_resolve_options = conflict_resolve.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_shared_options(conflict_resolve_options)
    conflict_resolve_options.add_argument(
        "conflict_id", metavar="CONFLICT_ID", help=Messages.CLI_MEMORY_CONFLICT_ID_HELP
    )
    conflict_resolve_options.add_argument(
        "--project", metavar="PROJECT_ID", help=Messages.CLI_PROJECT_HELP
    )
    conflict_resolve_options.add_argument(
        "--resolution",
        required=True,
        choices=[item.value for item in ConflictResolution],
        help=Messages.CLI_MEMORY_RESOLUTION_HELP,
    )
    _add_memory_audit_options(conflict_resolve_options, include_evidence=True)
    conflict_resolve_options.add_argument(
        "--scope-file", metavar="YAML", help=Messages.CLI_MEMORY_SCOPE_FILE_HELP
    )

    revalidation = commands.add_parser(
        "revalidation", help=Messages.CLI_MEMORY_REVALIDATION_HELP, add_help=False
    )
    revalidation.add_argument_group(Messages.CLI_OPTIONS_TITLE).add_argument(
        "-h", "--help", action="help", help=Messages.CLI_HELP_HELP
    )
    revalidation_commands = revalidation.add_subparsers(dest="memory_action", required=True)
    revalidation_list = revalidation_commands.add_parser(
        "list", help=Messages.CLI_MEMORY_LIST_HELP, add_help=False
    )
    revalidation_list_options = revalidation_list.add_argument_group(Messages.CLI_OPTIONS_TITLE)
    _add_shared_options(revalidation_list_options)
    revalidation_list_options.add_argument(
        "--project", metavar="PROJECT_ID", help=Messages.CLI_PROJECT_HELP
    )
    for action in ("confirm", "invalidate"):
        parser = revalidation_commands.add_parser(
            action, help=Messages.CLI_MEMORY_REVALIDATION_HELP, add_help=False
        )
        options = parser.add_argument_group(Messages.CLI_OPTIONS_TITLE)
        _add_shared_options(options)
        options.add_argument("memory_id", metavar="MEMORY_ID", help=Messages.CLI_MEMORY_ID_HELP)
        options.add_argument("--project", metavar="PROJECT_ID", help=Messages.CLI_PROJECT_HELP)
        _add_memory_audit_options(options, include_evidence=True)


def _add_memory_governance_target(group: argparse._ArgumentGroup) -> None:
    """単一メモリ統制コマンドの共通入力を追加する."""
    group.add_argument("memory_id", metavar="MEMORY_ID", help=Messages.CLI_MEMORY_ID_HELP)
    group.add_argument("--project", metavar="PROJECT_ID", help=Messages.CLI_PROJECT_HELP)
    _add_memory_audit_options(group, include_evidence=False)


def _add_memory_audit_options(
    group: argparse._ArgumentGroup,
    *,
    include_evidence: bool,
) -> None:
    """統制監査と楽観ロックの入力を追加する."""
    group.add_argument(
        "--actor", required=True, metavar="ACTOR_ID", help=Messages.CLI_FEEDBACK_ACTOR_HELP
    )
    group.add_argument(
        "--reason", required=True, metavar="TEXT", help=Messages.CLI_MEMORY_REASON_HELP
    )
    if include_evidence:
        group.add_argument(
            "--evidence",
            required=True,
            metavar="TEXT",
            help=Messages.CLI_MEMORY_EVIDENCE_HELP,
        )
    group.add_argument(
        "--lock-version",
        required=True,
        type=int,
        metavar="VERSION",
        help=Messages.CLI_MEMORY_LOCK_VERSION_HELP,
    )


def _add_shared_options(group: argparse._ArgumentGroup) -> None:
    """全 M2 サブコマンドで共通のオプションを登録する."""
    group.add_argument("-h", "--help", action="help", help=Messages.CLI_HELP_HELP)
    group.add_argument(
        "--repository", default=".", metavar="PATH", help=Messages.CLI_REPOSITORY_HELP
    )
    group.add_argument("--config", metavar="YAML", help=Messages.CLI_CONFIG_HELP)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI を実行し、安定した製品終了コードを返す."""
    try:
        arguments = build_parser().parse_args(argv)
        if arguments.command == "review":
            return execute_review_command(arguments)
        if arguments.command == "feedback":
            return execute_feedback_command(arguments)
        if arguments.command == "memory":
            return execute_memory_command(arguments)
        if arguments.command == "ground-truth":
            return execute_ground_truth_command(arguments)
        if arguments.command == "adjudication":
            return execute_adjudication_command(arguments)
        if arguments.command == "eval":
            return execute_evaluation_command(arguments)
        if arguments.command == "scm":
            return execute_scm_command(arguments)
        if arguments.command == "benchmark":
            return execute_benchmark(arguments)
        if arguments.command == "dashboard":
            return execute_web_command(arguments)
        if arguments.command == "governance":
            return execute_governance_command(arguments)
        return execute_rules_check_command(arguments)
    except (InvalidInputError, InvalidDiffError) as exc:
        write_error(type(exc).__name__, str(exc))
        return 1
    except LlmClientError as exc:
        write_error(type(exc).__name__, str(exc))
        return 2
    except InvalidLlmOutputError as exc:
        write_error(type(exc).__name__, str(exc))
        return 3
    except PolicyViolationError as exc:
        write_error(type(exc).__name__, str(exc))
        return 4
    except (DomainError, InfrastructureError, OSError) as exc:
        write_error(type(exc).__name__, str(exc))
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
