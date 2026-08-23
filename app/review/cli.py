from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from app.llm.client import LLMError, OpenAIClient
from app.review.context_builder import build_context, read_diff_from_file, read_git_diff, read_rules
from app.review.reviewer import BaselineReviewer, HeuristicMockReviewerClient, InvalidReviewOutput
from app.review.utils import annotate_diff_with_line_numbers, compute_fingerprint, validate_line_in_hunk

SUCCESS = 0
INVALID_INPUT = 1
LLM_CALL_FAILED = 2
INVALID_LLM_OUTPUT = 3
INTERNAL_ERROR = 4


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a baseline AI code review over a Git diff.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--diff-file", type=Path, help="Path to a unified diff file.")
    source.add_argument("--git-diff", action="store_true", help="Read staged diff, or unstaged diff if staged diff is empty.")
    parser.add_argument("--rules", type=Path, default=Path("rules/coding_rules.md"), help="Project coding rules file.")
    parser.add_argument("--output", type=Path, help="Write review JSON to this path. Defaults to stdout.")
    parser.add_argument("--mock", action="store_true", help="Use deterministic local mock reviewer instead of the OpenAI API.")
    parser.add_argument("--model", help="OpenAI model name. Defaults to OPENAI_MODEL or gpt-4o-2024-11-20.")
    parser.add_argument("--temperature", type=float, default=0, help="LLM sampling temperature. Default: 0.")
    parser.add_argument("--prompt-version", default="p1", help="Prompt template version. Default: p1.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    start_time = time.perf_counter()

    try:
        args = parse_args(argv)
        diff = read_git_diff() if args.git_diff else read_diff_from_file(args.diff_file)
        rules = read_rules(args.rules) if args.rules else ""

        # §18.1: Pre-annotate diff with absolute line numbers before sending to LLM
        annotated_diff = annotate_diff_with_line_numbers(diff)
        context = build_context(annotated_diff, rules)
    except (FileNotFoundError, ValueError) as exc:
        print(f"invalid input: {exc}", file=sys.stderr)
        return INVALID_INPUT

    # Default model per §26
    model = args.model or "gpt-4o-2024-11-20"

    try:
        client = HeuristicMockReviewerClient() if args.mock else OpenAIClient(model=model)
        result = BaselineReviewer(client).review(context)

        # §9.1: Compute fingerprint for each finding
        # §18.1: Validate line numbers and set line_valid
        for finding in result.reviews:
            # Compute fingerprint
            finding.fingerprint = compute_fingerprint(
                category=finding.category,
                file_path=finding.file,
                enclosing_symbol="unknown",  # Would need AST parsing for accurate value
                title=finding.title,
            )

            # Validate line numbers
            is_valid, adjusted = validate_line_in_hunk(
                diff, finding.file, finding.start_line, finding.end_line
            )
            finding.line_valid = 1 if is_valid else 0
            if adjusted and not is_valid:
                # Optionally adjust to nearest valid line
                pass  # For now, just mark as invalid

    except LLMError as exc:
        print(f"llm call failed: {exc}", file=sys.stderr)
        return LLM_CALL_FAILED
    except InvalidReviewOutput as exc:
        print(f"invalid llm output: {exc}", file=sys.stderr)
        return INVALID_LLM_OUTPUT
    except Exception as exc:
        print(f"internal error: {exc}", file=sys.stderr)
        return INTERNAL_ERROR

    # §22.3: Collect cost metrics
    latency_ms = int((time.perf_counter() - start_time) * 1000)

    # Update metadata with required fields
    result.metadata.update({
        "reviewer_version": "0.1.0",
        "mode": "mock" if args.mock else "baseline",
        "model": model,
        "temperature": args.temperature,
        "prompt_version": args.prompt_version,
        "memory_snapshot_id": None,  # Phase 1: no memory
        "review_time_ms": latency_ms,
        # Cost metrics (will be populated if LLM client provides them)
        "input_tokens": result.metadata.get("input_tokens"),
        "output_tokens": result.metadata.get("output_tokens"),
    })

    output = json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return SUCCESS