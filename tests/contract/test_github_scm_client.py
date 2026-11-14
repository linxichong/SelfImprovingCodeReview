from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import pytest

from self_improving_code_review.application.config.settings import ScmConfig
from self_improving_code_review.domain.common.errors import ScmClientError
from self_improving_code_review.domain.common.values import NormalizedPath
from self_improving_code_review.domain.scm import (
    ChangeRef,
    CommentPosition,
    CreateComment,
    PublicationKind,
    ScmProvider,
    UpdateComment,
)
from self_improving_code_review.infrastructure.scm.github_client import GitHubScmClient
from self_improving_code_review.infrastructure.scm.http_transport import (
    HttpResponse,
    UrllibHttpTransport,
)
from self_improving_code_review.messages import Messages

_BASE_SHA = "1" * 40
_HEAD_SHA = "2" * 40


@dataclass
class _Transport:
    responses: list[HttpResponse | ScmClientError]
    requests: list[tuple[str, str, Mapping[str, str], bytes | None, int]] = field(
        default_factory=list
    )

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
    ) -> HttpResponse:
        self.requests.append((method, url, headers, body, timeout_seconds))
        response = self.responses.pop(0)
        if isinstance(response, ScmClientError):
            raise response
        return response


@pytest.mark.case("CT-M4-001")
def test_get_change_and_authenticated_actor_follow_github_contract() -> None:
    transport = _Transport(
        [
            _response(
                {
                    "base": {"sha": _BASE_SHA},
                    "head": {
                        "sha": _HEAD_SHA,
                        "repo": {"full_name": "contributor/repository"},
                    },
                    "state": "open",
                    "draft": False,
                    "updated_at": "2026-09-25T01:02:03Z",
                }
            ),
            _response({"login": "review-bot"}),
        ]
    )
    client = _client(transport)

    change = client.get_change(_ref())
    actor = client.get_authenticated_actor()

    assert change.base_sha == _BASE_SHA
    assert change.head_sha == _HEAD_SHA
    assert change.source_repository_external_id == "contributor/repository"
    assert actor == "review-bot"
    method, url, headers, body, timeout = transport.requests[0]
    assert method == "GET"
    assert url == "https://api.github.com/repos/owner/repository/pulls/42"
    assert headers["Authorization"] == "Bearer token"
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert body is None
    assert timeout == 30


@pytest.mark.case("CT-M4-001")
def test_create_and_update_comments_use_kind_specific_endpoints() -> None:
    transport = _Transport(
        [
            _response({"id": 10, "html_url": "https://github.test/summary"}, status=201),
            _response({"id": 11, "html_url": "https://github.test/inline"}, status=201),
            _response({"id": 10, "html_url": "https://github.test/updated"}),
            _response({"id": 11, "html_url": "https://github.test/updated-inline"}),
        ]
    )
    client = _client(transport)

    summary = client.create_comment(
        _ref(),
        CreateComment(PublicationKind.SUMMARY, "概要"),
    )
    inline = client.create_comment(
        _ref(),
        CreateComment(
            PublicationKind.INLINE,
            "指摘",
            CommentPosition(NormalizedPath("src/app.py"), 12, _HEAD_SHA),
        ),
    )
    client.update_comment(
        _ref(),
        UpdateComment(PublicationKind.SUMMARY, summary.comment_external_id, "更新"),
    )
    client.update_comment(
        _ref(),
        UpdateComment(PublicationKind.INLINE, inline.comment_external_id, "更新"),
    )

    assert transport.requests[0][1].endswith("/issues/42/comments")
    assert transport.requests[1][1].endswith("/pulls/42/comments")
    inline_body = json.loads(transport.requests[1][3] or b"{}")
    assert inline_body == {
        "body": "指摘",
        "commit_id": _HEAD_SHA,
        "line": 12,
        "path": "src/app.py",
        "side": "RIGHT",
    }
    assert transport.requests[2][1].endswith("/issues/comments/10")
    assert transport.requests[3][1].endswith("/pulls/comments/11")


