"""固定された上流の評価器と Codex アダプターを別プロセスで実行する."""

import asyncio
import contextlib
import importlib
import io
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path


def score() -> dict[str, object]:
    """公式のファイル単位評価を実行し、不正な裁定を失敗として返す."""
    judge = importlib.import_module("judge")
    data = json.load(sys.stdin)
    refs = data["references"]
    log = io.StringIO()
    handler = logging.StreamHandler(log)
    handler.setLevel(logging.ERROR)
    logging.getLogger().addHandler(handler)
    try:
        hits, matched, rows = asyncio.run(
            judge.evaluate_file_level_semantics(refs, data["findings"])
        )
    finally:
        logging.getLogger().removeHandler(handler)
    ref_paths = {judge._normalize_path(ref["path"]) for ref in refs}
    if log.getvalue() or any(
        row["path"] in ref_paths and not re.search(r"\[[\d,\s]*\]", row["judge_reason"])
        for row in rows
    ):
        return {}
    return {
        "hit_n": hits,
        "matched_generated_n": matched,
        "matched_reference_indices": [
            index for index, ref in enumerate(refs, start=1) if ref.get("file_semantic_match")
        ],
    }


def review_codex() -> dict[str, object]:
    """MCP と構造化応答から指摘、子セッションから使用量を取得する."""
    adapter = importlib.import_module("reviewers.codex")
    repo, base, head, instance = sys.argv[3:7]
    env = {name: os.environ[name] for name in ("CODEX_API_KEY", "CODEX_MODEL", "CODEX_GATEWAY_URL")}
    home = adapter.write_codex_config(env)
    try:
        with tempfile.TemporaryDirectory(prefix="aacr-findings-") as output:
            result = adapter.run_codex_review(
                Path(repo), base, head, instance, Path(output), env, home, 3
            )
            content, meta = adapter.extract_from_jsonl(result.stdout)
            usage = meta.get("token_usage") or {}
            if adapter._usage_is_zero(usage):
                usage = adapter._extract_usage_from_session(home, meta.get("thread_id")) or {}
            mcp = adapter.load_streamed_findings(Path(output), instance) or []
            parsed = adapter.extract_findings_from_result(content) if content.strip() else None
            if isinstance(parsed, dict):
                parsed = parsed.get("findings")
            if not isinstance(parsed, list) and re.search(
                r"\bno new blocking or actionable defects were identified[.!]?$",
                content.strip(),
                re.IGNORECASE,
            ):
                parsed = []
            valid = isinstance(parsed, list) or bool(mcp)
            findings = parsed if isinstance(parsed, list) and len(parsed) > len(mcp) else mcp
            status = "timeout" if result.returncode == 124 else "failed"
            if result.returncode == 0 and valid:
                status = "complete"
            elif result.returncode == 0:
                status = "invalid"
            return {"status": status, "usage": usage, "findings": findings}
    finally:
        adapter.cleanup_codex_home(home)


def main() -> int:
    """固定された上流チェックアウトに対する内部プロトコルを提供する."""
    sys.path.insert(0, str(Path(sys.argv[1]) / "evaluation"))
    with contextlib.redirect_stdout(io.StringIO()):
        result = score() if sys.argv[2] == "score" else review_codex()
    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
