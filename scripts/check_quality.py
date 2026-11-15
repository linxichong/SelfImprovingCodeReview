"""開発標準の品質検査を共通入口から実行する."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UV = ("uv", "run", "--frozen")


def commands(base_sha: str | None) -> dict[str, list[tuple[str, ...]]]:
    """検査名と引数配列を対応付ける."""
    checks: dict[str, list[tuple[str, ...]]] = {
        "dependency-sync": [("uv", "lock", "--check"), ("uv", "sync", "--frozen", "--all-groups")],
        "lint": [(*UV, "ruff", "format", "--check", "."), (*UV, "ruff", "check", ".")],
        "type-check": [(*UV, "mypy", "src/self_improving_code_review")],
        "traceability": [(*UV, "python", "scripts/check_case_traceability.py")],
        "security-scan": [
            (*UV, "bandit", "-q", "-r", "src/self_improving_code_review"),
            (*UV, "pip-audit"),
            (*UV, "pre-commit", "run", "--all-files"),
        ],
        "evaluation-smoke": [(*UV, "pytest", "-q", "tests/evaluation", "-m", "smoke")],
        "coverage": [
            (
                *UV,
                "pytest",
                "-q",
                "--cov=self_improving_code_review",
                "--cov-report=xml:coverage.xml",
                "--cov-fail-under=85",
            ),
            (
                *UV,
                "diff-cover",
                "coverage.xml",
                f"--compare-branch={base_sha}",
                "--fail-under=90",
                "--format=json:.review/quality/diff-coverage.json",
            ),
        ],
    }
    for level in ("unit", "contract", "integration", "e2e"):
        checks[f"{level}-test"] = [
            (*UV, "pytest", "-q", f"tests/{level}", f"--junitxml=.review/{level}-results.xml")
        ]
    return checks


def run_command(command: tuple[str, ...]) -> tuple[int, float]:
    """外部検査を実行し、本文を保存せず終了状態を返す."""
    started = time.monotonic()
    try:
        result = subprocess.run(command, cwd=ROOT, check=False)
    except OSError:
        return 127, time.monotonic() - started
    return result.returncode, time.monotonic() - started


def git_value(*arguments: str) -> str:
    """信頼済み Git メタデータを取得する."""
    result = subprocess.run(
        ("git", *arguments), cwd=ROOT, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def main() -> int:
    """指定された門禁を実行し、機密本文を含まない結果を保存する."""
    parser = argparse.ArgumentParser(description="运行开发标准规定的质量门禁。")
    parser.add_argument("--mode", choices=("local", "full"), default="local")
    parser.add_argument("--check", choices=tuple(commands(None)))
    parser.add_argument("--base-sha")
    args = parser.parse_args()
    (ROOT / ".review" / "quality").mkdir(parents=True, exist_ok=True)
    checks = commands(args.base_sha)
    selected = (
        [args.check]
        if args.check
        else [
            name
            for name in checks
            if args.mode == "full" or name not in ("coverage", "security-scan")
        ]
    )
    records: list[dict[str, object]] = []
    for name in selected:
        if name == "coverage" and (
            args.base_sha is None or re.fullmatch(r"[0-9a-f]{40}", args.base_sha) is None
        ):
            records.append({"name": name, "status": "not_run", "reason": "BASE_SHA_REQUIRED"})
            continue
        for command in checks[name]:
            exit_code, duration = run_command(command)
            records.append(
                {
                    "name": name,
                    "command": command,
                    "exit_code": exit_code,
                    "duration_seconds": duration,
                    "status": "passed" if exit_code == 0 else "failed",
                }
            )
            if name == "coverage" and command[3] == "diff-cover" and exit_code == 0:
                coverage = json.loads(
                    (ROOT / ".review/quality/diff-coverage.json").read_text(encoding="utf-8")
                )
                records[-1]["executable_lines"] = coverage["total_num_lines"]
                if coverage["total_num_lines"] == 0:
                    records[-1]["status"] = "not_applicable"
                    records[-1]["reason"] = "ZERO_EXECUTABLE_LINES"
            if exit_code:
                records.extend(
                    {
                        "name": name,
                        "command": pending,
                        "status": "not_run",
                        "reason": "PREVIOUS_COMMAND_FAILED",
                    }
                    for pending in checks[name][checks[name].index(command) + 1 :]
                )
                break
    passed = all(record["status"] in ("passed", "not_applicable") for record in records)
    report = {
        "status": "passed" if passed else "failed",
        "commit_sha": git_value("rev-parse", "HEAD"),
        "is_dirty": bool(git_value("status", "--porcelain")),
        "base_sha": args.base_sha,
        "checks": records,
    }
    destination = ROOT / ".review" / "quality"
    destination.mkdir(parents=True, exist_ok=True)
    label = args.check or args.mode
    (destination / f"{label}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
