"""公開評価 CLI と、結果の境界および同組比較を検証する."""

import hashlib
import json
import secrets
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from self_improving_code_review.cli.main import main
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.infrastructure.benchmark import execution
from self_improving_code_review.infrastructure.benchmark.conversion import convert
from self_improving_code_review.infrastructure.benchmark.execution import BenchTools, Workspace
from self_improving_code_review.infrastructure.benchmark.models import (
    Manifest,
    Sample,
    Score,
    Tool,
    Trial,
)


@pytest.fixture
def benchmark(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    project = tmp_path / "project"
    upstream = tmp_path / "aacr"
    samples = upstream / "dataset/v1"
    samples.mkdir(parents=True)
    project.mkdir()
    (project / "prompts").mkdir()
    (project / "prompts/review_general_v1.yaml").write_text("fixture")
    (project / "config/rules").mkdir(parents=True)
    (project / "config/rules/rule.yaml").write_text("fixture")
    record = {
        "githubPrUrl": "https://github.com/test/sample/pull/1",
        "source_commit": "a" * 40,
        "target_commit": "b" * 40,
        "comments": [{"path": "file.py", "note": "known bug", "from_line": 3}],
    }
    data = json.dumps([record]).encode()
    (samples / "positive_samples.json").write_bytes(data)
    (upstream / "evaluation").mkdir()
    (upstream / "evaluation/requirements.txt").write_text("fixture==1\n")
    refs = []
    for path in (upstream, project):
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        subprocess.run(["git", "add", "."], cwd=path, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=test",
                "-c",
                "user.email=test@example.test",
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "-qm",
                "fixture",
            ],
            cwd=path,
            check=True,
        )
        refs.append(
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()
        )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "upstream_commit": refs[0],
                "dataset_sha256": hashlib.sha256(data).hexdigest(),
                "instances": ["test__sample@bbbbbbb"],
                "repeats": 1,
                "model": "exact-model",
                "judge_model": "judge-model",
                "gateway": "https://example.test/v1",
                "ocr_sha256": "0" * 64,
                "codex_version": "1.0",
            }
        )
    )
    monkeypatch.chdir(project)
    return ["benchmark", "--manifest", str(manifest), "--upstream", str(upstream)]


