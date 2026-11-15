"""AACR-Bench の計画確認と明示的実行の CLI 境界."""

import argparse
import csv
import json
import shutil
from pathlib import Path

from pydantic import ValidationError

from self_improving_code_review.cli.output import write_document
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.infrastructure.benchmark.execution import (
    BenchTools,
    load_samples,
)
from self_improving_code_review.infrastructure.benchmark.models import Manifest
from self_improving_code_review.infrastructure.benchmark.report import Entry, plan, run
from self_improving_code_review.messages import Messages


def add_benchmark_options(parser: argparse.ArgumentParser) -> None:
    """公開ベンチマークの入力と実行オプションを追加する."""
    parser.add_argument("-h", "--help", action="help", help=Messages.CLI_HELP_HELP)
    parser.add_argument("--manifest", type=Path, required=True, help=Messages.BENCH_MANIFEST)
    parser.add_argument("--upstream", type=Path, required=True, help=Messages.BENCH_CHECKOUT)
    parser.add_argument("--ocr", type=Path, help=Messages.BENCH_OCR)
    parser.add_argument("--codex", type=Path, help=Messages.BENCH_CODEX)
    parser.add_argument("--evaluator-python", type=Path, help=Messages.BENCH_PYTHON)
    parser.add_argument("--output", type=Path, help=Messages.BENCH_OUTPUT)
    parser.add_argument("--execute", action="store_true", help=Messages.BENCH_EXECUTE)


def execute_benchmark(arguments: argparse.Namespace) -> int:
    """既定では計画のみ表示し、明示された場合に公式評価を実行する."""
    try:
        manifest = Manifest.model_validate_json(arguments.manifest.read_bytes())
        upstream: Path = arguments.upstream.resolve()
        samples = load_samples(upstream, manifest)
    except (OSError, ValidationError) as exc:
        raise InvalidInputError(Messages.BENCH_INVALID) from exc
    codex = arguments.codex or Path(shutil.which("codex") or "codex")
    tools = BenchTools(
        manifest,
        Path.cwd(),
        upstream,
        (arguments.ocr or Path("ocr")).absolute(),
        codex.absolute(),
        (arguments.evaluator_python or Path("python3")).absolute(),
    )
    document = plan(tools, samples)
    if not arguments.execute:
        write_document(document, None)
        return 0
    output = _validate_execute(arguments, tools)
    result = run(tools, samples)
    document.update(result)
    _write_reports(document, result["rows"], output)
    return (
        0
        if all(
            item["scored"] == len(samples) * manifest.repeats for item in result["by_tool"].values()
        )
        else 2
    )


def _validate_execute(arguments: argparse.Namespace, tools: BenchTools) -> Path:
    """実行前に必要なバイナリ、キー、および未使用の出力ファイルを検査する."""
    if not arguments.ocr or not arguments.evaluator_python or not arguments.output:
        raise InvalidInputError(Messages.BENCH_EXECUTE_ARGS)
    output: Path = arguments.output.absolute()
    if output.suffix != ".json" or any(
        path.exists() or path.is_symlink() for path in (output, output.with_suffix(".csv"))
    ):
        raise InvalidInputError(Messages.BENCH_OUTPUT_INVALID)
    tools.verify()
    return output


def _write_reports(document: dict[str, object], rows: list[Entry], output: Path) -> None:
    """既存レポートを上書きせず JSON と CSV を保存する."""
    output.parent.mkdir(parents=True, exist_ok=True)
    summaries = [entry["summary"] for entry in rows]
    with (
        output.open("x", encoding="utf-8") as target,
        output.with_suffix(".csv").open("x", encoding="utf-8", newline="") as csv_file,
    ):
        json.dump(document, target, ensure_ascii=False, indent=2)
        target.write("\n")
        writer = csv.DictWriter(csv_file, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
