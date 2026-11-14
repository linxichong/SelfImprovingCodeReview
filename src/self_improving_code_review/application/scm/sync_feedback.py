"""SCM の明示コメントを一度だけ M2 フィードバックへ同期する."""

from __future__ import annotations

from self_improving_code_review.application.config.settings import AppConfig
from self_improving_code_review.application.ports.scm_client import ScmClient
from self_improving_code_review.application.ports.scm_feedback import (
    ScmFeedbackProcessor,
    ScmFeedbackRepository,
)
from self_improving_code_review.application.scm.feedback_parser import parse_feedback_command
from self_improving_code_review.application.scm.models import (
    ParsedFeedbackCommand,
    ScmFeedbackEventRecord,
    ScmFeedbackSyncOutcome,
    ScmFeedbackTarget,
)
from self_improving_code_review.domain.common.errors import DomainError, InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.scm.models import ChangeRef, CommentEvent
from self_improving_code_review.messages import Messages


class SyncScmFeedback:
    """SCM イベントを出版物へ照合し、許可済み実行者の明示評価だけを処理する."""

    def __init__(
        self,
        *,
        config: AppConfig,
        scm_client: ScmClient,
        repository: ScmFeedbackRepository,
        processor: ScmFeedbackProcessor,
    ) -> None:
        """設定、SCM、監査リポジトリおよび M2 処理を保持する."""
        self._config = config
        self._scm_client = scm_client
        self._repository = repository
        self._processor = processor

    def execute(self, ref: ChangeRef) -> ScmFeedbackSyncOutcome:
        """保存済みカーソル以降の一頁を取得し、イベントを重複なく処理する."""
        self._validate_enabled(ref)
        actor = self._scm_client.get_authenticated_actor()
        if actor != self._config.scm.bot_actor_external_id:
            raise InvalidInputError(Messages.SCM_ACTOR_MISMATCH)
        cursor = self._repository.get_cursor(ref)
        page = self._scm_client.list_comment_events(ref, cursor=cursor)
        processed = 0
        ignored = 0
        failed = 0
        duplicate = 0
        checkpoint = cursor
        for event in page.events:
            event_cursor = event.updated_at.isoformat()
            if self._repository.has_event(ref.provider.value, event.event_external_id):
                duplicate += 1
                self._repository.save_cursor(ref, event_cursor)
                checkpoint = event_cursor
                continue
            target = self._target(ref, event)
            if target is None:
                ignored += 1
                self._repository.save_cursor(ref, event_cursor)
                checkpoint = event_cursor
                continue
            status, command, actor_id, feedback_id, error_code = self._process_event(
                event,
                target,
            )
            if status == "processed":
                processed += 1
            elif status == "ignored":
                ignored += 1
            else:
                failed += 1
            self._repository.save_event_and_cursor(
                ref,
                ScmFeedbackEventRecord(
                    event_external_id=event.event_external_id,
                    target=target,
                    external_comment_id=event.comment_external_id,
                    actor_id=actor_id,
                    label=command.label if command is not None else None,
                    payload_hash=hash_text(event.body),
                    mapped_feedback_id=feedback_id,
                    status=status,
                    safe_error_code=error_code,
                ),
                event_cursor,
            )
            checkpoint = event_cursor
        if page.next_cursor is not None and page.next_cursor != checkpoint:
            self._repository.save_cursor(ref, page.next_cursor)
            checkpoint = page.next_cursor
        return ScmFeedbackSyncOutcome(
            fetched_count=len(page.events),
            processed_count=processed,
            ignored_count=ignored,
            failed_count=failed,
            duplicate_count=duplicate,
            next_cursor=checkpoint,
        )

    def _validate_enabled(self, ref: ChangeRef) -> None:
        """SCM とフィードバック同期の明示許可および対象を検証する."""
        if not self._config.scm.enabled:
            raise InvalidInputError(Messages.SCM_DISABLED)
        if not self._config.scm.feedback_sync_enabled:
            raise InvalidInputError(Messages.SCM_FEEDBACK_SYNC_DISABLED)
        if ref.repository_external_id != self._config.scm.repository_external_id:
            raise InvalidInputError(Messages.SCM_REPOSITORY_MISMATCH)

    def _target(self, ref: ChangeRef, event: CommentEvent) -> ScmFeedbackTarget | None:
        """返信先が本システムの公開済み行コメントなら内部指摘を返す."""
        if event.target_comment_external_id is None:
            return None
        return self._repository.find_target(ref, event.target_comment_external_id)

    def _process_event(
        self,
        event: CommentEvent,
        target: ScmFeedbackTarget,
    ) -> tuple[str, ParsedFeedbackCommand | None, str | None, str | None, str | None]:
        """単一イベントを解析・実行し、安全な監査項目を返す."""
        if event.actor_external_id == self._config.scm.bot_actor_external_id:
            return "ignored", None, None, None, "SELF_EVENT"
        actor_id = self._config.scm.feedback_actor_mapping.get(event.actor_external_id)
        if actor_id is None or not actor_id.strip():
            return "ignored", None, None, None, "UNKNOWN_ACTOR"
        try:
            command = parse_feedback_command(event.body)
        except InvalidInputError:
            return "ignored", None, actor_id, None, "INVALID_COMMAND"
        try:
            outcome = self._processor.process(target, command, actor_id)
        except DomainError as exc:
            return "failed", command, actor_id, None, type(exc).__name__
        return "processed", command, actor_id, str(outcome.feedback_id), outcome.safe_error_code
