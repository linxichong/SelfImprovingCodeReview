from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from self_improving_code_review.domain.common.errors import (
    InvalidInputError,
    InvalidStateTransitionError,
)
from self_improving_code_review.domain.common.values import NormalizedPath
from self_improving_code_review.domain.scm import (
    ChangeRef,
    CommentPosition,
    CreateComment,
    PublicationKind,
    PublicationStatus,
    ScmChange,
    ScmChangeState,
    ScmProvider,
    UpdateComment,
    allowed_source_statuses,
    transition_publication,
)

_BASE_SHA = "1" * 40
_HEAD_SHA = "2" * 40


@pytest.mark.case("UT-M4-002")
def test_change_snapshot_accepts_fixed_revisions() -> None:
    reference = ChangeRef(ScmProvider.GITHUB, "owner/repository", "42")

    change = ScmChange(
        ref=reference,
        base_sha=_BASE_SHA,
        head_sha=_HEAD_SHA,
        state=ScmChangeState.OPEN,
        is_draft=False,
        source_repository_external_id="contributor/repository",
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert change.ref == reference
    assert change.head_sha == _HEAD_SHA


@pytest.mark.case("UT-M4-002")
@pytest.mark.parametrize(
    ("repository", "change_id"),
    [("repository", "1"), ("owner/", "1"), ("/repository", "1"), ("owner/repository", " ")],
)
def test_change_reference_rejects_invalid_external_identifiers(
    repository: str,
    change_id: str,
) -> None:
    with pytest.raises(InvalidInputError):
        ChangeRef(ScmProvider.GITHUB, repository, change_id)


@pytest.mark.case("UT-M4-002")
@pytest.mark.parametrize(
    ("base_sha", "head_sha", "updated_at"),
    [
        ("1" * 39, _HEAD_SHA, datetime(2026, 1, 1, tzinfo=UTC)),
        ("G" * 40, _HEAD_SHA, datetime(2026, 1, 1, tzinfo=UTC)),
        (_BASE_SHA, _HEAD_SHA, datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)),
    ],
)
def test_change_snapshot_rejects_unfixed_inputs(
    base_sha: str,
    head_sha: str,
    updated_at: datetime,
) -> None:
    with pytest.raises(InvalidInputError):
        ScmChange(
            ref=ChangeRef(ScmProvider.GITHUB, "owner/repository", "42"),
            base_sha=base_sha,
            head_sha=head_sha,
            state=ScmChangeState.OPEN,
            is_draft=False,
            source_repository_external_id="owner/repository",
            updated_at=updated_at,
        )


@pytest.mark.case("UT-M4-004")
def test_inline_comment_requires_new_side_position() -> None:
    position = CommentPosition(NormalizedPath("src/app.py"), 10, _HEAD_SHA)

    request = CreateComment(PublicationKind.INLINE, "指摘本文", position)

    assert request.position == position


@pytest.mark.case("UT-M4-004")
@pytest.mark.parametrize(
    "factory",
    [
        lambda: CommentPosition(NormalizedPath("src/app.py"), 0, _HEAD_SHA),
        lambda: CommentPosition(NormalizedPath("src/app.py"), 1, _HEAD_SHA, side="old"),
        lambda: CommentPosition(NormalizedPath("src/app.py"), 1, "2" * 39),
        lambda: CreateComment(PublicationKind.INLINE, "本文"),
        lambda: CreateComment(
            PublicationKind.SUMMARY,
            "本文",
            CommentPosition(NormalizedPath("src/app.py"), 1, _HEAD_SHA),
        ),
        lambda: CreateComment(PublicationKind.SUMMARY, " "),
        lambda: UpdateComment(PublicationKind.SUMMARY, "", "本文"),
        lambda: UpdateComment(PublicationKind.INLINE, "comment-1", " "),
    ],
)
def test_comment_models_reject_invalid_combinations(factory: Callable[[], object]) -> None:
    with pytest.raises(InvalidInputError):
        factory()


@pytest.mark.case("UT-M4-007")
@pytest.mark.parametrize(
    ("current", "target"),
    [
        (PublicationStatus.PLANNED, PublicationStatus.PUBLISHING),
        (PublicationStatus.PLANNED, PublicationStatus.UPDATING),
        (PublicationStatus.PLANNED, PublicationStatus.STALE),
        (PublicationStatus.PLANNED, PublicationStatus.BLOCKED),
        (PublicationStatus.PUBLISHING, PublicationStatus.PUBLISHED),
        (PublicationStatus.PUBLISHING, PublicationStatus.FAILED),
        (PublicationStatus.PUBLISHING, PublicationStatus.STALE),
        (PublicationStatus.PUBLISHED, PublicationStatus.PLANNED),
        (PublicationStatus.PUBLISHED, PublicationStatus.UPDATING),
        (PublicationStatus.PUBLISHED, PublicationStatus.STALE),
        (PublicationStatus.UPDATING, PublicationStatus.PUBLISHED),
        (PublicationStatus.UPDATING, PublicationStatus.FAILED),
        (PublicationStatus.UPDATING, PublicationStatus.STALE),
        (PublicationStatus.FAILED, PublicationStatus.PUBLISHING),
        (PublicationStatus.FAILED, PublicationStatus.UPDATING),
        (PublicationStatus.FAILED, PublicationStatus.PUBLISHED),
        (PublicationStatus.FAILED, PublicationStatus.STALE),
        (PublicationStatus.FAILED, PublicationStatus.BLOCKED),
    ],
)
def test_publication_state_machine_accepts_defined_transitions(
    current: PublicationStatus,
    target: PublicationStatus,
) -> None:
    assert transition_publication(current, target) is target


@pytest.mark.case("UT-M4-007")
@pytest.mark.parametrize(
    ("current", "target"),
    [
        (PublicationStatus.PLANNED, PublicationStatus.PUBLISHED),
        (PublicationStatus.PLANNED, PublicationStatus.FAILED),
        (PublicationStatus.PUBLISHED, PublicationStatus.PUBLISHING),
        (PublicationStatus.PUBLISHED, PublicationStatus.FAILED),
    ],
)
def test_publication_state_machine_rejects_undefined_transitions(
    current: PublicationStatus,
    target: PublicationStatus,
) -> None:
    with pytest.raises(InvalidStateTransitionError):
        transition_publication(current, target)


@pytest.mark.case("UT-M4-007")
def test_allowed_source_statuses_excludes_terminal_states() -> None:
    sources = allowed_source_statuses(PublicationStatus.PUBLISHED)

    assert PublicationStatus.PLANNED not in sources
    assert PublicationStatus.STALE not in sources
    assert PublicationStatus.BLOCKED not in sources
    assert sources == (
        PublicationStatus.PUBLISHING,
        PublicationStatus.UPDATING,
        PublicationStatus.FAILED,
    )


@pytest.mark.case("UT-M4-007")
@pytest.mark.parametrize(
    "current",
    [PublicationStatus.STALE, PublicationStatus.BLOCKED],
)
def test_terminal_publication_states_reject_transitions(current: PublicationStatus) -> None:
    with pytest.raises(InvalidStateTransitionError):
        transition_publication(current, PublicationStatus.PUBLISHING)
