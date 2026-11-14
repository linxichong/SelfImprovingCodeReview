"""品質門禁の失敗状態と引数境界を検証する。"""

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

SPEC = importlib.util.spec_from_file_location(
    "quality_runner", Path(__file__).resolve().parents[2] / "scripts/check_quality.py"
)
assert SPEC is not None
assert SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


@pytest.mark.parametrize("exit_code", [0, 1, 127])
def test_command_preserves_exit_code(exit_code: int) -> None:
    with patch.object(runner.subprocess, "run") as execute:
        execute.return_value.returncode = exit_code
        assert runner.run_command(("uv", "lock", "--check"))[0] == exit_code
        assert execute.call_args.kwargs["check"] is False


def test_missing_executable_fails() -> None:
    with patch.object(runner.subprocess, "run", side_effect=FileNotFoundError):
        assert runner.run_command(("missing",))[0] == 127


def test_missing_coverage_baseline_is_not_success(tmp_path: Path) -> None:
    with (
        patch.object(runner, "ROOT", tmp_path),
        patch.object(runner, "git_value", return_value="fixed"),
        patch("sys.argv", ["check_quality.py", "--check", "coverage"]),
    ):
        assert runner.main() == 1
        assert "BASE_SHA_REQUIRED" in (tmp_path / ".review/quality/coverage.json").read_text()


@pytest.mark.parametrize("line_count", [0, 3])
def test_coverage_generates_and_reads_report(tmp_path: Path, line_count: int) -> None:
    report = tmp_path / ".review/quality/diff-coverage.json"

    def execute(command: tuple[str, ...]) -> tuple[int, float]:
        """差分検査の出力指定に応じて報告を生成する。"""
        if command[3] == "diff-cover":
            assert "--format=json:.review/quality/diff-coverage.json" in command
            report.write_text(json.dumps({"total_num_lines": line_count}))
        return 0, 0.0

    with (
        patch.object(runner, "ROOT", tmp_path),
        patch.object(runner, "git_value", return_value="fixed"),
        patch.object(runner, "run_command", side_effect=execute),
        patch("sys.argv", ["check_quality.py", "--check", "coverage", "--base-sha", "a" * 40]),
    ):
        assert runner.main() == 0
    result = json.loads((tmp_path / ".review/quality/coverage.json").read_text())
    assert result["checks"][-1]["executable_lines"] == line_count
    assert result["checks"][-1]["status"] == ("not_applicable" if line_count == 0 else "passed")