@pytest.mark.case("CT-M4-001")
def test_list_comment_events_uses_stable_paging_cursor() -> None:
    transport = _Transport(
        [
            _response(
                [
                    {
                        "id": 99,
                        "in_reply_to_id": 10,
                        "body": '/review-feedback true-positive --reason "妥当"',
                        "updated_at": "2026-09-25T01:02:03Z",
                        "user": {"login": "reviewer"},
                    }
                ]
            )
        ]
    )

    page = _client(transport).list_comment_events(
        _ref(),
        cursor="2026-09-24T01:02:03+00:00",
    )

    assert page.events[0].comment_external_id == "99"
    assert page.events[0].target_comment_external_id == "10"
    assert page.events[0].actor_external_id == "reviewer"
    assert page.next_cursor == "2026-09-25T01:02:03+00:00"
    assert "since=2026-09-24T01%3A02%3A03%2B00%3A00" in transport.requests[0][1]


@pytest.mark.case("CT-M4-001")
def test_list_comment_events_sorts_provider_results_before_advancing_cursor() -> None:
    def event(identifier: int, updated_at: str) -> dict[str, object]:
        """並び順確認用の GitHub コメントを返す."""
        return {
            "id": identifier,
            "body": "本文",
            "updated_at": updated_at,
            "user": {"login": "reviewer"},
        }

    transport = _Transport(
        [
            _response(
                [
                    event(3, "2026-09-25T02:00:00Z"),
                    event(2, "2026-09-25T01:00:00Z"),
                    event(1, "2026-09-25T01:00:00Z"),
                ]
            )
        ]
    )

    page = _client(transport).list_comment_events(_ref(), cursor=None)

    assert [item.comment_external_id for item in page.events] == ["1", "2", "3"]
    assert page.next_cursor == "2026-09-25T02:00:00+00:00"


@pytest.mark.case("CT-M4-001", "CT-M4-002")
def test_retryable_status_and_connection_error_are_retried() -> None:
    transport = _Transport(
        [
            ScmClientError("temporary", is_retryable=True),
            _response({}, status=500),
            _response({"login": "review-bot"}),
        ]
    )
    delays: list[float] = []
    client = _client(transport, sleep=delays.append)

    assert client.get_authenticated_actor() == "review-bot"
    assert delays == [0.25, 0.5]
    assert client.get_metrics().call_count == 3
    assert client.get_metrics().retry_count == 2
    assert client.get_metrics().retry_wait_ms == 750


@pytest.mark.case("CT-M4-001", "CT-M4-002")
@pytest.mark.parametrize(
    ("status", "message", "retryable"),
    [
        (401, Messages.SCM_API_AUTH_FAILED, False),
        (403, Messages.SCM_API_AUTH_FAILED, False),
        (404, Messages.SCM_RESOURCE_NOT_FOUND, False),
        (422, Messages.SCM_API_REQUEST_REJECTED, False),
        (429, Messages.SCM_RATE_LIMIT_EXHAUSTED, True),
        (418, Messages.SCM_API_REQUEST_FAILED, False),
        (501, Messages.SCM_API_REQUEST_FAILED, False),
        (503, Messages.SCM_API_TEMPORARY_FAILURE_EXHAUSTED, True),
    ],
)
def test_http_errors_are_safely_classified(
    status: int,
    message: str,
    retryable: bool,
) -> None:
    transport = _Transport([_response({}, status=status)] * 3)

    with pytest.raises(ScmClientError, match=re.escape(message)) as raised:
        _client(transport).get_authenticated_actor()

    assert raised.value.status_code == status
    assert raised.value.is_retryable is retryable


@pytest.mark.case("CT-M4-001")
@pytest.mark.parametrize(
    ("headers", "expected_delay"),
    [
        ({"Retry-After": "7"}, 7.0),
        ({"x-ratelimit-reset": "1012"}, 12.0),
        ({"X-RateLimit-Reset": "990"}, 0.0),
        ({"Retry-After": "soon"}, 0.25),
    ],
)
def test_rate_limit_wait_uses_provider_reset_information(
    headers: dict[str, str],
    expected_delay: float,
) -> None:
    transport = _Transport(
        [_response({}, status=429, headers=headers), _response({"login": "review-bot"})]
    )
    delays: list[float] = []
    client = _client(transport, sleep=delays.append, now_epoch=lambda: 1000.0)

    assert client.get_authenticated_actor() == "review-bot"
    assert delays == [expected_delay]
    assert client.get_metrics().retry_wait_ms == int(expected_delay * 1000)


