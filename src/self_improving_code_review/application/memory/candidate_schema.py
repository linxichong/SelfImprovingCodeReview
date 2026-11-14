"""メモリ候補の JSON スキーマと検証.

スキーマは意図的に閉じており、追加プロパティを拒否する. 適用範囲の
パターン数と文字列長を制限し、不正な応答で後続処理が肥大化しないようにする.
"""

from __future__ import annotations

from typing import Any, Never

from self_improving_code_review.domain.common.enums import MemoryCategory, MemoryPolarity
from self_improving_code_review.domain.common.errors import InvalidLlmOutputError

__all__ = [
    "MEMORY_CANDIDATE_SCHEMA_VERSION",
    "build_memory_candidate_schema",
    "validate_memory_candidate_payload",
]

MEMORY_CANDIDATE_SCHEMA_VERSION = 1

_MAX_FACT_LENGTH = 500
_MAX_INSTRUCTION_LENGTH = 500
_MAX_EVIDENCE_SUMMARY_LENGTH = 500
_MAX_SCOPE_ITEMS = 20
_MAX_SCOPE_ITEM_LENGTH = 200
_SCOPE_GROUPS: tuple[str, ...] = (
    "languages",
    "frameworks",
    "paths",
    "symbols",
    "keywords",
    "risk_tags",
)


def build_memory_candidate_schema() -> dict[str, Any]:
    """メモリ候補の閉じた出力スキーマを返す."""
    scope_group = {
        "type": "array",
        "maxItems": _MAX_SCOPE_ITEMS,
        "items": {"type": "string", "minLength": 1, "maxLength": _MAX_SCOPE_ITEM_LENGTH},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "category",
            "scope",
            "fact",
            "review_instruction",
            "polarity",
            "confidence",
            "evidence_summary",
        ],
        "properties": {
            "schema_version": {"const": MEMORY_CANDIDATE_SCHEMA_VERSION},
            "category": {"enum": [item.value for item in MemoryCategory]},
            "scope": {
                "type": "object",
                "additionalProperties": False,
                "required": list(_SCOPE_GROUPS),
                "properties": dict.fromkeys(_SCOPE_GROUPS, scope_group),
            },
            "fact": {"type": "string", "minLength": 1, "maxLength": _MAX_FACT_LENGTH},
            "review_instruction": {
                "type": "string",
                "minLength": 1,
                "maxLength": _MAX_INSTRUCTION_LENGTH,
            },
            "polarity": {"enum": [item.value for item in MemoryPolarity]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "evidence_summary": {
                "type": "string",
                "minLength": 1,
                "maxLength": _MAX_EVIDENCE_SUMMARY_LENGTH,
            },
        },
    }


def validate_memory_candidate_payload(payload: object) -> None:
    """LLM の送信内容を閉じた候補契約に照らして検証する.

    Raises:
        InvalidLlmOutputError: 最上位構造、列挙値、文字列長、適用範囲、
            確信度のいずれかが契約に違反する場合.
    """
    if not isinstance(payload, dict):
        _invalid("memory candidate must be an object")
    expected = {
        "schema_version",
        "category",
        "scope",
        "fact",
        "review_instruction",
        "polarity",
        "confidence",
        "evidence_summary",
    }
    if set(payload) != expected:
        _invalid("memory candidate has missing or unknown top-level properties")
    if payload["schema_version"] != MEMORY_CANDIDATE_SCHEMA_VERSION:
        _invalid("memory candidate has an unsupported schema_version")
    if payload["category"] not in {item.value for item in MemoryCategory}:
        _invalid("memory candidate category is invalid")
    if payload["polarity"] not in {item.value for item in MemoryPolarity}:
        _invalid("memory candidate polarity is invalid")

    _validate_string("memory candidate fact", payload["fact"], _MAX_FACT_LENGTH)
    _validate_string(
        "memory candidate review_instruction",
        payload["review_instruction"],
        _MAX_INSTRUCTION_LENGTH,
    )
    _validate_string(
        "memory candidate evidence_summary",
        payload["evidence_summary"],
        _MAX_EVIDENCE_SUMMARY_LENGTH,
    )
    _validate_confidence(payload["confidence"])
    _validate_scope(payload["scope"])


def _validate_scope(scope: object) -> None:
    """適用範囲が文字列配列の閉じた対応表であることを検証する.

    Raises:
        InvalidLlmOutputError: グループが欠落または未知、要素が文字列でない、
            上限を超える、あるいはすべてのグループが空の場合.
    """
    if not isinstance(scope, dict) or set(scope) != set(_SCOPE_GROUPS):
        _invalid("memory candidate scope has missing or unknown groups")
    has_value = False
    for group in _SCOPE_GROUPS:
        values = scope[group]
        if not isinstance(values, list) or len(values) > _MAX_SCOPE_ITEMS:
            _invalid(f"memory candidate scope {group} must be a bounded array")
        for value in values:
            _validate_string(
                f"memory candidate scope {group} item",
                value,
                _MAX_SCOPE_ITEM_LENGTH,
            )
        has_value = has_value or bool(values)
    if not has_value:
        _invalid("memory candidate scope must define at least one group")


def _validate_confidence(value: object) -> None:
    """確信度が 0.0 から 1.0 の実数であることを検証する.

    Raises:
        InvalidLlmOutputError: 真偽値、数値以外、または範囲外の場合.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        _invalid("memory candidate confidence must be a number")
    if not 0.0 <= float(value) <= 1.0:
        _invalid("memory candidate confidence must be between 0.0 and 1.0")


def _validate_string(name: str, value: object, maximum: int) -> None:
    """上限内かつ空でない 1 件の応答文字列を検証する.

    Raises:
        InvalidLlmOutputError: 文字列でない、空、または上限を超える場合.
    """
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        _invalid(f"{name} must be a non-empty string of at most {maximum} characters")


def _invalid(message: str) -> Never:
    """候補契約違反を一貫した例外へ変換する.

    Raises:
        InvalidLlmOutputError: 常に送出する.
    """
    raise InvalidLlmOutputError(message)
