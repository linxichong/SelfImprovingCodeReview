"""SCM 変更要求を固定し、既存レビューと任意の公開を調停する."""

from __future__ import annotations

from pathlib import Path

from self_improving_code_review.application.config.settings import AppConfig
from self_improving_code_review.application.ports.diff_provider import DiffProvider, DiffSource
from self_improving_code_review.application.ports.scm_client import ScmClient
from self_improving_code_review.application.ports.scm_publication import (
    PublicationPublisher,
    ReviewExecutor,
)
from self_improving_code_review.application.ports.secret_scanner import SecretScanner
from self_improving_code_review.application.review.models import ReviewResult
from self_improving_code_review.application.scm.models import (
    PublicationPlan,
    PublicationResult,
    ScmReviewMetrics,
    ScmReviewOutcome,
)
from self_improving_code_review.application.scm.plan_publications import plan_publications
from self_improving_code_review.domain.common.enums import (
    DiffSourceKind,
    ReviewMode,
    RunStatus,
)
from self_improving_code_review.domain.common.errors import (
    InvalidInputError,
    PolicyViolationError,
)
from self_improving_code_review.domain.scm.models import (
    ChangeRef,
    PublicationKind,
    PublicationOperation,
    ScmChange,
    ScmChangeState,
)
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.models import ScanProfile, SourceKind, SourcePayload


class ScmReviewCoordinator:
    """SCM、ローカル Git、M3 レビューおよびコメント公開の境界を調停する."""

    def __init__(
        self,
        *,
        config: AppConfig,
        scm_client: ScmClient,
        diff_provider: DiffProvider,
        review_executor: ReviewExecutor,
        secret_scanner: SecretScanner,
        publisher: PublicationPublisher | None = None,
    ) -> None:
        """検証済み設定と外向きポートを受け取る."""
        self._config = config
        self._scm_client = scm_client
        self._diff_provider = diff_provider
        self._review_executor = review_executor
        self._secret_scanner = secret_scanner
        self._publisher = publisher

    def execute(
        self,
        repository: Path,
        ref: ChangeRef,
        *,
        mode: ReviewMode,
        config_path: Path | None,
        should_publish: bool,
        execution_nonce: str | None = None,
    ) -> ScmReviewOutcome:
        """変更要求を固定してレビューし、三重ゲート成立時だけ公開する."""
        self._validate_enabled(ref)
        actor = self._scm_client.get_authenticated_actor()
        if actor != self._config.scm.bot_actor_external_id:
            raise InvalidInputError(Messages.SCM_ACTOR_MISMATCH)
        change = self._scm_client.get_change(ref)
        self._validate_change(change)
        local_head = self._diff_provider.resolve_head_sha(repository)
        if local_head != change.base_sha:
            raise InvalidInputError(Messages.SCM_BASE_SHA_MISMATCH)
        review = self._review_executor.execute(
            repository,
            self._config,
            DiffSource(
                kind=DiffSourceKind.RANGE,
                base_revision=change.base_sha,
                head_revision=change.head_sha,
            ),
            mode,
            config_path=config_path,
            execution_nonce=execution_nonce,
        )
        plans = plan_publications(
            change,
            review,
            max_inline_comments=self._config.scm.max_inline_comments,
        )
        self._scan_publications(change, plans)
        if not should_publish or not self._config.scm.publish_enabled:
            if self._publisher is not None:
                self._publisher.save_plans(change, plans)
            return ScmReviewOutcome(
                change=change,
                review=review,
                plans=plans,
                metrics=self._metrics(plans, ()),
            )
        self._validate_publish_gate(review)
        latest_change = self._scm_client.get_change(ref)
        if latest_change.head_sha != change.head_sha:
            raise InvalidInputError(Messages.SCM_HEAD_SHA_CHANGED)
        if self._publisher is None:
            raise InvalidInputError(Messages.SCM_PUBLISHER_REQUIRED)
        publications = self._publisher.publish(change, plans)
        return ScmReviewOutcome(
            change=change,
            review=review,
            plans=plans,
            publications=publications,
            metrics=self._metrics(plans, publications),
        )

    def _metrics(
        self,
        plans: tuple[PublicationPlan, ...],
        publications: tuple[PublicationResult, ...],
    ) -> ScmReviewMetrics:
        """SCM API と公開処理を本文非保持の件数へ集約する."""
        api = self._scm_client.get_metrics()
        operation_counts = {
            operation: sum(item.operation is operation for item in publications)
            for operation in PublicationOperation
        }
        return ScmReviewMetrics(
            api_call_count=api.call_count,
            api_retry_count=api.retry_count,
            api_retry_wait_ms=api.retry_wait_ms,
            planned_inline_count=sum(plan.kind is PublicationKind.INLINE for plan in plans),
            planned_summary_count=sum(plan.kind is PublicationKind.SUMMARY for plan in plans),
            created_count=operation_counts[PublicationOperation.CREATED],
            updated_count=operation_counts[PublicationOperation.UPDATED],
            reused_count=operation_counts[PublicationOperation.REUSED],
            recovered_count=operation_counts[PublicationOperation.RECOVERED],
            failed_count=operation_counts[PublicationOperation.FAILED],
            stale_count=operation_counts[PublicationOperation.STALE],
            blocked_count=operation_counts[PublicationOperation.BLOCKED],
            head_change_abort_count=sum(
                item.safe_error_code == "HEAD_CHANGED" for item in publications
            ),
        )

    def _validate_enabled(self, ref: ChangeRef) -> None:
        """設定と要求対象が同じ有効な SCM リポジトリを示すことを検証する."""
        if not self._config.scm.enabled:
            raise InvalidInputError(Messages.SCM_DISABLED)
        if ref.repository_external_id != self._config.scm.repository_external_id:
            raise InvalidInputError(Messages.SCM_REPOSITORY_MISMATCH)

    def _validate_change(self, change: ScmChange) -> None:
        """閉じた変更要求および許可されていない下書きを拒否する."""
        if change.state is not ScmChangeState.OPEN:
            raise InvalidInputError(Messages.SCM_CHANGE_NOT_OPEN)
        if change.is_draft and not self._config.scm.include_drafts:
            raise InvalidInputError(Messages.SCM_DRAFT_NOT_ALLOWED)

    def _scan_publications(
        self,
        change: ScmChange,
        plans: tuple[PublicationPlan, ...],
    ) -> None:
        """外部送信直前の全コメント本文を厳格に検査する."""
        for sequence, plan in enumerate(plans, start=1):
            body = plan.request.body
            result = self._secret_scanner.scan(
                SourcePayload(
                    kind=SourceKind.SCM_PUBLICATION,
                    label=f"scm-publication-{sequence}",
                    text=body,
                ),
                ScanProfile.STRICT,
            )
            if not result.is_passed:
                if self._publisher is not None:
                    self._publisher.block_plans(change, plans)
                raise PolicyViolationError(Messages.SCM_PUBLICATION_SECRET_DETECTED)

    def _validate_publish_gate(self, review: ReviewResult) -> None:
        """設定、CLI 指示およびレビュー成功を公開前に検証する."""
        if review.run.status is not RunStatus.SUCCESS:
            raise InvalidInputError(Messages.SCM_REVIEW_NOT_SUCCESSFUL)
