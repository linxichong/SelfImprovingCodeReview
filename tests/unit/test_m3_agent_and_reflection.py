"""M3 のレビュー担当ループと自己検証処理を検証する。"""

from __future__ import annotations

import re
from collections import deque
from decimal import Decimal
from pathlib import Path

import pytest

from self_improving_code_review.application.ports.llm_client import (
    LlmRequest,
    LlmResult,
    LlmUsage,
)
from self_improving_code_review.application.review import reflect_findings as reflection_module
from self_improving_code_review.application.review import review_agent as review_agent_module
from self_improving_code_review.application.review.agent_turn_schema import AgentTurn
from self_improving_code_review.application.review.context_tools import (
    ContextFragment,
    ContextToolCoordinator,
    ContextToolSession,
    ToolResult,
)
from self_improving_code_review.application.review.prompt_loader import load_prompt_template
from self_improving_code_review.application.review.reflect_findings import FindingReflector
from self_improving_code_review.application.review.review_agent import ReviewAgentLoop
from self_improving_code_review.domain.common.enums import (
    ReflectionStatus,
    Severity,
    ToolStatus,
    ValidationStatus,
)
from self_improving_code_review.domain.common.errors import (
    InvalidLlmOutputError,
    LlmClientError,
    ReflectionBudgetExceededError,
)
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.common.values import (
    BundleId,
    FindingId,
    LineRange,
    NormalizedPath,
    RunId,
)
from self_improving_code_review.domain.finding.models import (
    CandidateFinding,
    Evidence,
    Finding,
    ValidationOutcome,
)
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.models import (
    ScannedPayload,
    ScanProfile,
    ScanResult,
    SecretFinding,
    SourcePayload,
)
from self_improving_code_review.security.path_policy import PathPolicy

RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")
BUNDLE_ID = BundleId(RUN_ID, 1)
PROMPTS = Path(__file__).parents[2] / "prompts"


class Llm:
    """応答または例外を順番に返す LLM 模擬。"""

    model = "dataset/test"

    def __init__(self, *responses: dict[str, object] | BaseException) -> None:
        self.responses = deque(responses)
        self.requests: list[LlmRequest] = []

    def generate_structured(self, request: LlmRequest) -> LlmResult:
        self.requests.append(request)
        response = self.responses.popleft()
        if isinstance(response, BaseException):
            raise response
        return LlmResult(response, self.model, LlmUsage(10, 2, 3, Decimal("0.01")), 1)


class Source:
    """単一ファイルを返す入力元。"""

    source_revision = None

    def read_text(self, _path: NormalizedPath) -> str | None:
        return "value = 1"

    def list_paths(
        self, _scope: tuple[NormalizedPath, ...], *, max_files: int
    ) -> tuple[NormalizedPath, ...]:
        return (NormalizedPath("src/app.py"),)[:max_files]


class Executor:
    """安全な短い断片を返すツール実行器。"""

    def execute(self, request: object, _source: object) -> ToolResult:
        del request
        return ToolResult(
            ToolStatus.SUCCESS,
            (ContextFragment(NormalizedPath("src/app.py"), 1, 1, "value = 1"),),
        )


class Scanner:
    """設定に応じて結果を封印または遮断する。"""

    engine_version = "test"

    def __init__(self, blocked: bool = False) -> None:
        self.blocked = blocked

    def scan_and_seal(self, payload: SourcePayload) -> tuple[ScanResult, ScannedPayload | None]:
        findings = (SecretFinding("test", payload.label, 1),) if self.blocked else ()
        result = ScanResult(hash_text(payload.text), "test", ScanProfile.STRICT, findings)
        sealed = None
        if not findings:
            sealed = ScannedPayload("scan", "test", hash_text(payload.text), payload.text)
        return result, sealed


