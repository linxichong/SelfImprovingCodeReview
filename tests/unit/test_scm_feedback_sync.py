from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from self_improving_code_review.application.config.settings import AppConfig, ScmConfig
from self_improving_code_review.application.feedback.models import FeedbackOutcome
from self_improving_code_review.application.scm.feedback_parser import parse_feedback_command
from self_improving_code_review.application.scm.models import (
    ParsedFeedbackCommand,
    ScmFeedbackEventRecord,
    ScmFeedbackTarget,
)
from self_improving_code_review.application.scm.sync_feedback import SyncScmFeedback
from self_improving_code_review.domain.common.enums import FeedbackLabel
from self_improving_code_review.domain.common.errors import DatabaseError, InvalidInputError
from self_improving_code_review.domain.common.values import FeedbackId
from self_improving_code_review.domain.scm import (
    ChangeRef,
    CommentEvent,
    EventPage,
    ScmProvider,
)

_REF = ChangeRef(ScmProvider.GITHUB, "owner/repository", "42")
_TARGET = ScmFeedbackTarget(
    "publication-1", "00000000-0000-4000-8000-000000000001", "B001-F001", "10"
)
_TEST_FAILURE = "test failure"


@pytest.mark.case("UT-M4-006")
@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("true-positive", FeedbackLabel.TRUE_POSITIVE),
        ("false-positive", FeedbackLabel.FALSE_POSITIVE),
        ("needs-more-evidence", FeedbackLabel.NEEDS_MORE_EVIDENCE),
        ("not-applicable", FeedbackLabel.NOT_APPLICABLE),
    ],
)
def test_feedback_parser_accepts_only_defined_labels(
    label: str,
    expected: FeedbackLabel,
) -> None:
    command = parse_feedback_command(f'/review-feedback {label} --reason "確認理由"')

    assert command == ParsedFeedbackCommand(expected, "確認理由")


@pytest.mark.case("UT-M4-006")
@pytest.mark.parametrize(
    "body",
    [
        "/review-feedback true-positive",
        '/review-feedback missed-issue --reason "理由"',
        '/review-feedback TRUE-POSITIVE --reason "理由"',
        "/review-feedback true-positive --reason 理由",
        (
            '/review-feedback true-positive --reason "理由"\n'
            '/review-feedback false-positive --reason "別"'
        ),
        '通常コメント /review-feedback true-positive --reason "理由"',
    ],
)
def test_feedback_parser_rejects_non_exact_commands(body: str) -> None:
    with pytest.raises(InvalidInputError):
        parse_feedback_command(body)


def test_sync_processes_mapped_reply_and_saves_body_hash_only() -> None:
    event = _event("event-1", actor="github-reviewer", target="10")
    repository = _Repository(targets={"10": _TARGET})
    processor = _Processor()
    sync = _sync(repository, processor, events=(event,))

    outcome = sync.execute(_REF)

    assert outcome.processed_count == 1
    assert outcome.failed_count == 0
    assert processor.calls[0][2] == "reviewer-1"
    record = repository.saved[0][0]
    assert record.label is FeedbackLabel.TRUE_POSITIVE
    assert record.mapped_feedback_id == "00000000-0000-4000-8000-000000000099"
    assert len(record.payload_hash) == 64
    assert not hasattr(record, "body")


def test_sync_counts_duplicate_and_unrelated_comments_without_processing() -> None:
    duplicate = _event("duplicate", actor="github-reviewer", target="10")
    no_reply = _event("no-reply", actor="github-reviewer", target=None)
    unknown_target = _event("unknown-target", actor="github-reviewer", target="999")
    repository = _Repository(
        existing={"duplicate"},
        targets={"10": _TARGET},
    )
    processor = _Processor()

    outcome = _sync(
        repository,
        processor,
        events=(duplicate, no_reply, unknown_target),
    ).execute(_REF)

    assert outcome.duplicate_count == 1
    assert outcome.ignored_count == 2
    assert not processor.calls
    assert not repository.saved
    assert repository.cursor_saves == [
        "2026-09-25T00:00:00+00:00",
        "2026-09-25T00:00:00+00:00",
        "2026-09-25T00:00:00+00:00",
    ]


def test_sync_advances_new_page_cursor_for_empty_page() -> None:
    repository = _Repository()

    outcome = _sync(
        repository,
        _Processor(),
        events=(),
        next_cursor="2026-09-26T00:00:00+00:00",
    ).execute(_REF)

    assert outcome.next_cursor == "2026-09-26T00:00:00+00:00"
    assert repository.cursor_saves == ["2026-09-26T00:00:00+00:00"]