def test_benchmark_validates_pinned_inputs_without_a_key(
    benchmark: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("REVIEW_LLM_API_KEY", raising=False)
    assert main(benchmark) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["samples"][0]["instance_id"] == "test__sample@bbbbbbb"
    assert result["samples"][0]["reference_count"] == 1
    assert "known bug" not in json.dumps(result)
    manifest = Path(benchmark[2])
    changed = json.loads(manifest.read_text())
    changed["dataset_sha256"] = "0" * 64
    manifest.write_text(json.dumps(changed))
    assert main(benchmark) == 1


def test_benchmark_preserves_zero_findings_and_partial_attempts(
    benchmark: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def prepare(folder: Path, sample: Sample, project: Path) -> Workspace:
        return Workspace("a" * 40, "b" * 40, "0" * 64, folder, folder)

    def verify(tools: BenchTools) -> None:
        return None

    def review(tools: BenchTools, tool: Tool, work: Workspace, instance: str, repeat: int) -> Trial:
        return Trial(status="timeout" if tool == "ocr" else "complete")

    def score(tools: BenchTools, sample: Sample, trial: Trial) -> Score:
        return Score(hit_n=0, matched_generated_n=0, matched_reference_indices=())

    monkeypatch.setattr(Workspace, "prepare", prepare)
    monkeypatch.setattr(BenchTools, "verify", verify)
    monkeypatch.setattr(BenchTools, "review", review)
    monkeypatch.setattr(BenchTools, "score", score)
    output = tmp_path / "report.json"
    args = [
        *benchmark,
        "--execute",
        "--ocr",
        "fixture-ocr",
        "--evaluator-python",
        "python",
        "--output",
        str(output),
    ]
    assert main(args) == 2
    result = json.loads(output.read_text())
    assert result["by_tool"]["sicr"]["reference_n"] == 1
    assert result["by_tool"]["sicr"]["recall"] == 0.0
    assert result["rows"][0]["summary"]["generated_n"] == 0
    assert result["rows"][1]["summary"]["status"] == "timeout"
    assert result["rows"][1]["summary"]["generated_n"] is None
    assert result["paired_by_tool"]["codex"]["scored"] == 0
    assert "timeout" in output.with_suffix(".csv").read_text()
    assert main(args) == 1


@pytest.mark.parametrize("path", ["../file.py", "/file.py", "src//file.py"])
def test_conversion_rejects_invalid_locations(path: str) -> None:
    payload = json.dumps(
        {
            "run": {"status": "success"},
            "findings": [{"file": path, "start_line": 3, "end_line": 5, "title": "bug"}],
            "metrics": {"input_tokens": 20, "output_tokens": 3},
        }
    )
    with pytest.raises(ValidationError):
        convert("sicr", payload, 0, 15)


def test_sicr_conversion_preserves_line_range_title_and_usage() -> None:
    data = {
        "run": {"status": "success"},
        "findings": [
            {
                "file": "src/file.py",
                "start_line": 3,
                "end_line": 5,
                "title": "bug",
                "description": "detail",
            }
        ],
        "metrics": {"input_tokens": 20, "output_tokens": 3, "cost_usd": "0.002"},
    }
    result = convert("sicr", json.dumps(data), 0, 15)
    assert result.findings[0].judge_row() == {
        "path": "src/file.py",
        "note": "bug\ndetail",
        "from_line": 3,
        "to_line": 5,
        "side": "right",
    }
    assert result.usage.model_dump() == {
        "input_tokens": 20,
        "output_tokens": 3,
        "latency_ms": 15,
        "cost_usd": "0.002",
    }


def test_benchmark_adapters_pass_pins_and_keep_credentials_out_of_reports(
    benchmark: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = Manifest.model_validate_json(Path(benchmark[2]).read_bytes())
    ocr = tmp_path / "ocr"
    ocr.write_text("binary fixture")
    manifest = manifest.model_copy(update={"ocr_sha256": execution.digest(ocr)})
    tools = BenchTools(
        manifest, Path.cwd(), Path(benchmark[4]), ocr, tmp_path / "codex", Path("/python")
    )
    work = Workspace("a" * 40, "b" * 40, "0" * 64, tmp_path / "sicr", tmp_path / "external")
    sample = execution.load_samples(tools.upstream, manifest)[0]
    key = secrets.token_urlsafe()
    monkeypatch.setenv("REVIEW_LLM_API_KEY", key)
    monkeypatch.setenv("JUDGE_USE_MOCK", "true")
    case_file = {"file": "file.py", "start_line": 3, "end_line": 3}
    usage = {"input_tokens": 70, "output_tokens": 12}

    def command(
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        body: str | None = None,
        timeout: int = 240,
    ) -> subprocess.CompletedProcess[str]:
        def completed(stdout: str = "", code: int = 0) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args, code, stdout, "")

        if args[-1] == "--version":
            return completed("codex-cli 1.0")
        assert env is not None
        if args[1] == "-c":
            assert body == "fixture==1\n"
            return completed()
        if args[3] == "score":
            assert env["JUDGE_USE_MOCK"] == "false"
            assert env["JUDGE_API_KEY"] == key
            assert body is not None
            assert json.loads(body)["findings"][0]["path"] == "file.py"
            return completed(
                json.dumps({"hit_n": 1, "matched_generated_n": 1, "matched_reference_indices": [1]})
            )
        if args[3] == "codex":
            assert env["CODEX_MODEL"] == "exact-model"
            assert env["CODEX_API_KEY"] == key
            return completed(
                json.dumps(
                    {
                        "status": "complete",
                        "usage": usage,
                        "findings": [{**case_file, "summary": "a bug"}],
                    }
                )
            )
        output = Path(args[args.index("--output") + 1])
        if args[0] == str(ocr):
            assert env["OCR_LLM_TOKEN"] == key
            assert env["OCR_LLM_URL"] == "https://example.test/v1/chat/completions"
            output.write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "summary": usage,
                        "comments": [
                            {"path": "file.py", "start_line": 3, "end_line": 3, "content": "a bug"}
                        ],
                    }
                )
            )
        else:
            assert env["REVIEW_LLM_API_KEY"] == key
            config = Path(args[args.index("--config") + 1])
            assert json.loads(config.read_text())["llm"]["model"] == "exact-model"
            output.write_text(
                json.dumps(
                    {
                        "run": {"status": "success"},
                        "metrics": usage,
                        "findings": [{**case_file, "title": "a bug"}],
                    }
                )
            )
        return completed()

    monkeypatch.setattr(execution, "command", command)
    tools.verify()
    for tool in ("sicr", "ocr", "codex"):
        review = tools.review(tool, work, sample.instance_id, 1)
        assert review.status == "complete"
        assert review.usage.input_tokens == 70
        assert tools.score(sample, review).matched_reference_indices == (1,)
        assert key not in review.model_dump_json()
    assert not (tmp_path / "review-result.json").exists()
    ocr.write_text("changed binary")
    with pytest.raises(InvalidInputError):
        tools.verify()
