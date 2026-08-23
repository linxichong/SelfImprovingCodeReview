from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ReviewContext:
    diff: str
    rules: str

    def as_prompt_context(self) -> str:
        return f"""## Project Coding Rules

{self.rules or "No project-specific rules were provided."}

## Git Diff

```diff
{self.diff}
```"""


def read_diff_from_file(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"diff file not found: {path}")
    return path.read_text(encoding="utf-8")


def read_git_diff() -> str:
    result = subprocess.run(
        ["git", "diff", "--cached"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff --cached failed")
    if result.stdout.strip():
        return result.stdout

    result = subprocess.run(
        ["git", "diff"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff failed")
    return result.stdout


def read_rules(path: Path | None) -> str:
    if path is None:
        return ""
    if not path.exists():
        raise FileNotFoundError(f"rules file not found: {path}")
    return path.read_text(encoding="utf-8")


def build_context(diff: str, rules: str) -> ReviewContext:
    if not diff.strip():
        raise ValueError("diff is empty")
    return ReviewContext(diff=diff, rules=rules)