@pytest.mark.case("CT-M4-001", "CT-M4-002")
def test_primary_rate_limit_403_is_retried_after_reset() -> None:
    transport = _Transport(
        [
            _response(
                {},
                status=403,
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1005"},
            ),
            _response({"login": "review-bot"}),
        ]
    )
    delays: list[float] = []

    actor = _client(transport, sleep=delays.append, now_epoch=lambda: 1000.0)
    assert actor.get_authenticated_actor() == "review-bot"
    assert delays == [5.0]


@pytest.mark.case("CT-M4-001")
def test_rate_limit_wait_beyond_ceiling_fails_without_sleeping() -> None:
    transport = _Transport([_response({}, status=429, headers={"Retry-After": "3600"})])
    delays: list[float] = []

    with pytest.raises(
        ScmClientError, match=re.escape(Messages.SCM_RATE_LIMIT_EXHAUSTED)
    ) as raised:
        _client(transport, sleep=delays.append).get_authenticated_actor()

    assert raised.value.is_retryable is True
    assert delays == []
    assert len(transport.requests) == 1


@pytest.mark.case("CT-M4-001")
@pytest.mark.parametrize("change_id", ["0", "not-number"])
def test_invalid_pull_request_number_is_rejected(change_id: str) -> None:
    client = _client(_Transport([]))

    with pytest.raises(ScmClientError, match=re.escape(Messages.SCM_INVALID_CHANGE_ID)):
        client.get_change(ChangeRef(ScmProvider.GITHUB, "owner/repository", change_id))


@pytest.mark.case("CT-M4-001")
def test_client_rejects_blank_token_and_uses_default_transport() -> None:
    config = ScmConfig(
        enabled=True,
        repository_external_id="owner/repository",
        bot_actor_external_id="review-bot",
        max_response_bytes=4096,
    )

    with pytest.raises(ScmClientError, match=re.escape(Messages.SCM_TOKEN_REQUIRED)):
        GitHubScmClient(config, "  ")
    client = GitHubScmClient(config, "token")

    assert isinstance(client._transport, UrllibHttpTransport)
    assert client._transport._max_response_bytes == 4096


@pytest.mark.case("CT-M4-001")
def test_repository_mismatch_is_rejected_before_request() -> None:
    client = _client(_Transport([]))
    ref = ChangeRef(ScmProvider.GITHUB, "other/repository", "42")

    with pytest.raises(ScmClientError, match=re.escape(Messages.SCM_REPOSITORY_MISMATCH)):
        client.update_comment(
            ref,
            UpdateComment(PublicationKind.SUMMARY, "10", "更新"),
        )


@pytest.mark.case("CT-M4-001")
@pytest.mark.parametrize(
    "document",
    [
        [],
        {"login": ""},
        {"login": 1},
    ],
)
def test_json_object_and_required_string_are_validated(document: object) -> None:
    with pytest.raises(ScmClientError, match=re.escape(Messages.SCM_INVALID_RESPONSE)):
        _client(_Transport([_response(document)])).get_authenticated_actor()


@pytest.mark.case("CT-M4-001")
@pytest.mark.parametrize("body", [b"not-json", b'"\xff"'])
def test_invalid_json_or_utf8_is_rejected(body: bytes) -> None:
    transport = _Transport([HttpResponse(200, {}, body)])

    with pytest.raises(ScmClientError, match=re.escape(Messages.SCM_INVALID_RESPONSE)):
        _client(transport).get_authenticated_actor()


@pytest.mark.case("CT-M4-001", "CT-M4-002")
def test_non_retryable_transport_error_is_not_retried() -> None:
    transport = _Transport([ScmClientError("permanent")])

    with pytest.raises(ScmClientError, match="permanent"):
        _client(transport).get_authenticated_actor()

    assert len(transport.requests) == 1


@pytest.mark.case("CT-M4-001", "CT-M4-002")
def test_retryable_transport_error_stops_at_configured_limit() -> None:
    transport = _Transport([ScmClientError("temporary", is_retryable=True)] * 3)
    delays: list[float] = []

    with pytest.raises(ScmClientError, match="temporary"):
        _client(transport, sleep=delays.append).get_authenticated_actor()

    assert len(transport.requests) == 3
    assert delays == [0.25, 0.5]


@pytest.mark.case("CT-M4-001")
def test_empty_event_page_without_cursor_returns_no_cursor() -> None:
    page = _client(_Transport([_response([])])).list_comment_events(_ref(), cursor=None)

    assert page.events == ()
    assert page.next_cursor is None


