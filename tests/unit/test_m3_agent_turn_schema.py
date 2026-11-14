"""M3 レビュー担当ターンの閉じた応答契約を検証する。"""

from __future__ import annotations

import pytest

from self_improving_code_review.application.review.agent_turn_schema import (
    build_agent_turn_schema,
    parse_agent_turn,
)
from self_improving_code_review.domain.common.errors import InvalidLlmOutputError


def _tool_request() -> dict[str, object]:
    return {
        "tool_name": "read_file",
        "path": "src/app.py",
        "path_scope": [],
        "query": None,
        "start_line": 1,
        "end_line": 20,
    }


@pytest.mark.case("UT-M3-004")
def test_schema_disables_tool_action_after_budget_exhaustion() -> None:
    enabled = build_agent_turn_schema(max_findings=3, allow_tool_request=True)
    disabled = build_agent_turn_schema(max_findings=3, allow_tool_request=False)

    assert enabled["properties"]["action"]["enum"] == ["final", "tool-request"]
    assert disabled["properties"]["action"]["enum"] == ["final"]


@pytest.mark.case("UT-M3-004")
def test_tool_request_and_final_are_parsed_exclusively() -> None:
    tool_turn = parse_agent_turn(
        {
            "schema_version": 1,
            "action": "tool-request",
            "tool_request": _tool_request(),
            "findings": [],
        },
        max_findings=3,
        allow_tool_request=True,
    )
    final_turn = parse_agent_turn(
        {
            "schema_version": 1,
            "action": "final",
            "tool_request": None,
            "findings": [],
        },
        max_findings=3,
        allow_tool_request=False,
    )

    assert tool_turn.tool_intent is not None
    assert tool_turn.tool_intent.path == "src/app.py"
    assert final_turn.final_payload == {"schema_version": 1, "findings": []}


@pytest.mark.case("UT-M3-004")
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"schema_version": 1, "action": "final", "tool_request": None, "findings": {}},
        {
            "schema_version": 1,
            "action": "final",
            "tool_request": _tool_request(),
            "findings": [],
        },
        {
            "schema_version": 1,
            "action": "tool-request",
            "tool_request": _tool_request(),
            "findings": [{"unexpected": True}],
        },
    ],
)
def test_invalid_or_nonexclusive_turn_is_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(InvalidLlmOutputError):
        parse_agent_turn(payload, max_findings=3, allow_tool_request=True)


@pytest.mark.case("UT-M3-004")
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tool_name", 1),
        ("path", 1),
        ("query", 1),
        ("path_scope", [1]),
        ("start_line", True),
        ("end_line", "2"),
    ],
)
def test_tool_request_field_types_are_closed(field: str, value: object) -> None:
    request = _tool_request()
    request[field] = value
    payload = {
        "schema_version": 1,
        "action": "tool-request",
        "tool_request": request,
        "findings": [],
    }

    with pytest.raises(InvalidLlmOutputError):
        parse_agent_turn(payload, max_findings=3, allow_tool_request=True)


@pytest.mark.case("UT-M3-004")
def test_tool_request_requires_exact_fields_and_available_budget() -> None:
    request = _tool_request()
    request.pop("query")
    payload = {
        "schema_version": 1,
        "action": "tool-request",
        "tool_request": request,
        "findings": [],
    }
    with pytest.raises(InvalidLlmOutputError):
        parse_agent_turn(payload, max_findings=3, allow_tool_request=True)

    payload["tool_request"] = _tool_request()
    with pytest.raises(InvalidLlmOutputError):
        parse_agent_turn(payload, max_findings=3, allow_tool_request=False)
