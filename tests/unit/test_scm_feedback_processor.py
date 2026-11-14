from __future__ import annotations

from types import SimpleNamespace

import pytest

from self_improving_code_review.application.feedback.models import FeedbackOutcome
from self_improving_code_review.application.scm.models import (
    ParsedFeedbackCommand,
    ScmFeedbackTarget,
)
from self_improving_code_review.cli.scm_feedback_processor import (
    ScmM2FeedbackProcessor,
    _parse_finding_id,
)
from self_improving_code_review.domain.common.enums import ExtractionStatus, FeedbackLabel
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import (
    ExtractionId,
    FeedbackId,
    ProjectId,
    RunId,
)

_RUN = RunId("00000000-0000-4000-8000-000000000001")
_TARGET = ScmFeedbackTarget("publication", str(_RUN), "B001-F001", "comment")
_COMMAND = ParsedFeedbackCommand(FeedbackLabel.TRUE_POSITIVE, "妥当な理由")


@pytest.mark.parametrize("raw", ["invalid", "Bxx-Fyy"])
def test_scm_finding_id_parser_rejects_invalid_saved_value(raw: str) -> None:
    with pytest.raises(InvalidInputError):
        _parse_finding_id(_RUN, raw)


@pytest.mark.parametrize(("run_context", "context"), [(None, "ctx"), (object(), None)])
def test_processor_rejects_missing_saved_context(
    run_context: object | None,
    context: str | None,
) -> None:
    processor = _processor(
        repository=_Repository(run_context=run_context, context=context),
        extractor=_Extractor(),
    )

    with pytest.raises(InvalidInputError):
        processor.process(_TARGET, _COMMAND, "reviewer")


def test_processor_reuses_existing_extractor() -> None:
    run_context = SimpleNamespace(
        project_id=ProjectId("project"),
        source_revision="2" * 40,
        is_test_evaluation=False,
    )
    extractor = _Extractor()
    processor = _processor(
        repository=_Repository(run_context=run_context, context="固定コンテキスト"),
        extractor=extractor,
    )

    outcome = processor.process(_TARGET, _COMMAND, "reviewer")

    assert outcome.candidate_status is ExtractionStatus.SUCCESS
    assert extractor.calls == 1


def test_processor_close_without_built_client_is_noop() -> None:
    processor = object.__new__(ScmM2FeedbackProcessor)
    processor._llm_client = None

    processor.close()


class _Recorder:
    """固定の保存済みフィードバック結果を返す."""

    def execute(self, value: object) -> FeedbackOutcome:
        """入力を受け入れ固定結果を返す."""
        del value
        return FeedbackOutcome(FeedbackId("00000000-0000-4000-8000-000000000002"), "RECORDED")


class _Repository:
    """実行および指摘コンテキストを制御する."""

    def __init__(self, *, run_context: object | None, context: str | None) -> None:
        """返却値を保持する."""
        self._run_context = run_context
        self._context = context

    def find_run_context(self, run_id: RunId) -> object | None:
        """設定済み実行コンテキストを返す."""
        del run_id
        return self._run_context

    def find_finding_context(self, run_id: RunId, finding_id: str) -> str | None:
        """設定済み指摘コンテキストを返す."""
        del run_id, finding_id
        return self._context


class _Extractor:
    """候補抽出呼出を記録する."""

    def __init__(self) -> None:
        """呼出回数を初期化する."""
        self.calls = 0

    def execute(self, request: object) -> object:
        """固定の作成成功結果を返す."""
        del request
        self.calls += 1
        return SimpleNamespace(
            extraction_id=ExtractionId("00000000-0000-4000-8000-000000000003"),
            status=ExtractionStatus.SUCCESS,
            candidate_memory_id=None,
            candidate_version=None,
            safe_error_code=None,
        )


def _processor(
    *,
    repository: _Repository,
    extractor: _Extractor,
) -> ScmM2FeedbackProcessor:
    """初期化済みの外部依存を置き換えた処理器を返す."""
    processor = object.__new__(ScmM2FeedbackProcessor)
    processor._recorder = _Recorder()
    processor._repository = repository
    processor._extractor = extractor
    processor._llm_client = None
    return processor
