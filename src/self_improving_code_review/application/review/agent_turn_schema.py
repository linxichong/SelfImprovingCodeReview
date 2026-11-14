"""M3 レビュー担当ターンの閉じたスキーマと解析."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from self_improving_code_review.application.review.context_tools import ToolIntent
from self_improving_code_review.application.review.output_schema import (
    build_review_output_schema,
    validate_review_output_payload,
)
from self_improving_code_review.domain.common.errors import InvalidLlmOutputError
from self_improving_code_review.messages import Messages

__all__ = ["AgentTurn", "build_agent_turn_schema", "parse_agent_turn"]

_TOP_LEVEL_KEYS = frozenset({"schema_version", "action", "tool_request", "findings"})
_TOOL_KEYS = frozenset({"tool_name", "path", "path_scope", "query", "start_line", "end_line"})


@dataclass(frozen=True, slots=True)
class AgentTurn:
    """ツール要求または最終候補のいずれか一方を持つターン."""

    action: Literal["tool-request", "final"]
    tool_intent: ToolIntent | None
    final_payload: dict[str, Any] | None


def build_agent_turn_schema(*, max_findings: int, allow_tool_request: bool) -> dict[str, Any]:
    """レビュー担当ターン用の JSON スキーマを返す."""
    review_schema = build_review_output_schema(max_findings=max_findings)
    finding_schema = review_schema["properties"]["findings"]
    actions = ["final", "tool-request"] if allow_tool_request else ["final"]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "action", "tool_request", "findings"],
        "properties": {
            "schema_version": {"const": 1},
            "action": {"enum": actions},
            "tool_request": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "required": sorted(_TOOL_KEYS),
                "properties": {
                    "tool_name": {
                        "enum": [
                            "read_file",
                            "get_changed_file",
                            "find_symbol",
                            "search_code",
                        ]
                    },
                    "path": {"type": ["string", "null"], "maxLength": 512},
                    "path_scope": {
                        "type": "array",
                        "maxItems": 10,
                        "items": {"type": "string", "maxLength": 512},
                    },
                    "query": {"type": ["string", "null"], "maxLength": 256},
                    "start_line": {"type": ["integer", "null"], "minimum": 1},
                    "end_line": {"type": ["integer", "null"], "minimum": 1},
                },
            },
            "findings": finding_schema,
        },
        "allOf": [
            {
                "if": {"properties": {"action": {"const": "tool-request"}}},
                "then": {
                    "properties": {
                        "tool_request": {"type": "object"},
                        "findings": {"maxItems": 0},
                    }
                },
            },
            {
                "if": {"properties": {"action": {"const": "final"}}},
                "then": {"properties": {"tool_request": {"type": "null"}}},
            },
        ],
    }


def parse_agent_turn(
    payload: dict[str, Any],
    *,
    max_findings: int,
    allow_tool_request: bool,
) -> AgentTurn:
    """未信頼なターン応答を排他的な内部型へ変換する."""
    if frozenset(payload) != _TOP_LEVEL_KEYS or payload.get("schema_version") != 1:
        raise InvalidLlmOutputError(Messages.AGENT_TURN_INVALID)
    action = payload.get("action")
    findings = payload.get("findings")
    tool_request = payload.get("tool_request")
    if not isinstance(findings, list):
        raise InvalidLlmOutputError(Messages.AGENT_TURN_INVALID)
    if action == "final":
        if tool_request is not None:
            raise InvalidLlmOutputError(Messages.AGENT_TURN_INVALID)
        final_payload = {"schema_version": 1, "findings": findings}
        validate_review_output_payload(final_payload, max_findings=max_findings)
        return AgentTurn("final", None, final_payload)
    if action != "tool-request" or not allow_tool_request or findings:
        raise InvalidLlmOutputError(Messages.AGENT_TURN_INVALID)
    intent = _parse_tool_intent(tool_request)
    return AgentTurn("tool-request", intent, None)


def _parse_tool_intent(value: object) -> ToolIntent:
    """ツール要求オブジェクトを型と項目数を検証して変換する."""
    if not isinstance(value, dict) or frozenset(value) != _TOOL_KEYS:
        raise InvalidLlmOutputError(Messages.AGENT_TURN_INVALID)
    tool_name = value["tool_name"]
    path = value["path"]
    path_scope = value["path_scope"]
    query = value["query"]
    start_line = value["start_line"]
    end_line = value["end_line"]
    if not isinstance(tool_name, str):
        raise InvalidLlmOutputError(Messages.AGENT_TURN_INVALID)
    if path is not None and not isinstance(path, str):
        raise InvalidLlmOutputError(Messages.AGENT_TURN_INVALID)
    if query is not None and not isinstance(query, str):
        raise InvalidLlmOutputError(Messages.AGENT_TURN_INVALID)
    if not isinstance(path_scope, list) or any(not isinstance(item, str) for item in path_scope):
        raise InvalidLlmOutputError(Messages.AGENT_TURN_INVALID)
    if start_line is not None and type(start_line) is not int:
        raise InvalidLlmOutputError(Messages.AGENT_TURN_INVALID)
    if end_line is not None and type(end_line) is not int:
        raise InvalidLlmOutputError(Messages.AGENT_TURN_INVALID)
    return ToolIntent(
        tool_name=tool_name,
        path=path,
        path_scope=tuple(path_scope),
        query=query,
        start_line=start_line,
        end_line=end_line,
    )
