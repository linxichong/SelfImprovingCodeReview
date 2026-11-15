"""同じ PR と反復条件で三つのツールを比較する."""

import shutil
import subprocess  # nosec B404
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypedDict

from pydantic import ValidationError

from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.infrastructure.benchmark.execution import (
    BenchTools,
    Workspace,
    digest,
    git,
    review_settings,
)
from self_improving_code_review.infrastructure.benchmark.models import (
    TOOLS,
    Sample,
    Status,
    Tool,
    Trial,
)


class TrialRow(TypedDict):
    """管理台と CSV へ共通で渡す試行の指標."""

    tool: Tool
    instance: str
    repeat: int
    status: Status
    reference_n: int
    generated_n: int | None
    hit_n: int | None
    matched_generated_n: int | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    cost_usd: str | None


class Entry(TypedDict):
    """試行の指標と一致した参考指摘の位置."""

    summary: TrialRow
    matched_reference_indices: tuple[int, ...] | None


class RunReport(TypedDict):
    """三つのツールの試行、採点集計および入力の照合情報."""

    effective_revisions: dict[str, dict[str, str]]
    preparation_failures: list[str]
    by_tool: dict[Tool, dict[str, object]]
    paired_by_tool: dict[Tool, dict[str, object]]
    rows: list[Entry]


def plan(tools: BenchTools, samples: tuple[Sample, ...]) -> dict[str, object]:
    """使用する入力を、参考指摘本文や秘密を含めず固定する."""
    rules = tools.project / "config/rules"
    inputs = {
        path.relative_to(rules).as_posix(): digest(path)
        for path in sorted(rules.rglob("*"))
        if path.is_file()
    }
    ocr_profile = Path.home() / ".opencodereview"
    return {
        "manifest": tools.manifest.model_dump(mode="json"),
        "project_commit": git(tools.project, "rev-parse", "HEAD"),
        "project_has_local_changes": bool(git(tools.project, "status", "--porcelain")),
        "evaluator_requirements_sha256": digest(tools.upstream / "evaluation/requirements.txt"),
        "prompt_sha256": digest(tools.project / "prompts/review_general_v1.yaml"),
        "rules_sha256": inputs,
        "sicr_settings": review_settings(tools.manifest.model, tools.manifest.gateway, 1),
        "ocr_profile_sha256": {
            name: digest(ocr_profile / name)
            for name in ("config.json", "rule.json")
            if (ocr_profile / name).is_file()
        },
        "samples": [
            {
                "instance_id": item.instance_id,
                "pr": item.pr_url,
                "base": item.source_commit,
                "head": item.target_commit,
                "reference_count": len(item.comments),
            }
            for item in samples
        ],
    }


def run(tools: BenchTools, samples: tuple[Sample, ...]) -> RunReport:
    """各 PR を固定した作業コピーで審査し、成功した試行を採点する."""
    rows: list[Entry] = []
    revisions: dict[str, dict[str, str]] = {}
    preparation_failures: list[str] = []
    with TemporaryDirectory(prefix="sicr-aacr-") as root:
        assets = Path(root) / "assets"
        shutil.copytree(tools.project / "prompts", assets / "prompts")
        shutil.copytree(tools.project / "config/rules", assets / "config/rules")
        for index, sample in enumerate(samples):
            folder = Path(root) / str(index)
            folder.mkdir()
            try:
                work = Workspace.prepare(folder, sample, assets)
            except InvalidInputError, OSError, subprocess.TimeoutExpired:
                work = None
                preparation_failures.append(sample.instance_id)
            if work is not None:
                revisions[sample.instance_id] = {
                    "effective_base": work.base,
                    "diff_sha256": work.diff_sha256,
                }
            for repeat in range(1, tools.manifest.repeats + 1):
                rows.extend(_trial(tools, sample, work, repeat, tool) for tool in TOOLS)
    return {
        "effective_revisions": revisions,
        "preparation_failures": preparation_failures,
        "by_tool": summarize([entry["summary"] for entry in rows]),
        "paired_by_tool": summarize(_paired(rows)),
        "rows": rows,
    }


def _trial(
    tools: BenchTools, sample: Sample, work: Workspace | None, repeat: int, tool: Tool
) -> Entry:
    """一つの試行を保存し、環境の問題と正常な指摘ゼロを区別する."""
    trial = Trial(status="missing")
    if work is not None:
        try:
            trial = tools.review(tool, work, sample.instance_id, repeat)
        except OSError:
            trial = Trial(status="failed")
    status = trial.status
    score = None
    if status == "complete":
        try:
            score = tools.score(sample, trial)
        except InvalidInputError, ValidationError, OSError, subprocess.TimeoutExpired:
            status = "judge-failed"
    usage = trial.usage
    return {
        "summary": {
            "tool": tool,
            "instance": sample.instance_id,
            "repeat": repeat,
            "status": status,
            "reference_n": len(sample.comments),
            "generated_n": len(trial.findings) if trial.status == "complete" else None,
            "hit_n": score.hit_n if score else None,
            "matched_generated_n": score.matched_generated_n if score else None,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "latency_ms": usage.latency_ms,
            "cost_usd": usage.cost_usd,
        },
        "matched_reference_indices": score.matched_reference_indices if score else None,
    }


def _paired(entries: list[Entry]) -> list[TrialRow]:
    """全ツールが採点まで完了したサンプルと反復だけを比較する."""
    rows = [entry["summary"] for entry in entries if entry["summary"]["status"] == "complete"]
    counts = Counter((row["instance"], row["repeat"]) for row in rows)
    return [row for row in rows if counts[(row["instance"], row["repeat"])] == len(TOOLS)]


def summarize(rows: list[TrialRow]) -> dict[Tool, dict[str, object]]:
    """未完了件数と評価済み指標、レビュー使用量を別々に集計する."""
    return {tool: _tool_summary([row for row in rows if row["tool"] == tool]) for tool in TOOLS}


def _tool_summary(rows: list[TrialRow]) -> dict[str, object]:
    """同一ツールの precision、recall、F1 と測定件数を得る."""
    scored = [row for row in rows if row["status"] == "complete"]
    statuses = Counter(row["status"] for row in rows)
    refs = sum(row["reference_n"] for row in scored)
    hits = sum(row["hit_n"] or 0 for row in scored)
    generated = sum(row["generated_n"] or 0 for row in scored)
    matched = sum(row["matched_generated_n"] or 0 for row in scored)
    precision = matched / generated if generated else 0.0
    recall = hits / refs if refs else 0.0
    measured = [
        row
        for row in scored
        if row["input_tokens"] is not None and row["output_tokens"] is not None
    ]
    return {
        "attempts": len(rows),
        "statuses": dict(statuses),
        "scored": len(scored),
        "reference_n": refs,
        "hit_n": hits,
        "generated_n": generated,
        "matched_generated_n": matched,
        "precision": precision if scored else None,
        "recall": recall if scored else None,
        "f1": 2 * precision * recall / (precision + recall)
        if precision + recall
        else (0.0 if scored else None),
        "token_measured": len(measured),
        "input_tokens": sum(row["input_tokens"] or 0 for row in measured),
        "output_tokens": sum(row["output_tokens"] or 0 for row in measured),
    }