def test_sync_does_not_create_cursor_for_empty_initial_page() -> None:
    repository = _Repository(cursor=None)

    outcome = _sync(
        repository,
        _Processor(),
        events=(),
        next_cursor=None,
    ).execute(_REF)

    assert outcome.next_cursor is None
    assert repository.cursor_saves == []


def test_sync_uses_event_cursor_when_page_cursor_is_missing() -> None:
    repository = _Repository(cursor=None, targets={"10": _TARGET})
    event = _event("event", actor="github-reviewer", target="10")

    outcome = _sync(
        repository,
        _Processor(),
        events=(event,),
        next_cursor=None,
    ).execute(_REF)

    assert outcome.next_cursor == event.updated_at.isoformat()
    assert repository.saved[0][1] == event.updated_at.isoformat()


def test_sync_saves_page_cursor_only_after_all_events() -> None:
    first = _event(
        "first",
        actor="github-reviewer",
        target=None,
        updated_at=datetime(2026, 9, 25, tzinfo=UTC),
    )
    second = _event(
        "second",
        actor="github-reviewer",
        target=None,
        updated_at=datetime(2026, 9, 26, tzinfo=UTC),
    )
    repository = _Repository()

    _sync(
        repository,
        _Processor(),
        events=(first, second),
        next_cursor="2026-09-27T00:00:00+00:00",
    ).execute(_REF)

    assert repository.cursor_saves == [
        first.updated_at.isoformat(),
        second.updated_at.isoformat(),
        "2026-09-27T00:00:00+00:00",
    ]


def test_sync_does_not_advance_to_page_cursor_after_mid_page_failure() -> None:
    class FailingRepository(_Repository):
        """二件目の監査保存だけを失敗させるリポジトリ."""

        def save_event_and_cursor(
            self,
            ref: ChangeRef,
            record: ScmFeedbackEventRecord,
            cursor: str,
        ) -> None:
            """二件目なら失敗し、それ以前の確定位置を保持する."""
            if record.event_external_id == "second":
                raise DatabaseError(_TEST_FAILURE)
            super().save_event_and_cursor(ref, record, cursor)

    first = _event(
        "first",
        actor="github-reviewer",
        target="10",
        updated_at=datetime(2026, 9, 25, tzinfo=UTC),
    )
    second = _event(
        "second",
        actor="github-reviewer",
        target="10",
        updated_at=datetime(2026, 9, 26, tzinfo=UTC),
    )
    repository = FailingRepository(targets={"10": _TARGET})

    with pytest.raises(DatabaseError, match="test failure"):
        _sync(
            repository,
            _Processor(),
            events=(first, second),
            next_cursor="2026-09-27T00:00:00+00:00",
        ).execute(_REF)

    assert repository.saved[0][1] == first.updated_at.isoformat()
    assert repository.cursor == first.updated_at.isoformat()
    assert repository.cursor_saves == []


@pytest.mark.parametrize(
    ("event_id", "actor", "body", "error_code"),
    [
        ("self", "review-bot", None, "SELF_EVENT"),
        ("unknown", "stranger", None, "UNKNOWN_ACTOR"),
        (
            "invalid",
            "github-reviewer",
            "通常コメント",
            "INVALID_COMMAND",
        ),
    ],
)
def test_sync_audits_ignored_targeted_events(
    event_id: str,
    actor: str,
    body: str | None,
    error_code: str,
) -> None:
    repository = _Repository(targets={"10": _TARGET})
    event = _event(event_id, actor=actor, target="10", body=body)

    outcome = _sync(repository, _Processor(), events=(event,)).execute(_REF)

    assert outcome.ignored_count == 1
    assert repository.saved[0][0].safe_error_code == error_code
    assert repository.saved[0][0].status == "ignored"


def test_sync_audits_domain_failure_without_advancing_feedback() -> None:
    repository = _Repository(targets={"10": _TARGET})
    processor = _Processor(should_fail=True)

    outcome = _sync(
        repository,
        processor,
        events=(_event("failed", actor="github-reviewer", target="10"),),
    ).execute(_REF)

    assert outcome.failed_count == 1
    assert repository.saved[0][0].status == "failed"
    assert repository.saved[0][0].safe_error_code == "InvalidInputError"


@pytest.mark.parametrize(
    "config",
    [
        AppConfig(),
        AppConfig(
            scm=ScmConfig(
                enabled=True,
                repository_external_id="owner/repository",
                bot_actor_external_id="review-bot",
            )
        ),
    ],
)
def test_sync_requires_both_scm_and_feedback_flags(config: AppConfig) -> None:
    sync = SyncScmFeedback(
        config=config,
        scm_client=_Client(()),
        repository=_Repository(),
        processor=_Processor(),
    )

    with pytest.raises(InvalidInputError):
        sync.execute(_REF)


