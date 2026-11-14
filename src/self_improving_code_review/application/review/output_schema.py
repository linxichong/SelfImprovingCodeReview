"""レビュー応答用の JSON スキーマ.

スキーマは意図的に閉じており、追加プロパティを拒否し、
不正または悪意のある応答によって後続処理が肥大化しないよう、
すべての文字列に長さ上限を設ける.
"""

from __future__ import annotations

from typing import Any, Never

from self_improving_code_review.domain.common.enums import Severity
from self_improving_code_review.domain.common.errors import InvalidLlmOutputError

__all__ = [
    "REVIEW_OUTPUT_SCHEMA_VERSION",
    "build_review_output_schema",
    "validate_review_output_payload",
]

REVIEW_OUTPUT_SCHEMA_VERSION = 1

_MAX_PATH_LENGTH = 512
_MAX_TITLE_LENGTH = 160
_MAX_TEXT_LENGTH = 2000
_MAX_QUOTE_LENGTH = 600
_MAX_ENTITY_LENGTH = 200


def build_review_output_schema(*, max_findings: int) -> dict[str, Any]:
    """レビュー出力スキーマを返す.

    Args:
        max_findings: 1 つのレビュー単位から受け入れる指摘の最大件数.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "findings"],
        "properties": {
            "schema_version": {"const": REVIEW_OUTPUT_SCHEMA_VERSION},
            "findings": {
                "type": "array",
                "maxItems": max_findings,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "file",
                        "start_line",
                        "end_line",
                        "category",
                        "issue_type",
                        "rule_id",
                        "primary_entity",
                        "severity",
                        "title",
                        "description",
                        "evidence",
                        "suggestion",
                        "needs_context",
                    ],
                    "properties": {
                        "file": {"type": "string", "minLength": 1, "maxLength": _MAX_PATH_LENGTH},
                        "start_line": {"type": "integer", "minimum": 1},
                        "end_line": {"type": "integer", "minimum": 1},
                        "category": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": _MAX_ENTITY_LENGTH,
                        },
                        "issue_type": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": _MAX_ENTITY_LENGTH,
                        },
                        "rule_id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": _MAX_ENTITY_LENGTH,
                            "description": (
                                "applicable_rule_ids に含まれる ID だけを使用すること。"
                                "applicable_rule_ids が空の場合は GENERAL.<ISSUE_TYPE> "
                                "だけを使用し、プロジェクトルール ID を作らないこと。"
                            ),
                        },
                        "primary_entity": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": _MAX_ENTITY_LENGTH,
                        },
                        "severity": {"enum": [item.value for item in Severity]},
                        "title": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": _MAX_TITLE_LENGTH,
                        },
                        "description": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": _MAX_TEXT_LENGTH,
                        },
                        "evidence": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["quote", "reason"],
                            "properties": {
                                "quote": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": _MAX_QUOTE_LENGTH,
                                    "description": (
                                        "追加行のコード本文をそのまま引用すること。"
                                        "行頭の Unified Diff 記号 + は含めず、"
                                        "複数行では連続する追加行だけを使用すること。"
                                    ),
                                },
                                "reason": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": _MAX_TEXT_LENGTH,
                                },
                            },
                        },
                        "suggestion": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": _MAX_TEXT_LENGTH,
                        },
                        "needs_context": {"type": "boolean"},
                    },
                },
            },
        },
    }


def validate_review_output_payload(payload: object, *, max_findings: int) -> None:
    """LLM の送信内容を M1 の閉じた応答契約に照らして検証する."""
    if not isinstance(payload, dict):
        _invalid("review response must be an object")
    expected_top = {"schema_version", "findings"}
    if set(payload) != expected_top:
        _invalid("review response has missing or unknown top-level properties")
    if payload["schema_version"] != REVIEW_OUTPUT_SCHEMA_VERSION:
        _invalid("review response has an unsupported schema_version")
    findings = payload["findings"]
    if not isinstance(findings, list) or len(findings) > max_findings:
        _invalid("review response findings must be a bounded array")
    for finding in findings:
        _validate_finding(finding)


def _validate_finding(finding: object) -> None:
    """1件の閉じた指摘オブジェクトを検証する."""
    expected = {
        "file",
        "start_line",
        "end_line",
        "category",
        "issue_type",
        "rule_id",
        "primary_entity",
        "severity",
        "title",
        "description",
        "evidence",
        "suggestion",
        "needs_context",
    }
    if not isinstance(finding, dict) or set(finding) != expected:
        _invalid("finding has missing or unknown properties")
    string_limits = {
        "file": _MAX_PATH_LENGTH,
        "category": _MAX_ENTITY_LENGTH,
        "issue_type": _MAX_ENTITY_LENGTH,
        "rule_id": _MAX_ENTITY_LENGTH,
        "primary_entity": _MAX_ENTITY_LENGTH,
        "title": _MAX_TITLE_LENGTH,
        "description": _MAX_TEXT_LENGTH,
        "suggestion": _MAX_TEXT_LENGTH,
    }
    for name, maximum in string_limits.items():
        _validate_string(f"finding {name}", finding[name], maximum)
    if type(finding["start_line"]) is not int or type(finding["end_line"]) is not int:
        _invalid("finding line values must be integers")
    if finding["start_line"] < 1 or finding["end_line"] < finding["start_line"]:
        _invalid("finding line range is invalid")
    if finding["severity"] not in {item.value for item in Severity}:
        _invalid("finding severity is invalid")
    if type(finding["needs_context"]) is not bool:
        _invalid("finding needs_context must be boolean")
    evidence = finding["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != {"quote", "reason"}:
        _invalid("finding evidence must contain only quote and reason")
    _validate_string("finding evidence quote", evidence["quote"], _MAX_QUOTE_LENGTH)
    _validate_string("finding evidence reason", evidence["reason"], _MAX_TEXT_LENGTH)


def _validate_string(name: str, value: object, maximum: int) -> None:
    """上限内かつ空でない1件の応答文字列を検証する."""
    if not isinstance(value, str) or not value or len(value) > maximum:
        _invalid(f"{name} must be a non-empty string of at most {maximum} characters")


def _invalid(message: str) -> Never:
    """安定した形式の不正出力エラーを送出する."""
    raise InvalidLlmOutputError(message)
