from __future__ import annotations

import re
import time
from typing import Protocol

from app.llm.prompts import BASELINE_REVIEW_SYSTEM_PROMPT
from app.review.context_builder import ReviewContext
from app.review.models import ReviewResult

REVIEWER_VERSION = "0.1.0"


class InvalidReviewOutput(ValueError):
    pass


class JsonReviewerClient(Protocol):
    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        ...


class BaselineReviewer:
    def __init__(self, client: JsonReviewerClient) -> None:
        self.client = client

    def review(self, context: ReviewContext) -> ReviewResult:
        started = time.perf_counter()
        raw_result = self.client.complete_json(
            BASELINE_REVIEW_SYSTEM_PROMPT,
            context.as_prompt_context(),
        )
        try:
            result = ReviewResult.from_dict(raw_result)
        except ValueError as exc:
            raise InvalidReviewOutput(str(exc)) from exc
        result.metadata.setdefault("reviewer_version", REVIEWER_VERSION)
        result.metadata.setdefault("mode", "baseline")
        result.metadata.setdefault("review_time_ms", int((time.perf_counter() - started) * 1000))
        return result


class HeuristicMockReviewerClient:
    """Deterministic local reviewer for smoke tests and demos without an API key."""

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        reviews = []
        if "TODO" in user_prompt or "FIXME" in user_prompt:
            file_name, line_number = _first_changed_location(user_prompt)
            reviews.append(
                {
                    "id": "R001",
                    "file": file_name,
                    "start_line": line_number,
                    "end_line": line_number,
                    "category": "maintainability",
                    "severity": "low",
                    "title": "Unresolved TODO in changed code",
                    "description": "The diff contains TODO/FIXME text that may represent unfinished work.",
                    "evidence": "TODO or FIXME appears in the provided diff.",
                    "suggestion": "Resolve the TODO before merging or link it to a tracked follow-up.",
                    "confidence": 0.7,
                    "fingerprint": "",  # Will be computed in cli.py
                    "line_valid": 1,    # Mock always returns valid lines
                    "used_memory_ids": [],
                }
            )
        return {"reviews": reviews, "metadata": {"mode": "mock"}}


def _first_changed_location(prompt: str) -> tuple[str, int]:
    file_name = "unknown"
    current_new_line = 1
    for line in prompt.splitlines():
        if line.startswith("+++ b/"):
            file_name = line.removeprefix("+++ b/")
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            if match:
                current_new_line = int(match.group(1))
        elif line.startswith("+") and not line.startswith("+++"):
            return file_name, current_new_line
        elif not line.startswith("-"):
            current_new_line += 1
    return file_name, current_new_line