@pytest.mark.case("UT-M4-008")
def test_sync_rejects_authenticated_actor_mismatch() -> None:
    sync = _sync(_Repository(), _Processor(), events=())
    sync._scm_client = _Client((), actor="another-bot")

    with pytest.raises(InvalidInputError):
        sync.execute(_REF)


def test_sync_rejects_repository_mismatch() -> None:
    sync = _sync(_Repository(), _Processor(), events=())
    other = ChangeRef(ScmProvider.GITHUB, "other/repository", "42")

    with pytest.raises(InvalidInputError):
        sync.execute(other)


@dataclass
class _Client:
    events: tuple[CommentEvent, ...]
    actor: str = "review-bot"
    next_cursor: str | None = "2026-09-25T00:00:00+00:00"
    expected_cursor: str | None = "2026-09-24T00:00:00+00:00"

    def get_authenticated_actor(self) -> str:
        return self.actor

    def list_comment_events(self, ref: ChangeRef, *, cursor: str | None) -> EventPage:
        assert ref == _REF
        assert cursor == self.expected_cursor
        return EventPage(self.events, self.next_cursor)


@dataclass
class _Repository:
    existing: set[str] = field(default_factory=set)
    targets: dict[str, ScmFeedbackTarget] = field(default_factory=dict)
    saved: list[tuple[ScmFeedbackEventRecord, str]] = field(default_factory=list)
    cursor: str | None = "2026-09-24T00:00:00+00:00"
    cursor_saves: list[str] = field(default_factory=list)

    def get_cursor(self, ref: ChangeRef) -> str | None:
        assert ref == _REF
        return self.cursor

    def has_event(self, provider: str, event_external_id: str) -> bool:
        assert provider == "github"
        return event_external_id in self.existing

    def find_target(
        self,
        ref: ChangeRef,
        external_comment_id: str,
    ) -> ScmFeedbackTarget | None:
        assert ref == _REF
        return self.targets.get(external_comment_id)

    def save_event_and_cursor(
        self,
        ref: ChangeRef,
        record: ScmFeedbackEventRecord,
        cursor: str,
    ) -> None:
        assert ref == _REF
        self.saved.append((record, cursor))
        self.cursor = cursor

    def save_cursor(self, ref: ChangeRef, cursor: str) -> None:
        """監査対象外イベントの確定カーソルを記録する."""
        assert ref == _REF
        self.cursor_saves.append(cursor)
        self.cursor = cursor


@dataclass
class _Processor:
    should_fail: bool = False
    calls: list[tuple[ScmFeedbackTarget, ParsedFeedbackCommand, str]] = field(default_factory=list)

    def process(
        self,
        target: ScmFeedbackTarget,
        command: ParsedFeedbackCommand,
        actor_id: str,
    ) -> FeedbackOutcome:
        self.calls.append((target, command, actor_id))
        if self.should_fail:
            raise InvalidInputError("failure")
        return FeedbackOutcome(
            FeedbackId("00000000-0000-4000-8000-000000000099"),
            "RECORDED",
        )


def _sync(
    repository: _Repository,
    processor: _Processor,
    *,
    events: tuple[CommentEvent, ...],
    next_cursor: str | None = "2026-09-25T00:00:00+00:00",
) -> SyncScmFeedback:
    config = AppConfig(
        scm=ScmConfig(
            enabled=True,
            repository_external_id="owner/repository",
            bot_actor_external_id="review-bot",
            feedback_sync_enabled=True,
            feedback_actor_mapping={"github-reviewer": "reviewer-1"},
        )
    )
    return SyncScmFeedback(
        config=config,
        scm_client=_Client(
            events,
            next_cursor=next_cursor,
            expected_cursor=repository.cursor,
        ),
        repository=repository,
        processor=processor,
    )


def _event(
    event_id: str,
    *,
    actor: str,
    target: str | None,
    body: str | None = None,
    updated_at: datetime | None = None,
) -> CommentEvent:
    return CommentEvent(
        event_external_id=event_id,
        comment_external_id=f"comment-{event_id}",
        target_comment_external_id=target,
        actor_external_id=actor,
        body=body or '/review-feedback true-positive --reason "妥当な指摘"',
        updated_at=updated_at or datetime(2026, 9, 25, tzinfo=UTC),
    )
