from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.llm.client import LLMError, OpenAIClient
from app.review.context_builder import build_context, read_diff_from_file, read_git_diff, read_rules
from app.review.reviewer import BaselineReviewer, HeuristicMockReviewerClient, InvalidReviewOutput

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
    parser.add_argument("--model", help="OpenAI model name. Defaults to OPENAI_MODEL or gpt-5.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        diff = read_git_diff() if args.git_diff else read_diff_from_file(args.diff_file)
        rules = read_rules(args.rules) if args.rules else ""
        context = build_context(diff, rules)
    except (FileNotFoundError, ValueError) as exc:
        print(f"invalid input: {exc}", file=sys.stderr)
        return INVALID_INPUT

    try:
        client = HeuristicMockReviewerClient() if args.mock else OpenAIClient(model=args.model)
        result = BaselineReviewer(client).review(context)
    except LLMError as exc:
        print(f"llm call failed: {exc}", file=sys.stderr)
        return LLM_CALL_FAILED
    except InvalidReviewOutput as exc:
        print(f"invalid llm output: {exc}", file=sys.stderr)
        return INVALID_LLM_OUTPUT
    except Exception as exc:
        print(f"internal error: {exc}", file=sys.stderr)
        return INTERNAL_ERROR

    output = json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return SUCCESS