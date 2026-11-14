"""レビュー開始時の固定識別情報と決定的な単位計画を準備する."""

from __future__ import annotations

from dataclasses import dataclass

from self_improving_code_review.application.config.settings import AppConfig
from self_improving_code_review.application.ports.diff_provider import RawDiff
from self_improving_code_review.application.review.bundler import BundlingResult, SimpleBundler
from self_improving_code_review.application.review.context_builder import (
    ReviewContextBuilder,
)
from self_improving_code_review.application.review.dependency_bundler import (
    DependencyAwareBundler,
    DependencyBundlingResult,
)
from self_improving_code_review.application.review.models import (
    ReviewPlan,
    ReviewRequest,
    RunVersions,
)
from self_improving_code_review.application.review.planner import RuleBasedReviewPlanner
from self_improving_code_review.application.review.reflect_findings import FindingReflector
from self_improving_code_review.application.review.specialist_reviewers import (
    select_specialized_reviewers,
)
from self_improving_code_review.domain.change.file_filter import (
    FileFilterResult,
)
from self_improving_code_review.domain.change.models import ChangedFile
from self_improving_code_review.domain.common.enums import (
    ReviewMode,
)
from self_improving_code_review.domain.common.hashing import hash_lines, hash_text
from self_improving_code_review.domain.common.values import RunId
from self_improving_code_review.domain.rule.models import ReviewRule, RuleSnapshot


@dataclass(frozen=True, slots=True)
class PreparedReviewInput:
    """取得と対象検証を終えた入力を後続の識別・計画処理へ渡す."""

    raw_diff: RawDiff
    file_results: tuple[FileFilterResult, ...]
    reviewable_files: tuple[ChangedFile, ...]
    snapshot: RuleSnapshot


def build_idempotency_key(
    request: ReviewRequest,
    raw_diff: RawDiff,
    snapshot: RuleSnapshot,
    *,
    config: AppConfig,
    context_builder: ReviewContextBuilder,
    reflector: FindingReflector | None,
    package_version: str,
    secret_engine_version: str,
    model: str,
    memory_state_hash: str,
) -> str:
    """既存の順序と書式を維持して実行の冪等キーを組み立てる."""
    idempotency_parts: tuple[str, ...] = (
        "review",
        str(request.project_id),
        request.repository_path.resolve().as_posix(),
        raw_diff.source_revision or "unborn",
        hash_text(raw_diff.text),
        config.pipeline_content_hash,
        request.mode.value,
        package_version,
        context_builder.prompt_hash,
        context_builder.agent_prompt_hash or "agent-disabled",
        (reflector.prompt_hash if reflector is not None else "reflection-disabled"),
        snapshot.rule_set_hash,
        secret_engine_version,
        model,
        memory_state_hash,
    )
    if config.governance.specialized_reviewers_enabled:
        idempotency_parts += (
            "m5-specialized-reviewers",
            context_builder.specialist_prompt_version or "",
            context_builder.specialist_prompt_hash or "",
        )
    if request.execution_nonce is not None:
        idempotency_parts += (request.execution_nonce,)
    return hash_lines(idempotency_parts)


def build_run_versions(
    snapshot: RuleSnapshot,
    *,
    config: AppConfig,
    context_builder: ReviewContextBuilder,
    reflector: FindingReflector | None,
    package_version: str,
    secret_engine_version: str,
) -> RunVersions:
    """固定された設定と資源から実行版情報を構築する."""
    return RunVersions(
        package_version=package_version,
        config_hash=config.content_hash,
        prompt_version=context_builder.prompt_version,
        prompt_hash=context_builder.prompt_hash,
        rule_snapshot_id=snapshot.snapshot_id,
        rule_set_hash=snapshot.rule_set_hash,
        is_rule_set_empty=snapshot.is_empty,
        secret_engine_version=secret_engine_version,
        agent_prompt_version=context_builder.agent_prompt_version,
        agent_prompt_hash=context_builder.agent_prompt_hash,
        reflection_prompt_version=(reflector.prompt_version if reflector is not None else None),
        reflection_prompt_hash=(reflector.prompt_hash if reflector is not None else None),
        pipeline_config_hash=config.pipeline_content_hash,
    )


def prepare_pipeline(
    run_id: RunId,
    files: tuple[ChangedFile, ...],
    snapshot: RuleSnapshot,
    mode: ReviewMode,
    *,
    is_m3_enabled: bool,
    config: AppConfig,
    bundler: SimpleBundler,
    dependency_bundler: DependencyAwareBundler,
    planner: RuleBasedReviewPlanner,
) -> tuple[
    BundlingResult | DependencyBundlingResult, dict[int, tuple[ReviewPlan, tuple[ReviewRule, ...]]]
]:
    """実行識別子に対する結合計画と担当計画を決定的に作成する."""
    bundling = (
        dependency_bundler.bundle(run_id, files) if is_m3_enabled else bundler.bundle(run_id, files)
    )
    planned: dict[int, tuple[ReviewPlan, tuple[ReviewRule, ...]]] = {}
    for bundle in bundling.bundles:
        plan, applicable_rules = planner.plan(bundle, snapshot.rules, mode)
        if config.governance.specialized_reviewers_enabled:
            plan = select_specialized_reviewers(
                plan,
                bundle,
                applicable_rules,
                enabled=True,
                max_reviewer_profiles=config.governance.max_reviewer_profiles,
                risk_threshold=config.governance.additional_reviewer_risk_threshold,
                specialist_total_tokens=config.governance.specialist_total_tokens,
                specialist_max_output_tokens=(config.governance.specialist_max_output_tokens),
            )
        planned[bundle.bundle_id.sequence] = (plan, applicable_rules)
    return bundling, planned