@pytest.mark.case("UT-M3-009")
def test_agent_loop_uses_one_tool_then_returns_final(tmp_path: Path) -> None:
    llm = Llm(_tool_turn(), _final_turn())
    outcome = ReviewAgentLoop(llm, max_findings=3).execute(
        _request(),
        coordinator=ContextToolCoordinator(tmp_path, PathPolicy()),
        session=_session(blocked=False),
        run_id=RUN_ID,
        bundle_id=BUNDLE_ID,
        source_revision=None,
    )

    assert outcome.result is not None
    assert outcome.result.payload == {"schema_version": 1, "findings": []}
    assert outcome.tool_call_count == 1
    assert outcome.tool_success_count == 1
    assert outcome.dynamic_context_tokens > 0
    assert len(outcome.llm_calls) == 2
    assert "ツール結果" in llm.requests[1].user_content


@pytest.mark.case("UT-M3-009")
def test_agent_loop_repairs_invalid_turn_once(tmp_path: Path) -> None:
    llm = Llm({}, _final_turn())

    outcome = ReviewAgentLoop(llm, max_findings=3).execute(
        _request(),
        coordinator=ContextToolCoordinator(tmp_path, PathPolicy()),
        session=_session(blocked=False),
        run_id=RUN_ID,
        bundle_id=BUNDLE_ID,
        source_revision=None,
    )

    assert outcome.result is not None
    assert outcome.result.attempt == 2
    assert len(outcome.llm_calls) == 2


@pytest.mark.case("UT-M3-009")
def test_agent_loop_repairs_adapter_level_invalid_json_once(tmp_path: Path) -> None:
    """ゲートウェイ本文に JSON がない場合も次の要求で形式修復する."""
    invalid = InvalidLlmOutputError(
        "json missing",
        input_tokens=7,
        output_tokens=5,
        latency_ms=11,
        cost_usd=Decimal("0.02"),
    )
    llm = Llm(invalid, _final_turn())

    outcome = ReviewAgentLoop(llm, max_findings=3).execute(
        _request(),
        coordinator=ContextToolCoordinator(tmp_path, PathPolicy()),
        session=_session(blocked=False),
        run_id=RUN_ID,
        bundle_id=BUNDLE_ID,
        source_revision=None,
    )

    assert outcome.result is not None
    assert outcome.result.attempt == 2
    assert outcome.result.usage.input_tokens == 17
    assert outcome.result.usage.output_tokens == 7
    assert outcome.result.usage.latency_ms == 14
    assert outcome.result.usage.cost_usd == Decimal("0.03")
    assert "形式修復" in llm.requests[1].user_content


@pytest.mark.case("UT-M3-009")
def test_agent_loop_rejects_second_adapter_level_invalid_json(tmp_path: Path) -> None:
    """形式修復後も JSON を抽出できない場合は合算した測定値で失敗する."""
    llm = Llm(
        InvalidLlmOutputError("first", input_tokens=2, latency_ms=3),
        InvalidLlmOutputError("second", output_tokens=4, latency_ms=5),
    )

    with pytest.raises(InvalidLlmOutputError) as captured:
        ReviewAgentLoop(llm, max_findings=3).execute(
            _request(),
            coordinator=ContextToolCoordinator(tmp_path, PathPolicy()),
            session=_session(blocked=False),
            run_id=RUN_ID,
            bundle_id=BUNDLE_ID,
            source_revision=None,
        )

    assert captured.value.attempt == 2
    assert captured.value.input_tokens == 2
    assert captured.value.output_tokens == 4
    assert captured.value.latency_ms == 8


def test_agent_loop_stops_when_tool_result_contains_secret(tmp_path: Path) -> None:
    outcome = ReviewAgentLoop(Llm(_tool_turn()), max_findings=3).execute(
        _request(),
        coordinator=ContextToolCoordinator(tmp_path, PathPolicy()),
        session=_session(blocked=True),
        run_id=RUN_ID,
        bundle_id=BUNDLE_ID,
        source_revision=None,
    )

    assert outcome.is_blocked
    assert outcome.result is None


