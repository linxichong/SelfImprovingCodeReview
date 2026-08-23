"""Utility functions for Phase 1: fingerprint, line number handling."""

from __future__ import annotations

import re
from dataclasses import dataclass


# Stopwords for fingerprint issue_key normalization (common English words)
STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "need", "dare",
    "ought", "used", "to", "of", "in", "for", "on", "with", "at", "by",
    "from", "as", "into", "through", "during", "before", "after",
    "above", "below", "between", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all",
    "each", "few", "more", "most", "other", "some", "such", "no", "nor",
    "not", "only", "own", "same", "so", "than", "too", "very", "just",
    "if", "and", "but", "or", "because", "until", "while", "this", "that",
    "these", "those", "it", "its", "they", "their", "them", "what",
    "which", "who", "whom", "we", "you", "he", "she", "him", "her", "his",
})


@dataclass(slots=True)
class DiffHunk:
    """Parsed hunk header and line mappings."""
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    # Mapping from new file line number to old file line number (or None for new lines)
    new_to_old: dict[int, int | None]


def parse_hunk_header(header: str) -> DiffHunk:
    """Parse a unified diff hunk header like '@@ -80,6 +82,8 @@'."""
    # Match @@ -old_start,old_count +new_start,new_count @@
    match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header)
    if not match:
        raise ValueError(f"invalid hunk header: {header}")

    old_start = int(match.group(1))
    old_count = int(match.group(2)) if match.group(2) else 1
    new_start = int(match.group(3))
    new_count = int(match.group(4)) if match.group(4) else 1

    # Build mapping from new file line to old file line
    new_to_old: dict[int, int | None] = {}
    old_line = old_start
    new_line = new_start

    # The header line itself is not part of the diff content
    # We'll populate this in annotate_diff_with_line_numbers
    return DiffHunk(
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        new_to_old=new_to_old,
    )


def annotate_diff_with_line_numbers(diff: str) -> str:
    """
    Annotate each line of the diff with its absolute line number in the new file.

    §18.1 Input side: Pre-annotate absolute line numbers before sending to LLM.
    Format: '  100    code line' where 100 is the new file absolute line number.
    """
    lines = diff.splitlines()
    result: list[str] = []
    current_hunk: DiffHunk | None = None

    for line in lines:
        # Detect hunk header
        if line.startswith("@@"):
            current_hunk = parse_hunk_header(line)
            result.append(line)
            continue

        if current_hunk is None:
            # Before first hunk, just pass through
            result.append(line)
            continue

        # Determine line type and annotate
        if line.startswith("+"):
            # New line in new file
            abs_line = current_hunk.new_start
            current_hunk.new_start += 1
            # Add to mapping: new line -> None (doesn't exist in old)
            current_hunk.new_to_old[abs_line] = None
            result.append(f"{abs_line:6d}    {line}")
        elif line.startswith("-"):
            # Deleted line - doesn't exist in new file, don't assign new line number
            # Optionally could mark with old file line, but for simplicity skip
            result.append(line)
        elif line.startswith(" ") or line == "":
            # Context line - exists in both
            abs_line = current_hunk.new_start
            old_line = current_hunk.old_start
            current_hunk.new_start += 1
            current_hunk.old_start += 1
            # Add to mapping: new line -> old line
            current_hunk.new_to_old[abs_line] = old_line
            result.append(f"{abs_line:6d}    {line}")
        else:
            # Any other line (e.g., index header, filename)
            result.append(line)

    return "\n".join(result)


def compute_fingerprint(
    category: str,
    file_path: str,
    enclosing_symbol: str,
    title: str,
) -> str:
    """
    Compute a stable fingerprint for a finding.

    §9.1: fingerprint = category : normalized_path : enclosing_symbol : issue_key

    - category: directly from finding.category
    - normalized_path: relative path from repo root
    - enclosing_symbol: function/method name where the finding is located
    - issue_key: normalized title (lowercase, stopwords removed, first tokens)
    """
    # Normalize path: use forward slashes, no leading/trailing slashes
    normalized = file_path.strip("/").replace("\\", "/")

    # Normalize title for issue_key
    issue_key = normalize_title(title)

    return f"{category}:{normalized}:{enclosing_symbol}:{issue_key}"


def normalize_title(title: str) -> str:
    """
    Normalize title for fingerprint issue_key.

    - Lowercase
    - Remove stopwords
    - Keep alphanumeric and underscore
    - Take first few significant tokens
    """
    # Lowercase and split
    tokens = title.lower().split()

    # Remove stopwords and non-alphanumeric (keep underscores)
    significant = [
        re.sub(r"[^a-z0-9]", "", t)
        for t in tokens
        if t not in STOPWORDS and re.search(r"[a-z0-9]", t)
    ]

    # Take first 4 tokens as issue key
    return "-".join(significant[:4]) if significant else "unknown"


def extract_enclosing_symbol_from_diff(
    diff: str,
    file_path: str,
    line: int,
) -> str:
    """
    Try to extract the enclosing function/method for a given line in the diff.

    This is a heuristic based on common patterns:
    - def function_name(
    - class ClassName(
    - func function_name(
    - method_name(
    """
    # This is a simplified version - in production would need language-specific parsers
    # For now, return a placeholder that can be improved later
    return "unknown"


def validate_line_in_hunk(
    diff: str,
    file_path: str,
    start_line: int,
    end_line: int,
) -> tuple[bool, int | None]:
    """
    Validate that line numbers fall within the hunk range for the given file.

    §18.1 Output side: Validate that LLM-provided line numbers are within the diff's hunk range.

    Returns:
        (is_valid, adjusted_line or None)
            - is_valid: True if line is in valid range
            - adjusted_line: If line could be adjusted to nearest hunk line, else None
    """
    # Parse all hunks for this file
    hunks = parse_file_hunks(diff, file_path)

    if not hunks:
        # No hunks found for this file - can't validate
        return True, None

    # Collect all valid new file line numbers
    valid_lines: set[int] = set()
    for hunk in hunks:
        valid_lines.update(hunk.new_to_old.keys())

    if start_line in valid_lines and end_line in valid_lines:
        return True, None

    # Try to find nearest line
    if valid_lines:
        nearest = min(valid_lines, key=lambda l: abs(l - start_line))
        distance = abs(nearest - start_line)
        # If within tolerance (3 lines), adjust
        if distance <= 3:
            return False, nearest

    return False, None


def parse_file_hunks(diff: str, target_file: str) -> list[DiffHunk]:
    """Parse all hunks for a specific file from the diff."""
    hunks: list[DiffHunk] = []
    current_file = ""
    current_hunk: DiffHunk | None = None

    for line in diff.splitlines():
        # Detect file header
        if line.startswith("diff --git") or line.startswith("--- "):
            # Extract filename from --- a/path/to/file
            if line.startswith("--- "):
                # Parse --- a/file b/file
                parts = line.split()
                if len(parts) >= 2:
                    # a/file or b/file
                    path = parts[1].lstrip("a/b/")
                    current_file = path
            continue

        # Detect hunk header
        if line.startswith("@@") and current_file:
            # Check if this hunk belongs to target file
            # For simplicity, associate with current_file
            hunk = parse_hunk_header(line)
            # Initialize mapping
            old_line = hunk.old_start
            new_line = hunk.new_start
            for _ in range(hunk.new_count):
                hunk.new_to_old[new_line] = old_line if old_line <= hunk.old_start + hunk.old_count - 1 else None
                new_line += 1
                old_line += 1
            hunks.append(hunk)

    # Filter hunks for target file (simplified - in reality need proper file association)
    return hunks