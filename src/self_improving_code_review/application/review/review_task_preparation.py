"""検査済み入力から担当別タスクと実際に注入した記憶集合を構築する."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from self_improving_code_review.application.config.settings import AppConfig
from self_improving_code_review.application.memory.retrieve_memories import (
    RetrievedMemories,
)
from self_improving_code_review.application.review.bundle_review_models import (
    BundleTask,
)
from self_improving_code_review.application.review.context_builder import (
    ReviewContextBuilder,
)
from self_improving_code_review.application.review.models import (
    BundleOutcome,
    ReviewBundle,
    ReviewPlan,
    ReviewRequest,
)
from self_improving_code_review.application.review.review_identity import (
    bundle_input_hash,
)
from self_improving_code_review.application.review.specialist_reviewers import (
    build_specialist_plan,
)
from self_improving_code_review.domain.common.enums import (
    ReviewMode,
)
from self_improving_code_review.domain.memory.models import MemoryContent
from self_improving_code_review.domain.rule.models import ReviewRule
from self_improving_code_review.security.models import (
    ScannedPayload,
    SecretFinding,
)


@dataclass(frozen=True, slots=True)
class ScannedReviewBundles:
    """秘密検査済み入力と最初の遮断結果を調整処理へ渡す."""

    payloads: Mapping[str, ScannedPayload]
    secret_findings: tuple[SecretFinding, ...]
    blocked_outcome: BundleOutcome | None


@dataclass(frozen=True, slots=True)
class PreparedReviewTasks:
    """担当順のタスクと実際の記憶注入結果を後続処理へ渡す."""

    tasks: tuple[BundleTask, ...]
    memory_contents: tuple[MemoryContent, ...]
    has_memory_context: bool
    memory_truncated_count: int


def prepare_review_tasks(
    request: ReviewRequest,
    bundles_to_review: tuple[ReviewBundle, ...],
    planned_reviews: Mapping[int, tuple[ReviewPlan, tuple[ReviewRule, ...]]],
    scanned_payloads: Mapping[str, ScannedPayload],
    *,
    config: AppConfig,
    context_builder: ReviewContextBuilder,
    retrieve: Callable[[ReviewRequest, ReviewBundle, ReviewPlan], RetrievedMemories],
) -> PreparedReviewTasks:
    """主担当・補助担当順に検索と入力構築を行い、使用した記憶だけを返す."""
    rendered_memory_contents: dict[tuple[str, int], MemoryContent] = {}
    has_memory_context = False
    memory_truncated_count = 0
    tasks: list[BundleTask] = []
    for bundle in bundles_to_review:
        primary_plan, applicable_rules = planned_reviews[bundle.bundle_id.sequence]
        scanned = scanned_payloads[str(bundle.bundle_id)]
        execution_plans = (
            primary_plan,
            *(
                build_specialist_plan(
                    primary_plan,
                    assignment,
                    max_output_tokens=(config.governance.specialist_max_output_tokens),
                )
                for assignment in primary_plan.additional_reviewers
            ),
        )
        for plan in execution_plans:
            retrieved = retrieve(request, bundle, plan)
            memory_truncated_count += retrieved.truncated_count
            if request.mode is ReviewMode.MEMORY and config.memory.is_injection_enabled:
                has_memory_context = True
                rendered_memory_contents.update(
                    {
                        (str(content.memory_id), content.version.value): content
                        for content in retrieved.contents
                    }
                )

            built = context_builder.build(
                bundle,
                plan,
                applicable_rules,
                scanned,
                retrieved.contents,
            )
            tasks.append(
                BundleTask(
                    bundle=bundle,
                    plan=plan,
                    applicable_rules=applicable_rules,
                    scanned=scanned,
                    built=built,
                    input_hash=bundle_input_hash(bundle, plan),
                )
            )
    return PreparedReviewTasks(
        tasks=tuple(tasks),
        memory_contents=tuple(rendered_memory_contents.values()),
        has_memory_context=has_memory_context,
        memory_truncated_count=memory_truncated_count,
    )