@pytest.mark.case("IT-M3-003", "UT-M3-009")
def test_agent_loop_stops_before_next_llm_call(tmp_path: Path) -> None:
    """並列実行の停止通知後は新しい LLM 呼出を開始しない。"""
    llm = Llm(_final_turn())

    outcome = ReviewAgentLoop(llm, max_findings=3).execute(
        _request(),
        coordinator=ContextToolCoordinator(tmp_path, PathPolicy()),
        session=_session(blocked=False),
        run_id=RUN_ID,
        bundle_id=BUNDLE_ID,
        source_revision=None,
        should_stop=lambda: True,
    )

    assert outcome.result is None
    assert outcome.llm_calls == ()
    assert llm.requests == []


@pytest.mark.case("UT-M3-009")
def test_agent_loop_rejects_invalid_tool_intent_and_provider_failures(tmp_path: Path) -> None:
    invalid_tool = _tool_turn()
    assert isinstance(invalid_tool["tool_request"], dict)
    invalid_tool["tool_request"]["path"] = "../outside.py"
    llm = Llm(invalid_tool, _final_turn())
    outcome = ReviewAgentLoop(llm, max_findings=3).execute(
        _request(),
        coordinator=ContextToolCoordinator(tmp_path, PathPolicy()),
        session=_session(blocked=False),
        run_id=RUN_ID,
        bundle_id=BUNDLE_ID,
        source_revision=None,
    )
    assert outcome.tool_outcomes[0].status is ToolStatus.INVALID_REQUEST

    failing = ReviewAgentLoop(
        Llm(LlmClientError("offline", is_retryable=True, attempt=2)), max_findings=3
    )
    with pytest.raises(LlmClientError) as captured:
        failing.execute(
            _request(),
            coordinator=ContextToolCoordinator(tmp_path, PathPolicy()),
            session=_session(blocked=False),
            run_id=RUN_ID,
            bundle_id=BUNDLE_ID,
            source_revision=None,
        )
    assert captured.value.attempt == 2

    invalid = ReviewAgentLoop(Llm({}, {}), max_findings=3)
    with pytest.raises(InvalidLlmOutputError):
        invalid.execute(
            _request(),
            coordinator=ContextToolCoordinator(tmp_path, PathPolicy()),
            session=_session(blocked=False),
            run_id=RUN_ID,
            bundle_id=BUNDLE_ID,
            source_revision=None,
        )


@pytest.mark.parametrize(
    "turn",
    [
        AgentTurn("final", None, None),
        AgentTurn("tool-request", None, None),
    ],
)
def test_agent_loop_defensive_assertions_reject_impossible_turns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    turn: AgentTurn,
) -> None:
    monkeypatch.setattr(review_agent_module, "parse_agent_turn", lambda *_args, **_kwargs: turn)
    loop = ReviewAgentLoop(Llm(_final_turn()), max_findings=3)

    with pytest.raises(AssertionError, match="must return or raise"):
        loop.execute(
            _request(),
            coordinator=ContextToolCoordinator(tmp_path, PathPolicy()),
            session=_session(blocked=False),
            run_id=RUN_ID,
            bundle_id=BUNDLE_ID,
            source_revision=None,
        )


def test_agent_loop_defensive_assertion_covers_empty_repair_iterator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(review_agent_module, "range", lambda _count: (), raising=False)

    with pytest.raises(AssertionError, match="must return or raise"):
        ReviewAgentLoop(Llm(), max_findings=3).execute(
            _request(),
            coordinator=ContextToolCoordinator(tmp_path, PathPolicy()),
            session=_session(blocked=False),
            run_id=RUN_ID,
            bundle_id=BUNDLE_ID,
            source_revision=None,
        )


