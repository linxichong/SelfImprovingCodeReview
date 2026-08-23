from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

REVIEW_RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reviews", "metadata"],
    "properties": {
        "reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "file",
                    "start_line",
                    "end_line",
                    "category",
                    "severity",
                    "title",
                    "description",
                    "evidence",
                    "suggestion",
                    "confidence",
                    "used_memory_ids",
                ],
                "properties": {
                    "id": {"type": "string"},
                    "file": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                    "category": {"type": "string", "enum": ["bug", "security", "maintainability", "test"]},
                    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "evidence": {"type": "string"},
                    "suggestion": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "used_memory_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "metadata": {
            "type": "object",
            "additionalProperties": True,
        },
    },
}


class LLMError(RuntimeError):
    pass


class OpenAIClient:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-5")
        if not self.api_key:
            raise LLMError("OPENAI_API_KEY is not set")

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        payload = {
            "model": self.model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "code_review_result",
                    "schema": REVIEW_RESULT_SCHEMA,
                    "strict": True,
                }
            },
        }
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMError(f"OpenAI API failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"OpenAI API failed: {exc.reason}") from exc

        text = _extract_response_text(body)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"model returned invalid JSON: {text[:500]}") from exc


def _extract_response_text(body: dict) -> str:
    if isinstance(body.get("output_text"), str):
        return body["output_text"]

    chunks: list[str] = []
    for item in body.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    if chunks:
        return "".join(chunks)
    raise LLMError("OpenAI response did not contain output text")