"""レビュー単位の上限付き並列実行と決定的な結果整列を担当する."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from threading import Event

from self_improving_code_review.application.review.bundle_review_models import (
    BundleReview,
    BundleTask,
)


def schedule_bundle_reviews(
    tasks: tuple[BundleTask, ...],
    *,
    max_workers: int,
    execute_group: Callable[[tuple[BundleTask, ...], Event], tuple[BundleReview, ...]],
) -> tuple[BundleReview, ...]:
    """レビュー単位を上限付きで実行し、識別子順へ戻す."""
    stop_event = Event()
    grouped: dict[int, list[BundleTask]] = {}
    for task in tasks:
        grouped.setdefault(task.bundle.bundle_id.sequence, []).append(task)
    task_groups = tuple(tuple(grouped[key]) for key in sorted(grouped))
    if max_workers == 1 or len(task_groups) < 2:
        reviews = tuple(
            review for group in task_groups for review in execute_group(group, stop_event)
        )
    else:
        reviews_list: list[BundleReview] = []
        with ThreadPoolExecutor(
            max_workers=min(max_workers, len(task_groups)),
            thread_name_prefix="review-bundle",
        ) as executor:
            futures: dict[Future[tuple[BundleReview, ...]], int] = {
                executor.submit(
                    execute_group,
                    group,
                    stop_event,
                ): group[0].bundle.bundle_id.sequence
                for group in task_groups
            }
            for future in as_completed(futures):
                reviews_list.extend(future.result())
        reviews = tuple(reviews_list)
    return tuple(
        sorted(
            reviews,
            key=lambda item: (
                item.task.bundle.bundle_id.sequence,
                item.task.plan.is_specialist,
            ),
        )
    )
