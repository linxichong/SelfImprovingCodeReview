"""固定上流プロセスとの境界および Git 準備を検証する."""

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from self_improving_code_review.infrastructure.benchmark import bridge, execution
from self_improving_code_review.infrastructure.benchmark.execution import Workspace
from self_improving_code_review.infrastructure.benchmark.models import Sample


@pytest.mark.parametrize(
    ("reason", "success"), [("same issue\n[1]", True), ("truncated", False), ("", False)]
)
def test_official_judge_bridge_preserves_matches_and_rejects_unparsed_reason(
    reason: str, success: bool, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def evaluate(refs: list[dict[str, object]], findings: list[object]) -> tuple:
        assert refs[0]["path"] == "file.py"
        assert findings == [{"path": "file.py", "note": "same issue"}]
        refs[0]["file_semantic_match"] = True
        return (1, 1, [{"judge_reason": reason, "path": "file.py"}])

    payload = {
        "references": [{"path": "file.py"}],
        "findings": [{"path": "file.py", "note": "same issue"}],
    }
    vendor = SimpleNamespace(
        evaluate_file_level_semantics=evaluate, _normalize_path=lambda path: path
    )
    monkeypatch.setattr(bridge, "importlib", SimpleNamespace(import_module=lambda _name: vendor))
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", ["bridge", ".", "score"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert bridge.main() == 0
    got = json.loads(capsys.readouterr().out)
    assert got == (
        {"hit_n": 1, "matched_generated_n": 1, "matched_reference_indices": [1]} if success else {}
    )


@pytest.mark.parametrize(
    ("content", "exit_code", "expected"),
    [
        ("No new blocking or actionable defects were identified.", 0, "complete"),
        ("", 0, "invalid"),
        ("", 124, "timeout"),
    ],
)
def test_codex_bridge_distinguishes_empty_text_explicit_no_findings_and_timeout(
    content: str, exit_code: int, expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    removed = []
    assert tmp_path.is_dir()
    adapter = SimpleNamespace(
        write_codex_config=lambda _env: tmp_path,
        cleanup_codex_home=lambda path: removed.append(path),
        run_codex_review=lambda *_args: SimpleNamespace(returncode=exit_code, stdout="events"),
        extract_from_jsonl=lambda _raw: (content, {"thread_id": "one", "token_usage": {}}),
        _usage_is_zero=lambda _usage: True,
        _extract_usage_from_session=lambda _home, _thread: {"input_tokens": 14, "output_tokens": 2},
        load_streamed_findings=lambda _output, _instance: None,
        extract_findings_from_result=lambda text: text,
    )
    monkeypatch.setattr(bridge, "importlib", SimpleNamespace(import_module=lambda _name: adapter))
    monkeypatch.setattr(sys, "argv", ["bridge", ".", "codex", str(tmp_path), "a", "b", "id"])
    for key in ("CODEX_API_KEY", "CODEX_MODEL", "CODEX_GATEWAY_URL"):
        monkeypatch.setenv(key, "fixture")
    data = bridge.review_codex()
    assert data == {
        "status": expected,
        "findings": [],
        "usage": {"input_tokens": 14, "output_tokens": 2},
    }
    assert removed == [tmp_path]


def test_workspace_pins_public_diff_and_keeps_sicr_assets_out_of_other_reviewers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    commit = ["-c", "user.name=fixture", "-c", "user.email=fixture@example.test", "commit", "-qm"]
    execution.git(source, "init", "--quiet")
    target = source / "file.py"
    target.write_text("safe = True\n")
    execution.git(source, "add", "file.py")
    execution.git(source, *commit, "base")
    base = execution.git(source, "rev-parse", "HEAD")
    target.write_text("safe = False\n")
    execution.git(source, "add", "file.py")
    execution.git(source, *commit, "target")
    head = execution.git(source, "rev-parse", "HEAD")
    assets = tmp_path / "assets"
    (assets / "prompts").mkdir(parents=True)
    (assets / "prompts/review_general_v1.yaml").write_text("trusted fixture")
    (assets / "config/rules").mkdir(parents=True)
    (assets / "config/rules/one.yaml").write_text("trusted fixture")
    case = Sample.model_validate(
        {
            "githubPrUrl": "https://github.com/test/sample/pull/1",
            "source_commit": base,
            "target_commit": head,
            "comments": [],
        }
    )
    original = execution.git

    def local_git(repo: Path, *args: str) -> str:
        if args[:3] == ("remote", "add", "origin"):
            return original(repo, *args[:3], str(source))
        return original(repo, *args)

    monkeypatch.setattr(execution, "git", local_git)
    folder = tmp_path / "case"
    folder.mkdir()
    result = Workspace.prepare(folder, case, assets)
    assert result.base == base
    assert result.head == head
    assert (result.sicr / "prompts/review_general_v1.yaml").read_text() == "trusted fixture"
    assert (result.sicr / ".review/benchmark-rules/one.yaml").is_file()
    assert not (result.external / "prompts").exists()
    assert original(result.external, "status", "--porcelain") == ""
    assert "+safe = False" in original(result.external, "diff", f"{base}...HEAD")
