"""M3 限定コンテキストツールの信頼境界と予算を検証する。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from self_improving_code_review.application.review.context_tools import (
    ContextFragment,
    ContextToolCoordinator,
    ContextToolSession,
    ToolIntent,
    ToolResult,
    ValidatedToolRequest,
)
from self_improving_code_review.domain.common.enums import ToolStatus
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.common.values import BundleId, NormalizedPath, RunId
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


@dataclass
class Source:
    """固定テキストを返すテスト用入力元。"""

    text: str = "line one\nline two"
    source_revision: str | None = None

    def read_text(self, _path: NormalizedPath) -> str | None:
        return self.text

    def list_paths(
        self, _scope: tuple[NormalizedPath, ...], *, max_files: int
    ) -> tuple[NormalizedPath, ...]:
        return (NormalizedPath("src/app.py"),)[:max_files]


class Executor:
    """設定済み結果または例外を返すテスト用実行器。"""

    def __init__(self, result: ToolResult | BaseException) -> None:
        self.result = result

    def execute(self, _request: object, _source: object) -> ToolResult:
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class Scanner:
    """安全または遮断を決定的に返す検査器。"""

    engine_version = "test"

    def __init__(self, *, blocked: bool = False) -> None:
        self.blocked = blocked

    def scan_and_seal(self, payload: SourcePayload) -> tuple[ScanResult, ScannedPayload | None]:
        findings = (SecretFinding("test", payload.label, 1),) if self.blocked else ()
        result = ScanResult(hash_text(payload.text), "test", ScanProfile.STRICT, findings)
        sealed = None
        if not findings:
            sealed = ScannedPayload("scan", "test", hash_text(payload.text), payload.text)
        return result, sealed


@pytest.mark.case("UT-M3-005")
def test_coordinator_normalizes_and_hashes_valid_request(tmp_path: Path) -> None:
    request = ContextToolCoordinator(tmp_path, PathPolicy()).validate(
        ToolIntent("read_file", path="src/app.py", start_line=1, end_line=20),
        run_id=RUN_ID,
        bundle_id=BUNDLE_ID,
        source_revision=None,
    )

    assert request.path == NormalizedPath("src/app.py")
    assert request.repository_root == tmp_path.resolve()
    assert request.request_hash

    search = ContextToolCoordinator(tmp_path, PathPolicy()).validate(
        ToolIntent("search_code", query="target"),
        run_id=RUN_ID,
        bundle_id=BUNDLE_ID,
        source_revision=None,
    )
    assert search.query == "target"


@pytest.mark.case("UT-M3-005", "UT-M3-006")
@pytest.mark.parametrize(
    "intent",
    [
        ToolIntent("unknown"),
        ToolIntent("read_file", path="../secret", start_line=1, end_line=2),
        ToolIntent("read_file", path=".git/config", start_line=1, end_line=2),
        ToolIntent("read_file", path="src/*.py", start_line=1, end_line=2),
        ToolIntent("read_file", path="src/a.py", start_line=0, end_line=2),
        ToolIntent("read_file", path="src/a.py", start_line=2, end_line=1),
        ToolIntent("read_file", path="src/a.py", start_line=1, end_line=201),
        ToolIntent("read_file"),
        ToolIntent("find_symbol"),
        ToolIntent("get_changed_file", path="src/a.py", start_line=1),
        ToolIntent("search_code", query=""),
        ToolIntent("search_code", query="x", start_line=1),
        ToolIntent("find_symbol", path_scope=tuple(f"src/{i}" for i in range(11)), query="x"),
    ],
)
def test_coordinator_rejects_invalid_intent(tmp_path: Path, intent: ToolIntent) -> None:
    with pytest.raises(InvalidInputError):
        ContextToolCoordinator(tmp_path, PathPolicy()).validate(
            intent,
            run_id=RUN_ID,
            bundle_id=BUNDLE_ID,
            source_revision=None,
        )


@pytest.mark.case("UT-M3-006")
def test_coordinator_rejects_symlink_that_escapes_repository(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.py"
    outside.write_text("value = 1", encoding="utf-8")
    (tmp_path / "linked.py").symlink_to(outside)

    with pytest.raises(InvalidInputError):
        ContextToolCoordinator(tmp_path, PathPolicy()).validate(
            ToolIntent("read_file", path="linked.py", start_line=1, end_line=1),
            run_id=RUN_ID,
            bundle_id=BUNDLE_ID,
            source_revision=None,
        )


@pytest.mark.case("IT-M3-003", "UT-M3-007")
def test_session_enforces_repeat_call_and_token_budgets(tmp_path: Path) -> None:
    request = _request(tmp_path)
    fragment = ContextFragment(NormalizedPath("src/app.py"), 1, 1, "safe text")
    session = ContextToolSession(
        source=Source(),
        executor=Executor(ToolResult(ToolStatus.SUCCESS, (fragment,))),
        secret_scanner=Scanner(),
        max_calls=2,
        token_budget=100,
    )

    first = session.execute(request)
    repeated = session.execute(request)
    exhausted = session.execute(request)

    assert first.status is ToolStatus.SUCCESS
    assert first.prompt_block is not None
    assert session.success_count == 1
    assert repeated.status is ToolStatus.REPEATED_REQUEST
    assert exhausted.status is ToolStatus.BUDGET_EXCEEDED
    assert session.call_count == 2
    assert session.consumed_tokens > 0


@pytest.mark.case("IT-M3-003", "UT-M3-007")
def test_session_reports_invalid_failure_result_budget_and_secret(tmp_path: Path) -> None:
    request = _request(tmp_path)
    invalid_session = ContextToolSession(
        source=Source(),
        executor=Executor(ToolResult(ToolStatus.NOT_FOUND)),
        secret_scanner=Scanner(),
        max_calls=1,
        token_budget=10,
    )
    invalid = invalid_session.reject_invalid(ToolIntent("bad"))
    invalid_exhausted = invalid_session.reject_invalid(ToolIntent("bad"))
    failed = ContextToolSession(
        source=Source(),
        executor=Executor(InvalidInputError("changed")),
        secret_scanner=Scanner(),
        max_calls=1,
        token_budget=10,
    ).execute(request)
    not_found = ContextToolSession(
        source=Source(),
        executor=Executor(ToolResult(ToolStatus.NOT_FOUND)),
        secret_scanner=Scanner(),
        max_calls=1,
        token_budget=10,
    ).execute(request)
    large = ContextFragment(NormalizedPath("src/app.py"), 1, 1, "x" * 1000)
    over_budget = ContextToolSession(
        source=Source(),
        executor=Executor(ToolResult(ToolStatus.SUCCESS, (large,))),
        secret_scanner=Scanner(),
        max_calls=1,
        token_budget=1,
    ).execute(request)
    secret = ContextToolSession(
        source=Source(),
        executor=Executor(ToolResult(ToolStatus.SUCCESS, (large,))),
        secret_scanner=Scanner(blocked=True),
        max_calls=1,
        token_budget=1000,
    ).execute(request)

    assert invalid.status is ToolStatus.INVALID_REQUEST
    assert invalid_exhausted.status is ToolStatus.BUDGET_EXCEEDED
    assert failed.reason_code == "TOOL_FAILED"
    assert not_found.status is ToolStatus.NOT_FOUND
    assert over_budget.reason_code == "TOKEN_BUDGET_EXCEEDED"
    assert secret.is_blocked
    assert secret.secret_findings


@pytest.mark.case("UT-M3-013")
@pytest.mark.parametrize(
    ("timer_values", "expected_ms"),
    [((10.0, 10.123), 123), ((10.0, 9.0), 0)],
)
def test_session_measures_tool_latency_separately(
    tmp_path: Path,
    timer_values: tuple[float, float],
    expected_ms: int,
) -> None:
    values = iter(timer_values)
    fragment = ContextFragment(NormalizedPath("src/app.py"), 1, 1, "safe text")
    session = ContextToolSession(
        source=Source(),
        executor=Executor(ToolResult(ToolStatus.SUCCESS, (fragment,))),
        secret_scanner=Scanner(),
        max_calls=1,
        token_budget=100,
        timer=lambda: next(values),
    )

    outcome = session.execute(_request(tmp_path))

    assert outcome.latency_ms == expected_ms


def _request(repository: Path) -> ValidatedToolRequest:
    return ValidatedToolRequest(
        run_id=RUN_ID,
        bundle_id=BUNDLE_ID,
        repository_root=repository,
        source_revision=None,
        tool_name="read_file",
        path=NormalizedPath("src/app.py"),
        path_scope=(),
        query=None,
        start_line=1,
        end_line=2,
        request_hash="request-hash",
    )