@pytest.mark.case("CT-M4-001")
def test_event_page_requires_json_array() -> None:
    with pytest.raises(ScmClientError, match=re.escape(Messages.SCM_INVALID_RESPONSE)):
        _client(_Transport([_response({})])).list_comment_events(_ref(), cursor=None)


@pytest.mark.case("CT-M4-001")
@pytest.mark.parametrize("cursor", ["invalid", "2026-09-25T01:02:03"])
def test_event_cursor_requires_timezone_aware_iso8601(cursor: str) -> None:
    with pytest.raises(ScmClientError, match=re.escape(Messages.SCM_EVENT_CURSOR_INVALID)):
        _client(_Transport([])).list_comment_events(_ref(), cursor=cursor)


@pytest.mark.case("CT-M4-001")
@pytest.mark.parametrize(
    "document",
    [
        {"base": [], "head": {}},
        {"base": {"sha": 1}, "head": {}},
        {"base": {"sha": _BASE_SHA}, "head": {"sha": _HEAD_SHA, "repo": []}},
        {
            "base": {"sha": _BASE_SHA},
            "head": {"sha": _HEAD_SHA, "repo": {"full_name": "fork/repository"}},
            "state": "unknown",
            "draft": False,
            "updated_at": "2026-09-25T01:02:03Z",
        },
        {
            "base": {"sha": _BASE_SHA},
            "head": {"sha": _HEAD_SHA, "repo": {"full_name": "fork/repository"}},
            "state": "open",
            "draft": 0,
            "updated_at": "2026-09-25T01:02:03Z",
        },
        {
            "base": {"sha": _BASE_SHA},
            "head": {"sha": _HEAD_SHA, "repo": {"full_name": "fork/repository"}},
            "state": "open",
            "draft": False,
            "updated_at": "invalid",
        },
        {
            "base": {"sha": _BASE_SHA},
            "head": {"sha": _HEAD_SHA, "repo": {"full_name": "fork/repository"}},
            "state": "open",
            "draft": False,
            "updated_at": "2026-09-25T01:02:03",
        },
    ],
)
def test_change_response_fields_are_strictly_validated(document: object) -> None:
    with pytest.raises(ScmClientError, match=re.escape(Messages.SCM_INVALID_RESPONSE)):
        _client(_Transport([_response(document)])).get_change(_ref())


@pytest.mark.case("CT-M4-001")
@pytest.mark.parametrize(
    "document",
    [
        {"id": True, "html_url": "https://example.test"},
        {"id": "10", "html_url": "https://example.test"},
        {"id": 10, "html_url": ""},
    ],
)
def test_published_comment_response_is_strictly_validated(document: object) -> None:
    with pytest.raises(ScmClientError, match=re.escape(Messages.SCM_INVALID_RESPONSE)):
        _client(_Transport([_response(document, status=201)])).create_comment(
            _ref(), CreateComment(PublicationKind.SUMMARY, "概要")
        )


@pytest.mark.case("CT-M4-001")
@pytest.mark.parametrize(
    "event",
    [
        "not-object",
        {"id": True},
        {
            "id": 1,
            "in_reply_to_id": True,
            "body": "本文",
            "updated_at": "2026-09-25T01:02:03Z",
            "user": {"login": "reviewer"},
        },
    ],
)
def test_comment_event_response_is_strictly_validated(event: object) -> None:
    with pytest.raises(ScmClientError, match=re.escape(Messages.SCM_INVALID_RESPONSE)):
        _client(_Transport([_response([event])])).list_comment_events(_ref(), cursor=None)


@pytest.mark.case("CT-M4-001")
def test_comment_event_accepts_non_reply_comment() -> None:
    event = {
        "id": 1,
        "body": "本文",
        "updated_at": "2026-09-25T01:02:03Z",
        "user": {"login": "reviewer"},
    }

    page = _client(_Transport([_response([event])])).list_comment_events(_ref(), cursor=None)

    assert page.events[0].target_comment_external_id is None


@pytest.mark.case("CT-M4-001")
def test_create_inline_comment_defensively_requires_position() -> None:
    request = CreateComment(PublicationKind.SUMMARY, "本文")
    object.__setattr__(request, "kind", PublicationKind.INLINE)

    with pytest.raises(ScmClientError, match=re.escape(Messages.SCM_INVALID_RESPONSE)):
        _client(_Transport([])).create_comment(_ref(), request)


