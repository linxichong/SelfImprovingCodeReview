"""M2 メモリ統制コマンドの構成起点.

承認と却下は統制操作である. 非対話式実行でも実行者を特定できない場合は
失敗させ、適用範囲を変更する承認は新しいバージョンを作る.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import yaml

from self_improving_code_review.application.config.settings import AppConfig, load_config
from self_improving_code_review.application.governance import (
    ManageMemoryLifecycle,
    RevalidateMemory,
)
from self_improving_code_review.application.memory.change_status import (
    ApproveMemory,
    MemoryStatusChange,
    RejectMemory,
)
from self_improving_code_review.cli.output import write_document
from self_improving_code_review.domain.common.enums import (
    ConflictResolution,
    ConflictStatus,
    MemoryStatus,
)
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import MemoryId, ProjectId
from self_improving_code_review.domain.governance.memory_models import (
    MemoryConflict,
    MemoryGovernanceResult,
    MemoryRevalidation,
)
from self_improving_code_review.domain.memory.models import MemoryContent, MemoryRecord
from self_improving_code_review.domain.memory.scope import MemoryScope
from self_improving_code_review.infrastructure.persistence import SqliteUnitOfWorkFactory
from self_improving_code_review.infrastructure.persistence.memory_governance_repository import (
    SqliteMemoryGovernanceRepository,
)
from self_improving_code_review.infrastructure.persistence.short_transaction import (
    ShortTransactionMemoryRepository,
)
from self_improving_code_review.infrastructure.system import SystemClock
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.authorization import AllowListActorAuthorizer

__all__ = ["execute_memory_command"]

_SCOPE_GROUPS: tuple[str, ...] = (
    "languages",
    "frameworks",
    "paths",
    "symbols",
    "keywords",
    "risk_tags",
)


def execute_memory_command(arguments: Namespace) -> int:
    """CLI 引数からメモリ統制操作を一回実行する.

    Args:
        arguments: argparse が検証したメモリコマンドの引数。

    Returns:
        成功は 0。競合を検出した承認は 2。

    Raises:
        AuthorizationError: 実行者に承認権限がない場合。
        InvalidInputError: メモリが存在しない、または入力が不正な場合。
        ConcurrencyConflictError: 同じバージョンから並行更新された場合。
        InvalidStateTransitionError: 現在状態から要求した遷移ができない場合。
    """
    repository = Path(arguments.repository).resolve()
    config = load_config(Path(arguments.config) if arguments.config else None)
    database_path = _database_path(repository, config)
    if arguments.memory_command in {"archive", "restore", "supersede", "conflict", "revalidation"}:
        return _execute_m5_memory_command(database_path, config, arguments)

    factory = SqliteUnitOfWorkFactory(database_path)
    memory_repository = ShortTransactionMemoryRepository(factory)
    clock = SystemClock()
    authorizer = AllowListActorAuthorizer(
        maintainers=config.memory.maintainers,
        is_production=config.memory.is_production,
    )

    if arguments.memory_command == "list":
        return _list_memories(memory_repository, config, arguments)
    if arguments.memory_command == "show":
        return _show_memory(memory_repository, arguments)

    if arguments.memory_command == "approve":
        change = ApproveMemory(
            memory_repository=memory_repository,
            authorizer=authorizer,
            clock=clock,
        ).execute(
            MemoryId(arguments.memory_id),
            actor_id=arguments.actor,
            reason=arguments.reason,
            expected_lock_version=arguments.lock_version,
            narrowed_scope=_load_scope(arguments.scope_file),
        )
    else:
        change = RejectMemory(
            memory_repository=memory_repository,
            authorizer=authorizer,
            clock=clock,
        ).execute(
            MemoryId(arguments.memory_id),
            actor_id=arguments.actor,
            reason=arguments.reason,
            expected_lock_version=arguments.lock_version,
        )

    if change.status is MemoryStatus.CONFLICT and config.governance.memory_lifecycle_enabled:
        found = memory_repository.find(change.memory_id)
        if found is None:
            raise InvalidInputError(Messages.memory_not_found(str(change.memory_id)))
        SqliteMemoryGovernanceRepository(
            database_path,
            busy_timeout_ms=config.governance.sqlite_busy_timeout_ms,
        ).record_detected_conflicts(
            found[0].project_id,
            change.memory_id,
            detected_at=clock.now_utc(),
        )
    _write_change(change)
    return 2 if change.status is MemoryStatus.CONFLICT else 0


def _execute_m5_memory_command(
    database_path: Path,
    config: AppConfig,
    arguments: Namespace,
) -> int:
    """有効化済み M5 メモリ統制コマンドを実行する."""
    if not (config.governance.enabled and config.governance.memory_lifecycle_enabled):
        raise InvalidInputError(Messages.CLI_MEMORY_LIFECYCLE_DISABLED)
    repository = SqliteMemoryGovernanceRepository(
        database_path,
        busy_timeout_ms=config.governance.sqlite_busy_timeout_ms,
    )
    authorizer = AllowListActorAuthorizer(
        maintainers=config.memory.maintainers,
        is_production=config.memory.is_production,
    )
    clock = SystemClock()
    lifecycle = ManageMemoryLifecycle(repository, authorizer, clock)
    revalidation = RevalidateMemory(
        repository,
        authorizer,
        clock,
        default_days=config.governance.revalidation_default_days,
    )
    project_id = ProjectId(arguments.project or config.project_id)

    if arguments.memory_command == "archive":
        result = lifecycle.archive(
            project_id,
            MemoryId(arguments.memory_id),
            expected_lock_version=arguments.lock_version,
            actor_id=arguments.actor,
            reason=arguments.reason,
        )
        _write_governance_result(result)
        return 0
    if arguments.memory_command == "restore":
        result = lifecycle.restore(
            project_id,
            MemoryId(arguments.memory_id),
            expected_lock_version=arguments.lock_version,
            actor_id=arguments.actor,
            reason=arguments.reason,
        )
        _write_governance_result(result)
        return 0
    if arguments.memory_command == "supersede":
        result = lifecycle.supersede(
            project_id,
            MemoryId(arguments.memory_id),
            MemoryId(arguments.replacement),
            expected_lock_version=arguments.lock_version,
            actor_id=arguments.actor,
            reason=arguments.reason,
        )
        _write_governance_result(result)
        return 0
    if arguments.memory_command == "conflict":
        return _execute_conflict_command(lifecycle, project_id, arguments)
    return _execute_revalidation_command(revalidation, project_id, arguments)


def _execute_conflict_command(
    lifecycle: ManageMemoryLifecycle,
    project_id: ProjectId,
    arguments: Namespace,
) -> int:
    """競合一覧または競合解決を実行する."""
    if arguments.memory_action == "list":
        conflicts = lifecycle.list_conflicts(project_id, ConflictStatus(arguments.status))
        write_document(
            {
                "schema_version": 2,
                "project_id": str(project_id),
                "conflicts": [_conflict_document(item) for item in conflicts],
            },
            None,
        )
        return 0
    conflict, result = lifecycle.resolve_conflict(
        project_id,
        arguments.conflict_id,
        ConflictResolution(arguments.resolution),
        expected_lock_version=arguments.lock_version,
        actor_id=arguments.actor,
        reason=arguments.reason,
        evidence=arguments.evidence,
        narrowed_scope=_load_scope(arguments.scope_file),
    )
    write_document(
        {
            "schema_version": 2,
            "conflict": _conflict_document(conflict),
            "memory": _governance_result_document(result),
        },
        None,
    )
    return 0


def _execute_revalidation_command(
    revalidation: RevalidateMemory,
    project_id: ProjectId,
    arguments: Namespace,
) -> int:
    """期限到来一覧、再確認または無効化を実行する."""
    if arguments.memory_action == "list":
        due = revalidation.list_due(project_id)
        write_document(
            {
                "schema_version": 2,
                "project_id": str(project_id),
                "revalidations": [_revalidation_document(item) for item in due],
            },
            None,
        )
        return 0
    operation = (
        revalidation.confirm if arguments.memory_action == "confirm" else revalidation.invalidate
    )
    result = operation(
        project_id,
        MemoryId(arguments.memory_id),
        expected_lock_version=arguments.lock_version,
        actor_id=arguments.actor,
        reason=arguments.reason,
        evidence=arguments.evidence,
    )
    _write_governance_result(result)
    return 0


def _write_governance_result(result: MemoryGovernanceResult) -> None:
    """メモリ統制結果を安全な版付き文書として出力する."""
    write_document(
        {"schema_version": 2, **_governance_result_document(result)},
        None,
    )


def _governance_result_document(result: MemoryGovernanceResult) -> dict[str, object]:
    """メモリ統制結果を秘密を含まない文書へ変換する."""
    return {
        "memory_id": str(result.memory_id),
        "version": result.version.value,
        "status": result.status.value,
        "lock_version": result.lock_version,
        "content_hash": result.content_hash,
    }


def _conflict_document(conflict: MemoryConflict) -> dict[str, object]:
    """競合スナップショットを安全な文書へ変換する."""
    return {
        "conflict_id": conflict.conflict_id,
        "left_memory_id": str(conflict.left_memory_id),
        "left_version": conflict.left_version.value,
        "left_content_hash": conflict.left_content_hash,
        "right_memory_id": str(conflict.right_memory_id),
        "right_version": conflict.right_version.value,
        "right_content_hash": conflict.right_content_hash,
        "reason_code": conflict.reason_code,
        "status": conflict.status.value,
        "lock_version": conflict.lock_version,
        "detected_at": conflict.detected_at.isoformat(),
        "resolution": conflict.resolution.value if conflict.resolution else None,
        "resolved_at": (conflict.resolved_at.isoformat() if conflict.resolved_at else None),
    }


def _revalidation_document(item: MemoryRevalidation) -> dict[str, object]:
    """再検証記録を安全な文書へ変換する."""
    return {
        "revalidation_id": item.revalidation_id,
        "memory_id": str(item.memory_id),
        "source_version": item.source_version.value,
        "status": item.status.value,
        "due_at": item.due_at.isoformat(),
        "created_at": item.created_at.isoformat(),
    }


def _list_memories(
    memory_repository: ShortTransactionMemoryRepository,
    config: AppConfig,
    arguments: Namespace,
) -> int:
    """指定した状態のメモリを一覧表示する."""
    project_id = ProjectId(arguments.project or config.project_id)
    memories = memory_repository.list_by_status(project_id, MemoryStatus(arguments.status))
    write_document(
        {
            "schema_version": 1,
            "project_id": str(project_id),
            "status": arguments.status,
            "memories": [_summary_document(record, content) for record, content in memories],
        },
        None,
    )
    return 0


def _show_memory(
    memory_repository: ShortTransactionMemoryRepository,
    arguments: Namespace,
) -> int:
    """メモリ一件の現在バージョンを表示する.

    Raises:
        InvalidInputError: メモリが存在しない場合。
    """
    found = memory_repository.find(MemoryId(arguments.memory_id))
    if found is None:
        raise InvalidInputError(Messages.memory_not_found(arguments.memory_id))
    record, content = found
    write_document(
        {
            "schema_version": 1,
            **_summary_document(record, content),
            "fact": content.fact,
            "review_instruction": content.review_instruction,
            "evidence_summary": content.evidence_summary,
            "scope": content.to_document()["scope"],
        },
        None,
    )
    return 0


def _summary_document(record: MemoryRecord, content: MemoryContent) -> dict[str, object]:
    """一覧と詳細で共有するメモリ要約を返す."""
    return {
        "memory_id": str(record.memory_id),
        "version": record.current_version.value,
        "status": record.current_status.value,
        "lock_version": record.lock_version,
        "category": content.category.value,
        "polarity": content.polarity.value,
        "confidence": content.confidence,
        "content_hash": content.content_hash,
        "updated_at": record.updated_at.isoformat(),
    }


def _write_change(change: MemoryStatusChange) -> None:
    """状態遷移の結果を標準出力へ書き込む."""
    write_document(
        {
            "schema_version": 1,
            "memory_id": str(change.memory_id),
            "version": change.version.value,
            "status": change.status.value,
            "conflict_reason_codes": list(change.conflict_reason_codes),
        },
        None,
    )


def _load_scope(scope_file: str | None) -> MemoryScope | None:
    """適用範囲 YAML を読み込み、縮小後の適用範囲を返す.

    Raises:
        InvalidInputError: ファイルを読み取れない、または形式が不正な場合。
    """
    if scope_file is None:
        return None
    path = Path(scope_file)
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise InvalidInputError(Messages.invalid_memory_scope_file(scope_file)) from exc
    if not isinstance(document, dict) or set(document) - set(_SCOPE_GROUPS):
        raise InvalidInputError(Messages.invalid_memory_scope_file(scope_file))
    try:
        return MemoryScope(
            languages=_group(document, "languages"),
            frameworks=_group(document, "frameworks"),
            paths=_group(document, "paths"),
            symbols=_group(document, "symbols"),
            keywords=_group(document, "keywords"),
            risk_tags=_group(document, "risk_tags"),
        )
    except InvalidInputError as exc:
        raise InvalidInputError(Messages.invalid_memory_scope_file(scope_file)) from exc


def _group(document: dict[str, object], name: str) -> tuple[str, ...]:
    """適用範囲グループを空白除去済みの文字列タプルとして返す.

    Raises:
        InvalidInputError: グループが文字列配列ではない場合。
    """
    values = document.get(name, [])
    if not isinstance(values, list):
        message = Messages.memory_scope_not_array(name)
        raise InvalidInputError(message)
    return tuple(str(value).strip() for value in values if str(value).strip())


def _database_path(repository: Path, config: AppConfig) -> Path:
    """設定されたデータベースパスを解決し、リポジトリ内に制限する.

    Raises:
        InvalidInputError: パスがリポジトリ外を指す場合。
    """
    configured = config.database.path
    candidate = (
        configured.resolve() if configured.is_absolute() else (repository / configured).resolve()
    )
    if not candidate.is_relative_to(repository):
        raise InvalidInputError(
            Messages.configured_path_outside_repository(Messages.DATABASE_PATH_LABEL)
        )
    return candidate
