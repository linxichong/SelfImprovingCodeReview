"""単一担当のレビューを実行し、永続化前の結果を返す."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from threading import Event

from self_improving_code_review.application.ports.context_source import (
    ContextSource,
    ContextSourceFactory,
    SourceFingerprint,
)
from self_improving_code_review.application.ports.llm_client import (
    LlmClient,
    LlmRequest,
    LlmResult,
)
from self_improving_code_review.application.ports.secret_scanner import SecretScanner
from self_improving_code_review.application.ports.telemetry import Telemetry
from self_improving_code_review.application.ports.unit_of_work import (
    LlmCallRecord,
)
from self_improving_code_review.application.review.bundle_review_models import (
    BundleReview,
    BundleTask,
)
from self_improving_code_review.application.review.bundle_review_outcomes import (
    add_usage,
    cancelled_review,
    outcome_from_records,
    review_error_code,
)
from self_improving_code_review.application.review.context_tools import (
    ContextToolCoordinator,
    ContextToolExecutor,
    ContextToolOutcome,
    ContextToolSession,
)
from self_improving_code_review.application.review.models import (
    BundleOutcome,
    ReviewRequest,
)
from self_improving_code_review.application.review.reflect_findings import FindingReflector
from self_improving_code_review.application.review.response_parser import parse_review_response
from self_improving_code_review.application.review.review_agent import ReviewAgentLoop
from self_improving_code_review.application.review.review_manager import ReviewManager
from self_improving_code_review.application.review.review_source_mapping import (
    map_secret_findings,
)
from self_improving_code_review.domain.change.models import ChangedFile
from self_improving_code_review.domain.common.enums import (
    BundleStatus,
)
from self_improving_code_review.domain.common.errors import (
    InvalidLlmOutputError,
    LlmClientError,
    PolicyViolationError,
    ReflectionBudgetExceededError,
)
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.common.values import RunId
from self_improving_code_review.domain.finding.models import Finding
from self_improving_code_review.domain.rule.models import RuleSnapshot
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.models import (
    ScannedPayload,
    ScanResult,
    SourceKind,
    SourcePayload,
)
from self_improving_code_review.security.path_policy import PathPolicy

_AGENT_RESULT_MISSING = "agent result must exist when not blocked"
_REFLECTION_BUDGET_EXCEEDED_CODE = "REFLECTION_BUDGET_EXCEEDED"


class BundleReviewExecutor:
    """LLM、限定ツール、自己検証を調停し、保存と並列調整を呼出元へ委ねる."""

    def __init__(
        self,
        *,
        llm_client: LlmClient,
        secret_scanner: SecretScanner,
        telemetry: Telemetry,
        manager: ReviewManager,
        agent_loop: ReviewAgentLoop,
        path_policy: PathPolicy,
        context_source_factory: ContextSourceFactory | None,
        context_tool_executor_factory: Callable[[tuple[ChangedFile, ...]], ContextToolExecutor]
        | None,
        finding_reflector: FindingReflector | None,
        generate: Callable[[LlmRequest], LlmResult],
        scan: Callable[[SourcePayload], tuple[ScanResult, ScannedPayload | None]],
    ) -> None:
        """構成済み依存と送信境界の処理を受け取り、DB依存を持たず実行する."""
        self._llm_client = llm_client
        self._secret_scanner = secret_scanner
        self._telemetry = telemetry
        self._manager = manager
        self._agent_loop = agent_loop
        self._path_policy = path_policy
        self._context_source_factory = context_source_factory
        self._context_tool_executor_factory = context_tool_executor_factory
        self._finding_reflector = finding_reflector
        self._generate_with_repair = generate
        self._scan_and_seal = scan

    def execute(
        self,
        task: BundleTask,
        *,
        request: ReviewRequest,
        run_id: RunId,
        snapshot: RuleSnapshot,
        source_revision: str | None,
        stop_event: Event,
    ) -> BundleReview:
        """担当別のレビュー単位を安全な属性だけで観測する."""
        attributes = {
            "run_id": str(run_id),
            "bundle_id": str(task.bundle.bundle_id),
            "reviewer_profile": task.plan.reviewer_type.value,
        }
        with self._telemetry.start_span("review.bundle", attributes):
            return self._execute(
                task,
                request=request,
                run_id=run_id,
                snapshot=snapshot,
                source_revision=source_revision,
                stop_event=stop_event,
            )

    def _execute(
        self,
        task: BundleTask,
        *,
        request: ReviewRequest,
        run_id: RunId,
        snapshot: RuleSnapshot,
        source_revision: str | None,
        stop_event: Event,
    ) -> BundleReview:
        """外部呼出結果を保存せず、単一レビュー単位の結果を返す."""
        if stop_event.is_set():
            return cancelled_review(task)
        bundle = task.bundle
        plan = task.plan
        built = task.built
        if plan.is_specialist:
            scan_result, rescanned = self._scan_and_seal(
                SourcePayload(
                    kind=SourceKind.DIFF,
                    label="bundle-diff",
                    text=bundle.diff_text,
                )
            )
            if rescanned is None:
                stop_event.set()
                outcome = BundleOutcome(
                    bundle_id=bundle.bundle_id,
                    status=BundleStatus.SKIPPED,
                    findings=(),
                    error_code="SECRET_DETECTED",
                    error_message=Messages.SPECIALIST_INPUT_BLOCKED,
                )
                return BundleReview(
                    task,
                    outcome,
                    secret_findings=map_secret_findings(bundle, scan_result.findings),
                )
        request_hash = hash_text(
            built.request.system_instruction + "\n" + built.request.user_content
        )
        llm_records: list[LlmCallRecord] = []
        tool_outcomes: tuple[ContextToolOutcome, ...] = ()
        reflected_findings: tuple[Finding, ...] = ()
        source_fingerprints: tuple[SourceFingerprint, ...] = ()
        context_source: ContextSource | None = None
        reflection_call_count = 0
        try:
            if plan.is_context_tool_enabled:
                if (
                    self._context_source_factory is None
                    or self._context_tool_executor_factory is None
                ):
                    raise PolicyViolationError(Messages.CONTEXT_TOOL_DEPENDENCY_MISSING)
                context_source = self._context_source_factory(
                    request.repository_path,
                    source_revision,
                    request.diff_source.kind,
                )
                session = ContextToolSession(
                    source=context_source,
                    executor=self._context_tool_executor_factory(bundle.files),
                    secret_scanner=self._secret_scanner,
                    max_calls=plan.budget.max_tool_calls,
                    token_budget=plan.budget.dynamic_context_tokens,
                )
                with (
                    self._telemetry.start_span("context.tool"),
                    self._telemetry.start_span("llm.generate"),
                ):
                    agent_outcome = self._agent_loop.execute(
                        built.request,
                        coordinator=ContextToolCoordinator(
                            request.repository_path, self._path_policy
                        ),
                        session=session,
                        run_id=run_id,
                        bundle_id=bundle.bundle_id,
                        source_revision=context_source.source_revision,
                        should_stop=stop_event.is_set,
                    )
                source_fingerprints = tuple(
                    replace(item, bundle_sequence=bundle.bundle_id.sequence)
                    for item in context_source.fingerprints
                )
                tool_outcomes = agent_outcome.tool_outcomes
                llm_records.extend(
                    LlmCallRecord(
                        run_id=run_id,
                        bundle_sequence=bundle.bundle_id.sequence,
                        request_hash=item.request_hash,
                        model=item.model,
                        status="success",
                        input_tokens=item.usage.input_tokens,
                        output_tokens=item.usage.output_tokens,
                        latency_ms=item.usage.latency_ms,
                        cost_usd=item.usage.cost_usd,
                        attempt=item.attempt,
                        call_kind="review-turn",
                        turn_index=item.turn_index,
                    )
                    for item in agent_outcome.llm_calls
                )
                if agent_outcome.is_blocked:
                    stop_event.set()
                    outcome = outcome_from_records(
                        bundle,
                        tuple(llm_records),
                        status=BundleStatus.SKIPPED,
                        error_code="SECRET_DETECTED",
                        error_message=Messages.SENSITIVE_CANDIDATE_IN_CONTEXT_TOOL,
                    )
                    return BundleReview(
                        task,
                        outcome,
                        tuple(llm_records),
                        tool_outcomes,
                        secret_findings=agent_outcome.secret_findings,
                        source_fingerprints=source_fingerprints,
                    )
                if agent_outcome.result is None:
                    if stop_event.is_set():
                        return cancelled_review(
                            task,
                            tuple(llm_records),
                            tool_outcomes,
                            source_fingerprints,
                        )
                    raise AssertionError(_AGENT_RESULT_MISSING)
                llm_result = agent_outcome.result
            else:
                llm_result = self._generate_with_repair(built.request)
                llm_records.append(
                    LlmCallRecord(
                        run_id=run_id,
                        bundle_sequence=bundle.bundle_id.sequence,
                        request_hash=request_hash,
                        model=llm_result.model,
                        status="success",
                        input_tokens=llm_result.usage.input_tokens,
                        output_tokens=llm_result.usage.output_tokens,
                        latency_ms=llm_result.usage.latency_ms,
                        cost_usd=llm_result.usage.cost_usd,
                        attempt=llm_result.attempt,
                    )
                )
            if stop_event.is_set():
                return cancelled_review(
                    task,
                    tuple(llm_records),
                    tool_outcomes,
                    source_fingerprints,
                )
            parsed = parse_review_response(llm_result.payload, bundle)
            rejection_codes = [item.reason_code for item in parsed.rejected]
            with self._telemetry.start_span("finding.validate"):
                reviewed = self._manager.validate_bundle(
                    bundle,
                    parsed.candidates,
                    task.scanned.text,
                    snapshot,
                    built.rendered_rule_ids,
                    plan.file_rule_bindings,
                )
            rejection_codes.extend(item.reason_code for item in reviewed.rejected)
            if plan.is_reflection_enabled:
                if self._finding_reflector is None:
                    raise PolicyViolationError(Messages.REFLECTION_DEPENDENCY_MISSING)
                with self._telemetry.start_span("llm.reflect"):
                    try:
                        reflected = self._finding_reflector.reflect(reviewed.findings)
                    except ReflectionBudgetExceededError as exc:
                        return BundleReview(
                            task,
                            BundleOutcome(
                                bundle_id=bundle.bundle_id,
                                status=BundleStatus.FAILED,
                                findings=(),
                                input_tokens=llm_result.usage.input_tokens,
                                output_tokens=llm_result.usage.output_tokens,
                                latency_ms=llm_result.usage.latency_ms,
                                cost_usd=llm_result.usage.cost_usd,
                                error_code=_REFLECTION_BUDGET_EXCEEDED_CODE,
                                error_message=str(exc),
                            ),
                            tuple(llm_records),
                            tool_outcomes,
                            candidate_count=len(parsed.candidates) + len(parsed.rejected),
                            rejection_reason_counts=tuple(rejection_codes),
                            source_fingerprints=source_fingerprints,
                        )
                reviewed = replace(reviewed, findings=reflected.findings)
                reflected_findings = reflected.findings
                reflection_call_count = reflected.call_count
                llm_result = replace(
                    llm_result,
                    usage=add_usage(llm_result.usage, reflected.usage),
                    attempt=llm_result.attempt + reflected.attempt,
                )
                if reflected.request_hash is not None and reflected.model is not None:
                    llm_records.append(
                        LlmCallRecord(
                            run_id=run_id,
                            bundle_sequence=bundle.bundle_id.sequence,
                            request_hash=reflected.request_hash,
                            model=reflected.model,
                            status="success",
                            input_tokens=reflected.usage.input_tokens,
                            output_tokens=reflected.usage.output_tokens,
                            latency_ms=reflected.usage.latency_ms,
                            cost_usd=reflected.usage.cost_usd,
                            attempt=reflected.attempt,
                            call_kind="reflection",
                        )
                    )
            outcome = BundleOutcome(
                bundle_id=bundle.bundle_id,
                status=BundleStatus.SUCCESS,
                findings=reviewed.findings,
                input_tokens=llm_result.usage.input_tokens,
                output_tokens=llm_result.usage.output_tokens,
                latency_ms=llm_result.usage.latency_ms,
                cost_usd=llm_result.usage.cost_usd,
            )
            return BundleReview(
                task,
                outcome,
                tuple(llm_records),
                tool_outcomes,
                reflected_findings,
                candidate_count=len(parsed.candidates) + len(parsed.rejected),
                rejection_reason_counts=tuple(rejection_codes),
                reflection_call_count=reflection_call_count,
                source_fingerprints=source_fingerprints,
            )
        except (LlmClientError, InvalidLlmOutputError) as exc:
            if context_source is not None:
                source_fingerprints = tuple(
                    replace(item, bundle_sequence=bundle.bundle_id.sequence)
                    for item in context_source.fingerprints
                )
            llm_records.append(
                LlmCallRecord(
                    run_id=run_id,
                    bundle_sequence=bundle.bundle_id.sequence,
                    request_hash=request_hash,
                    model=self._llm_client.model,
                    status="failed",
                    input_tokens=exc.input_tokens,
                    output_tokens=exc.output_tokens,
                    latency_ms=exc.latency_ms,
                    cost_usd=exc.cost_usd,
                    attempt=exc.attempt,
                    call_kind=(
                        "reflection" if plan.is_reflection_enabled and llm_records else "review"
                    ),
                )
            )
            return BundleReview(
                task,
                BundleOutcome(
                    bundle_id=bundle.bundle_id,
                    status=BundleStatus.FAILED,
                    findings=(),
                    input_tokens=exc.input_tokens,
                    output_tokens=exc.output_tokens,
                    latency_ms=exc.latency_ms,
                    cost_usd=exc.cost_usd,
                    error_code=review_error_code(exc),
                    error_message=str(exc),
                ),
                tuple(llm_records),
                tool_outcomes,
                source_fingerprints=source_fingerprints,
            )
