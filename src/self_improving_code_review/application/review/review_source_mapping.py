"""原文差分の選択と機密検出位置のソース行対応を決定する."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import cast

from self_improving_code_review.application.review.models import (
    ReviewBundle,
)
from self_improving_code_review.domain.change.models import ChangedFile
from self_improving_code_review.security.models import (
    SecretFinding,
)


def select_reviewable_raw_diff(
    raw_diff_text: str,
    changed_files: tuple[ChangedFile, ...],
    reviewable_paths: set[str],
) -> str:
    """適用可能な原文形式を保ったまま審査対象ファイルの差分だけを返す."""
    starts = tuple(match.start() for match in re.finditer(r"(?m)^diff --git ", raw_diff_text))
    ends = (*starts[1:], len(raw_diff_text))
    blocks = tuple(raw_diff_text[start:end] for start, end in zip(starts, ends, strict=True))
    if len(blocks) != len(changed_files):
        # 解析結果との対応が崩れた場合は原文を検証させ、検証を弱めない。
        return raw_diff_text
    return "".join(
        block
        for block, changed_file in zip(blocks, changed_files, strict=True)
        if changed_file.new_path.value in reviewable_paths
    )


def map_secret_findings(
    bundle: ReviewBundle,
    findings: tuple[SecretFinding, ...],
) -> tuple[SecretFinding, ...]:
    """レビュー単位相対の検査行をファイルパスと変更前後の行へ対応付ける."""
    spans: list[tuple[int, int, str, dict[int, tuple[int, str]]]] = []
    bundle_start = 1
    for changed_file in bundle.files:
        line_count = len(changed_file.to_diff_text().splitlines())
        bundle_end = bundle_start + line_count - 1
        spans.append(
            (
                bundle_start,
                bundle_end,
                changed_file.new_path.value,
                source_lines_by_file_diff_line(changed_file),
            )
        )
        bundle_start = bundle_end + 1

    mapped: list[SecretFinding] = []
    seen: set[tuple[str, str, int, str]] = set()
    for finding in findings:
        replacement = finding
        for start, end, path, source_lines in spans:
            if start <= finding.line_number <= end:
                file_diff_line = finding.line_number - start + 1
                source_line, line_side = source_lines.get(
                    file_diff_line,
                    (file_diff_line, "diff"),
                )
                replacement = replace(
                    finding,
                    path=path,
                    line_number=source_line,
                    line_side=line_side,
                )
                break
        key = (
            replacement.detector,
            replacement.path,
            replacement.line_number,
            replacement.line_side,
        )
        if key not in seen:
            mapped.append(replacement)
            seen.add(key)
    return tuple(mapped)


def source_lines_by_file_diff_line(changed_file: ChangedFile) -> dict[int, tuple[int, str]]:
    """描画済みファイル差分の行を変更前後の行番号へ対応付ける."""
    mapping: dict[int, tuple[int, str]] = {}
    file_diff_line = 1
    for hunk in changed_file.hunks:
        file_diff_line += 1
        for line in hunk.lines:
            file_diff_line += 1
            if line.new_line is not None:
                mapping[file_diff_line] = (line.new_line, "new")
            else:
                mapping[file_diff_line] = (cast(int, line.old_line), "old")
    return mapping