@pytest.mark.case("IT-M3-005", "UT-M3-010")
def test_reflector_updates_exact_ids_and_usage() -> None:
    finding = _finding()
    llm = Llm(_reflection(str(finding.finding_id), "supported", "EVIDENCE_SUFFICIENT"))
    reflector = FindingReflector(
        llm,
        load_prompt_template(PROMPTS, "reflection_v1"),
        max_input_tokens=1000,
        max_output_tokens=256,
        temperature=0,
    )

    outcome = reflector.reflect((finding,))

    assert outcome.findings[0].validation.reflection_status is ReflectionStatus.SUPPORTED
    assert outcome.request_hash
    assert outcome.call_count == 1
    assert reflector.prompt_version == "reflection_v1@2"
    assert reflector.prompt_hash


@pytest.mark.case("UT-M3-010")
def test_reflector_skips_ineligible_and_rejects_budget_or_bad_mapping() -> None:
    ineligible = _finding(reflection=ReflectionStatus.NEEDS_CONTEXT)
    reflector = FindingReflector(
        Llm(),
        load_prompt_template(PROMPTS, "reflection_v1"),
        max_input_tokens=1000,
        max_output_tokens=256,
        temperature=None,
    )
    assert reflector.reflect((ineligible,)).call_count == 0

    tiny = FindingReflector(
        Llm(),
        load_prompt_template(PROMPTS, "reflection_v1"),
        max_input_tokens=1,
        max_output_tokens=256,
        temperature=None,
    )
    with pytest.raises(
        ReflectionBudgetExceededError, match=re.escape(Messages.REFLECTION_BUDGET_EXCEEDED)
    ):
        tiny.reflect((_finding(),))

    finding = _finding()
    invalid = _reflection(str(finding.finding_id), "supported", "CONTEXT_REQUIRED")
    repairing = FindingReflector(
        Llm(invalid, invalid),
        load_prompt_template(PROMPTS, "reflection_v1"),
        max_input_tokens=1000,
        max_output_tokens=256,
        temperature=None,
    )
    with pytest.raises(InvalidLlmOutputError):
        repairing.reflect((finding,))


@pytest.mark.case("UT-M3-010")
def test_reflector_preserves_llm_failure_usage() -> None:
    reflector = FindingReflector(
        Llm(LlmClientError("offline", is_retryable=True, attempt=2, input_tokens=3)),
        load_prompt_template(PROMPTS, "reflection_v1"),
        max_input_tokens=1000,
        max_output_tokens=256,
        temperature=None,
    )

    with pytest.raises(LlmClientError) as captured:
        reflector.reflect((_finding(),))
    assert captured.value.attempt == 2
    assert captured.value.input_tokens == 3


@pytest.mark.case("UT-M3-010")
def test_reflector_repairs_adapter_level_invalid_json_once() -> None:
    """自己検証でも JSON 抽出失敗を一回だけ形式修復する."""
    finding = _finding()
    invalid = InvalidLlmOutputError(
        "json missing",
        input_tokens=3,
        output_tokens=4,
        latency_ms=5,
        cost_usd=Decimal("0.02"),
    )
    llm = Llm(
        invalid,
        _reflection(str(finding.finding_id), "supported", "EVIDENCE_SUFFICIENT"),
    )
    reflector = FindingReflector(
        llm,
        load_prompt_template(PROMPTS, "reflection_v1"),
        max_input_tokens=1000,
        max_output_tokens=256,
        temperature=None,
    )

    outcome = reflector.reflect((finding,))

    assert outcome.attempt == 2
    assert outcome.usage.input_tokens == 13
    assert outcome.usage.output_tokens == 6
    assert outcome.usage.latency_ms == 8
    assert outcome.usage.cost_usd == Decimal("0.03")
    assert "形式修復" in llm.requests[1].user_content


@pytest.mark.case("UT-M3-010")
def test_reflector_rejects_second_adapter_level_invalid_json() -> None:
    """自己検証の形式修復も二回目の JSON 抽出失敗で終了する."""
    reflector = FindingReflector(
        Llm(
            InvalidLlmOutputError("first", input_tokens=2, latency_ms=3),
            InvalidLlmOutputError("second", output_tokens=4, latency_ms=5),
        ),
        load_prompt_template(PROMPTS, "reflection_v1"),
        max_input_tokens=1000,
        max_output_tokens=256,
        temperature=None,
    )

    with pytest.raises(InvalidLlmOutputError) as captured:
        reflector.reflect((_finding(),))

    assert captured.value.attempt == 2
    assert captured.value.input_tokens == 2
    assert captured.value.output_tokens == 4
    assert captured.value.latency_ms == 8


