"""実 LLM 接続確認用データセットの構造契約。"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser

_DATASET_ROOT = Path(__file__).parents[2] / "datasets" / "real_llm_smoke"


def test_real_llm_smoke_patches_match_manifest_and_declared_line_counts() -> None:
    """全ケースの差分行数、Python 構文、期待位置を検証する。"""
    manifest = yaml.safe_load((_DATASET_ROOT / "manifest.yaml").read_text(encoding="utf-8"))
    cases = manifest["cases"]

    assert {case["patch"] for case in cases} == {
        f"patches/{path.name}" for path in (_DATASET_ROOT / "patches").glob("*.patch")
    }
    for case in cases:
        patch_text = (_DATASET_ROOT / case["patch"]).read_text(encoding="utf-8")
        changed_file = UnifiedDiffParser().parse(patch_text).files[0]

        for hunk in changed_file.hunks:
            assert sum(line.old_line is not None for line in hunk.lines) == hunk.old_count
            assert sum(line.new_line is not None for line in hunk.lines) == hunk.new_count

        source = "\n".join(
            line.text
            for hunk in changed_file.hunks
            for line in hunk.lines
            if line.new_line is not None
        )
        ast.parse(source)
        for expected in case["expected_findings"]:
            assert expected["file"] == changed_file.new_path.value
            assert expected["start_line"] in changed_file.added_line_numbers
