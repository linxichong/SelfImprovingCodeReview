"""フィードバック保存ユースケース.

保存と候補抽出は単一トランザクションにしない. 検証と保存の確定までを担い、
LLM 呼び出しを伴う候補抽出は別ユースケースへ委譲する.
"""

from __future__ import annotations

from self_improving_code_review.application.feedback.models import (
    FeedbackOutcome,
    FindingFeedbackInput,
    MissedIssueInput,
)
from self_improving_code_review.application.ports.authorization import ActorAuthorizer
from self_improving_code_review.application.ports.clock import Clock, IdGenerator
from self_improving_code_review.application.ports.memory_persistence import FeedbackRepository
from self_improving_code_review.application.ports.revision_content import RevisionContentReader
from self_improving_code_review.domain.common.enums import FeedbackLabel, Permission
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.feedback.models import FeedbackRecord, IssueLocation
from self_improving_code_review.domain.feedback.validators import (
    build_finding_feedback_key,
    build_missed_issue_key,
    normalize_free_text,
)
from self_improving_code_review.messages import Messages

__all__ = ["FEEDBACK_RECORDED", "RecordFindingFeedback", "RecordMissedIssue"]

FEEDBACK_RECORDED = "RECORDED"


class RecordFindingFeedback:
    """既存指摘への評価を追記する."""

    def __init__(
        self,
        *,
        feedback_repository: FeedbackRepository,
        authorizer: ActorAuthorizer,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        """リポジトリ、認可処理、時刻、識別子生成処理を保持する.

        Args:
            feedback_repository: フィードバック永続化処理.
            authorizer: 実行者権限の検証処理.
            clock: UTC 時刻の取得処理.
            id_generator: フィードバック識別子の生成処理.
        """
        self._repository = feedback_repository
        self._authorizer = authorizer
        self._clock = clock
        self._id_generator = id_generator

    def execute(self, request: FindingFeedbackInput) -> FeedbackOutcome:
        """評価を検証して保存する.

        Args:
            request: 実行、指摘、ラベル、理由を含む評価入力.

        Returns:
            保存済みフィードバックの識別子と状態.

        Raises:
            AuthorizationError: 実行者に記録権限がない場合.
            InvalidInputError: ラベルが見逃し専用、理由が不正、または
                指摘が実行に属さない場合.
        """
        self._authorizer.require(request.actor_id, Permission.RECORD_FEEDBACK)
        if request.label.is_missed_issue:
            message = Messages.FINDING_FEEDBACK_MISSED_ISSUE_FORBIDDEN
            raise InvalidInputError(message)
        reason = normalize_free_text("reason", request.reason)
        if not self._repository.exists_finding(request.run_id, str(request.finding_id)):
            message = f"{Messages.FINDING_NOT_IN_RUN}: {request.finding_id}"
            raise InvalidInputError(message)

        key = build_finding_feedback_key(
            run_id=request.run_id,
            finding_id=request.finding_id,
            actor_id=request.actor_id.strip(),
            label=request.label,
            reason=reason,
        )
        existing = self._repository.find_by_idempotency_key(key)
        if existing is not None:
            return FeedbackOutcome(
                feedback_id=existing.feedback_id,
                feedback_status=FEEDBACK_RECORDED,
                is_duplicate=True,
            )

        record = FeedbackRecord(
            feedback_id=self._id_generator.new_feedback_id(),
            run_id=request.run_id,
            label=request.label,
            reason=reason,
            actor_id=request.actor_id.strip(),
            created_at=self._clock.now_utc(),
            idempotency_key=key,
            finding_id=request.finding_id,
        )
        self._repository.save(record)
        return FeedbackOutcome(
            feedback_id=record.feedback_id,
            feedback_status=FEEDBACK_RECORDED,
        )


class RecordMissedIssue:
    """見逃し問題の報告を追記する."""

    def __init__(
        self,
        *,
        feedback_repository: FeedbackRepository,
        authorizer: ActorAuthorizer,
        clock: Clock,
        id_generator: IdGenerator,
        revision_reader: RevisionContentReader,
    ) -> None:
        """リポジトリ、認可処理、時刻、識別子生成処理を保持する."""
        self._repository = feedback_repository
        self._authorizer = authorizer
        self._clock = clock
        self._id_generator = id_generator
        self._revision_reader = revision_reader

    def execute(self, request: MissedIssueInput) -> FeedbackOutcome:
        """見逃し報告を検証して保存する.

        Args:
            request: 実行、ファイル、位置、問題種別、根拠を含む報告入力.

        Returns:
            保存済みフィードバックの識別子と状態.

        Raises:
            AuthorizationError: 実行者に記録権限がない場合.
            InvalidInputError: 位置を特定できない、または自由記述が不正な場合.
        """
        self._authorizer.require(request.actor_id, Permission.RECORD_FEEDBACK)
        issue_type = normalize_free_text("issue_type", request.issue_type)
        description = normalize_free_text("description", request.description)
        evidence = normalize_free_text("evidence", request.evidence)
        symbol = (
            normalize_free_text("symbol", request.symbol) if request.symbol is not None else None
        )
        location = IssueLocation(
            file=request.file,
            start_line=request.start_line,
            symbol=symbol,
        )
        run_context = self._repository.find_run_context(request.run_id)
        if run_context is None:
            raise InvalidInputError(Messages.FEEDBACK_RUN_NOT_FOUND)
        if run_context.source_revision is None:
            raise InvalidInputError(Messages.FEEDBACK_REVISION_REQUIRED)
        source_text = self._revision_reader.read_text(run_context.source_revision, request.file)
        if source_text is None:
            raise InvalidInputError(Messages.MISSED_ISSUE_FILE_NOT_FOUND)
        source_lines = source_text.splitlines()
        if location.start_line is not None and location.start_line > len(source_lines):
            raise InvalidInputError(Messages.MISSED_ISSUE_LINE_OUTSIDE_FILE)
        if location.symbol is not None and location.symbol not in source_text:
            raise InvalidInputError(Messages.MISSED_ISSUE_SYMBOL_NOT_FOUND)
        if location.start_line is not None and location.symbol is not None:
            start = max(location.start_line - 11, 0)
            end = min(location.start_line + 10, len(source_lines))
            if not any(location.symbol in line for line in source_lines[start:end]):
                raise InvalidInputError(Messages.MISSED_ISSUE_LOCATION_MISMATCH)

        key = build_missed_issue_key(
            run_id=request.run_id,
            location=location,
            issue_type=issue_type,
            evidence=evidence,
        )
        existing = self._repository.find_by_idempotency_key(key)
        if existing is not None:
            return FeedbackOutcome(
                feedback_id=existing.feedback_id,
                feedback_status=FEEDBACK_RECORDED,
                is_duplicate=True,
            )

        record = FeedbackRecord(
            feedback_id=self._id_generator.new_feedback_id(),
            run_id=request.run_id,
            label=FeedbackLabel.MISSED_ISSUE,
            reason=description,
            actor_id=request.actor_id.strip(),
            created_at=self._clock.now_utc(),
            idempotency_key=key,
            location=location,
            issue_type=issue_type,
            severity=request.severity,
            description=description,
            evidence=evidence,
        )
        self._repository.save(record)
        return FeedbackOutcome(
            feedback_id=record.feedback_id,
            feedback_status=FEEDBACK_RECORDED,
        )