def test_reflector_defensive_assertion_covers_empty_repair_iterator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reflection_module, "range", lambda _count: (), raising=False)
    reflector = FindingReflector(
        Llm(),
        load_prompt_template(PROMPTS, "reflection_v1"),
        max_input_tokens=1000,
        max_output_tokens=256,
        temperature=None,
    )

    with pytest.raises(AssertionError, match="must return or raise"):
        reflector.reflect((_finding(),))


@pytest.mark.case("UT-M3-010")
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"schema_version": 1, "results": {}},
        {"schema_version": 1, "results": ["bad"]},
        {
            "schema_version": 1,
            "results": [
                {"finding_id": "other", "status": "supported", "reason_code": "EVIDENCE_SUFFICIENT"}
            ],
        },
    ],
)
def test_reflection_parser_rejects_closed_schema_violations(payload: dict[str, object]) -> None:
    with pytest.raises(InvalidLlmOutputError):
        reflection_module._parse_reflection(payload, ("expected",))


@pytest.mark.case("IT-M3-005", "UT-M3-010")
def test_reflection_parser_rejects_duplicates_and_preserves_unmatched_finding() -> None:
    duplicate = {
        "schema_version": 1,
        "results": [
            {"finding_id": "same", "status": "supported", "reason_code": "EVIDENCE_SUFFICIENT"},
            {"finding_id": "same", "status": "supported", "reason_code": "EVIDENCE_SUFFICIENT"},
        ],
    }
    with pytest.raises(InvalidLlmOutputError):
        reflection_module._parse_reflection(duplicate, ("same", "other"))
    finding = _finding()
    assert reflection_module._with_reflection(finding, None) is finding


def _session(*, blocked: bool) -> ContextToolSession:
    return ContextToolSession(
        source=Source(),
        executor=Executor(),
        secret_scanner=Scanner(blocked),
        max_calls=1,
        token_budget=100,
    )


def _request() -> LlmRequest:
    return LlmRequest("system", "user", {}, 256, request_label="test")


def _tool_turn() -> dict[str, object]:
    return {
        "schema_version": 1,
        "action": "tool-request",
        "tool_request": {
            "tool_name": "read_file",
            "path": "src/app.py",
            "path_scope": [],
            "query": None,
            "start_line": 1,
            "end_line": 1,
        },
        "findings": [],
    }


def _final_turn() -> dict[str, object]:
    return {"schema_version": 1, "action": "final", "tool_request": None, "findings": []}


def _reflection(finding_id: str, status: str, reason: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "results": [{"finding_id": finding_id, "status": status, "reason_code": reason}],
    }


def _finding(*, reflection: ReflectionStatus = ReflectionStatus.REFLECTION_SKIPPED) -> Finding:
    candidate = CandidateFinding(
        file=NormalizedPath("src/app.py"),
        line_range=LineRange(1, 1),
        category="correctness",
        issue_type="null-dereference",
        rule_id="GENERAL.NULL_DEREFERENCE",
        primary_entity="user",
        severity=Severity.HIGH,
        title="Guard nullable user",
        description="The value can be None.",
        evidence=Evidence("user.name", "None reaches this line."),
        suggestion="Guard the value.",
        needs_context=reflection is ReflectionStatus.NEEDS_CONTEXT,
    )
    return Finding(
        FindingId(BUNDLE_ID, 1),
        BUNDLE_ID,
        candidate,
        "fingerprint",
        ValidationOutcome(
            ValidationStatus.VALID,
            ValidationStatus.VALID,
            reflection,
        ),
        "load_user",
    )
