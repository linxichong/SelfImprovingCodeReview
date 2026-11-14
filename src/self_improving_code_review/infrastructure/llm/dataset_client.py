"""固定データセットを使用するネットワーク非接続 LLM アダプター."""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from self_improving_code_review.application.ports.llm_client import (
    LlmRequest,
    LlmResult,
    LlmUsage,
)
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.messages import Messages

__all__ = ["DatasetLlmClient"]


class _StrictDatasetModel(BaseModel):
    """未知フィールドを拒否するデータセットモデル."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class _DatasetUsage(_StrictDatasetModel):
    """固定応答の使用量."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal("0"), ge=0)


class _DatasetResult(_StrictDatasetModel):
    """一つの固定 LLM 応答."""

    payload: dict[str, Any]
    usage: _DatasetUsage = _DatasetUsage()
    attempt: int = Field(default=1, ge=1)


class _DatasetCaseV1(_StrictDatasetModel):
    """要求本文との一致条件を持つ固定応答ケース."""

    case_id: str = Field(min_length=1)
    match_all: tuple[str, ...] = Field(min_length=1)
    result: _DatasetResult


class _DatasetDocumentV1(_StrictDatasetModel):
    """ローカル LLM データセットのルート構造."""

    schema_version: Literal[1]
    model: str = Field(min_length=1)
    default_result: _DatasetResult
    cases: tuple[_DatasetCaseV1, ...] = ()


class _DatasetResponseV2(_StrictDatasetModel):
    """レビュー単位、呼び出し種別、ターンで識別する固定応答."""

    review_unit_id: str = Field(min_length=1)
    call_kind: str = Field(min_length=1)
    turn_number: int = Field(ge=1)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: _DatasetResult

    @property
    def key(self) -> tuple[str, str, int]:
        """ケース内で一意となる応答キーを返す."""
        return (self.review_unit_id, self.call_kind, self.turn_number)


class _DatasetCaseV2(_StrictDatasetModel):
    """複数ターン応答列を持つ固定ケース."""

    case_id: str = Field(min_length=1)
    match_all: tuple[str, ...] = Field(min_length=1)
    responses: tuple[_DatasetResponseV2, ...] = Field(min_length=1)


class _DatasetDocumentV2(_StrictDatasetModel):
    """複数ターン対応ローカル LLM データセットのルート構造."""

    schema_version: Literal[2]
    model: str = Field(min_length=1)
    default_result: _DatasetResult
    cases: tuple[_DatasetCaseV2, ...] = ()


type _DatasetDocument = _DatasetDocumentV1 | _DatasetDocumentV2


class DatasetLlmClient:
    """要求内容に一致する固定応答を返し、外部通信を行わない.

    開発と自動テストで実 LLM のトークンを消費しないためのアダプター。
    システム指示と利用者内容に対して match_all 条件を上から評価し、
    最初に一致した固定応答を返す。
    """

    def __init__(self, dataset_path: Path) -> None:
        """検証済みのローカルデータセットを読み込む.

        Raises:
            InvalidInputError: ファイルを読み取れない、JSON またはスキーマが不正な場合.
        """
        try:
            raw_text = dataset_path.read_text(encoding="utf-8")
            document = json.loads(raw_text)
            schema_version = document.get("schema_version") if isinstance(document, dict) else None
            document_type = _DatasetDocumentV2 if schema_version == 2 else _DatasetDocumentV1
            self._dataset: _DatasetDocument = document_type.model_validate(document)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
            raise InvalidInputError(Messages.invalid_llm_dataset(dataset_path)) from exc
        self._case_ids = [case.case_id for case in self._dataset.cases]
        if len(self._case_ids) != len(set(self._case_ids)):
            raise InvalidInputError(Messages.duplicate_dataset_case_id(dataset_path))
        if isinstance(self._dataset, _DatasetDocumentV2):
            for case in self._dataset.cases:
                keys = [response.key for response in case.responses]
                if len(keys) != len(set(keys)):
                    raise InvalidInputError(Messages.duplicate_dataset_response_key(dataset_path))
        self._used_response_keys: set[tuple[str, str, str, int]] = set()
        self._activated_case_ids: set[str] = set()
        self._lock = Lock()
        self._is_closed = False

    @property
    def model(self) -> str:
        """データセットに記録された模擬モデル識別子を返す."""
        return self._dataset.model

    def close(self) -> None:
        """選択済みケースの全応答が使用済みであることを検証する.

        Raises:
            InvalidInputError: 複数ターンケースに未使用応答が残っている場合.
        """
        with self._lock:
            if self._is_closed:
                return
            self._is_closed = True
            if not isinstance(self._dataset, _DatasetDocumentV2):
                return
            expected = {
                (case.case_id, *response.key)
                for case in self._dataset.cases
                if case.case_id in self._activated_case_ids
                for response in case.responses
            }
            if expected != self._used_response_keys:
                raise InvalidInputError(Messages.unused_dataset_responses())

    def generate_structured(self, request: LlmRequest) -> LlmResult:
        """要求に最初に一致したケース、または既定応答を返す.

        Args:
            request: 照合対象となるシステム指示と利用者内容を含む要求。

        Returns:
            呼び出し元による変更がデータセットへ波及しないよう、
            応答本文を複製したプロバイダー非依存の結果。
        """
        with self._lock:
            if self._is_closed:
                raise InvalidInputError(Messages.closed_dataset_client())
            selected = self._select_result(request)
        return LlmResult(
            payload=deepcopy(selected.payload),
            model=self._dataset.model,
            usage=LlmUsage(
                input_tokens=selected.usage.input_tokens,
                output_tokens=selected.usage.output_tokens,
                latency_ms=selected.usage.latency_ms,
                cost_usd=selected.usage.cost_usd,
            ),
            attempt=selected.attempt,
        )

    def _select_result(self, request: LlmRequest) -> _DatasetResult:
        """スキーマ版に応じて要求と一致する固定応答を選択する."""
        searchable_text = f"{request.system_instruction}\n{request.user_content}"
        if isinstance(self._dataset, _DatasetDocumentV1):
            for case in self._dataset.cases:
                if all(marker in searchable_text for marker in case.match_all):
                    return case.result
            return self._dataset.default_result

        selected_case = next(
            (
                case
                for case in self._dataset.cases
                if all(marker in searchable_text for marker in case.match_all)
            ),
            None,
        )
        if selected_case is None:
            return self._dataset.default_result
        call_kind, separator, review_unit_id = request.request_label.rpartition(":")
        if not separator or not call_kind or not review_unit_id:
            raise InvalidInputError(Messages.invalid_dataset_request_label())
        self._activated_case_ids.add(selected_case.case_id)
        unused = [
            response
            for response in selected_case.responses
            if response.call_kind == call_kind
            and response.review_unit_id == review_unit_id
            and (selected_case.case_id, *response.key) not in self._used_response_keys
        ]
        if not unused:
            raise InvalidInputError(Messages.missing_dataset_response())
        selected_response = min(unused, key=lambda response: response.turn_number)
        actual_hash = hash_text(searchable_text)
        if selected_response.request_hash != actual_hash:
            raise InvalidInputError(Messages.mismatched_dataset_request_hash())
        self._used_response_keys.add((selected_case.case_id, *selected_response.key))
        return selected_response.result