@pytest.mark.case("CT-M4-001")
def test_find_created_comment_matches_exact_body_and_configured_bot() -> None:
    transport = _Transport(
        [
            _response(
                [
                    {
                        "id": 1,
                        "html_url": "https://github.test/other",
                        "body": "同じ本文",
                        "user": {"login": "other-user"},
                    },
                    {
                        "id": 2,
                        "html_url": "https://github.test/mismatch",
                        "body": "別本文",
                        "user": {"login": "review-bot"},
                    },
                    {
                        "id": 3,
                        "html_url": "https://github.test/match",
                        "body": "同じ本文",
                        "user": {"login": "review-bot"},
                    },
                ]
            ),
            _response([]),
        ]
    )
    client = _client(transport)

    summary = client.find_created_comment(
        _ref(), CreateComment(PublicationKind.SUMMARY, "同じ本文")
    )
    inline = client.find_created_comment(
        _ref(),
        CreateComment(
            PublicationKind.INLINE,
            "行コメント",
            CommentPosition(NormalizedPath("src/app.py"), 10, _HEAD_SHA),
        ),
    )

    assert summary is not None
    assert summary.comment_external_id == "3"
    assert inline is None
    query = "sort=created&direction=desc&per_page=100"
    assert transport.requests[0][1].endswith(f"/issues/42/comments?{query}")
    assert transport.requests[1][1].endswith(f"/pulls/42/comments?{query}")


@pytest.mark.case("CT-M4-001")
def test_find_created_comment_rejects_ambiguous_matches() -> None:
    comment = {
        "id": 1,
        "html_url": "https://github.test/match",
        "body": "同じ本文",
        "user": {"login": "review-bot"},
    }
    another = {**comment, "id": 2}

    with pytest.raises(ScmClientError, match=re.escape(Messages.SCM_INVALID_RESPONSE)):
        _client(_Transport([_response([comment, another])])).find_created_comment(
            _ref(), CreateComment(PublicationKind.SUMMARY, "同じ本文")
        )


@pytest.mark.case("CT-M4-001")
@pytest.mark.parametrize("document", [{}, ["invalid-comment"]])
def test_find_created_comment_validates_list_contract(document: object) -> None:
    with pytest.raises(ScmClientError, match=re.escape(Messages.SCM_INVALID_RESPONSE)):
        _client(_Transport([_response(document)])).find_created_comment(
            _ref(), CreateComment(PublicationKind.SUMMARY, "本文")
        )


@pytest.mark.case("CT-M4-001")
def test_create_comment_does_not_blindly_retry_connection_failure() -> None:
    transport = _Transport([ScmClientError("lost response", is_retryable=True)])
    delays: list[float] = []

    client = _client(transport, sleep=delays.append)
    with pytest.raises(ScmClientError, match="lost response"):
        client.create_comment(_ref(), CreateComment(PublicationKind.SUMMARY, "本文"))

    assert len(transport.requests) == 1
    assert delays == []
    assert client.get_metrics().call_count == 1
    assert client.get_metrics().retry_count == 0


@pytest.mark.case("CT-M4-001")
def test_create_comment_does_not_blindly_retry_server_error() -> None:
    transport = _Transport([_response({}, status=500)])
    delays: list[float] = []

    with pytest.raises(
        ScmClientError, match=re.escape(Messages.SCM_API_TEMPORARY_FAILURE_EXHAUSTED)
    ):
        _client(transport, sleep=delays.append).create_comment(
            _ref(), CreateComment(PublicationKind.SUMMARY, "本文")
        )

    assert len(transport.requests) == 1
    assert delays == []


def _client(
    transport: _Transport,
    *,
    sleep: Callable[[float], None] | None = None,
    now_epoch: Callable[[], float] = lambda: 0.0,
) -> GitHubScmClient:
    config = ScmConfig(
        enabled=True,
        repository_external_id="owner/repository",
        bot_actor_external_id="review-bot",
    )
    if sleep is None:
        return GitHubScmClient(config, "token", transport=transport, now_epoch=now_epoch)
    return GitHubScmClient(config, "token", transport=transport, sleep=sleep, now_epoch=now_epoch)


def _ref() -> ChangeRef:
    return ChangeRef(ScmProvider.GITHUB, "owner/repository", "42")


def _response(
    document: object,
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
) -> HttpResponse:
    return HttpResponse(status, dict(headers or {}), json.dumps(document).encode())
