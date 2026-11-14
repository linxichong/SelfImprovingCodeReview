"""正式規模評価候補の生成結果を検証する。"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from self_improving_code_review.cli import ground_truth_commands
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.models import ScanProfile, SourceKind, SourcePayload
from self_improving_code_review.security.secret_scanner import DetectSecretsScanner


@pytest.mark.case("IT-M2-018a")
def test_v7_candidate_preserves_population_and_requires_approval(tmp_path: Path) -> None:
    """v7 の HTTP 対照例、未承認状態、既存先の上書き拒否を確認する。"""
    script = (
        Path(__file__).resolve().parents[2] / "datasets/evaluation/prepare_test_v7_candidate.py"
    )
    repository = tmp_path / "formal-v7"
    command = [sys.executable, str(script), "--repository", str(repository)]
    completed = subprocess.run(
        command, check=True, capture_output=True, text=True, encoding="utf-8"
    )
    assert json.loads(completed.stdout)["publication_ready"] is False
    document = json.loads(
        (repository / "datasets/evaluation/ground-truth-test-v7.candidate.json").read_text(
            encoding="utf-8"
        )
    )
    assert document["version"] == 7
    assert len(document["cases"]) == 32
    assert len(document["issues"]) == 30
    assert sum(issue["severity"] == "critical" for issue in document["issues"]) == 8
    for case in document["cases"][-4:]:
        paths = subprocess.check_output(
            [
                "git",
                "-C",
                str(repository),
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                case["change_ref"],
            ],
            text=True,
        ).splitlines()
        assert len(paths) == 1
        source = subprocess.check_output(
            ["git", "-C", str(repository), "show", f"{case['change_ref']}:{paths[0]}"], text=True
        )
        assert sum(isinstance(node, ast.With) for node in ast.walk(ast.parse(source))) == 1
        assert case["approved_by"] == "REVIEW_REQUIRED"
        assert case["created_by"] == case["verified_by"]
    parsed = ground_truth_commands._GroundTruthDocument.model_validate(document)
    with pytest.raises(InvalidInputError):
        ground_truth_commands._validate_document(parsed)
    repeated = subprocess.run(
        command, check=False, capture_output=True, text=True, encoding="utf-8"
    )
    assert repeated.returncode == 1
    assert re.search(re.escape(Messages.EVALUATION_CANDIDATE_TARGET_EXISTS), repeated.stderr)


@pytest.mark.case("IT-M2-018")
def test_formal_candidate_has_required_population_and_resolvable_locations(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    script = project_root / "datasets/evaluation/prepare_formal_candidate.py"
    repository = tmp_path / "formal-evaluation"

    completed = subprocess.run(
        [sys.executable, str(script), "--repository", str(repository)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    summary = json.loads(completed.stdout)
    candidate = repository / "datasets/evaluation/ground-truth-test-v2.candidate.json"
    document = json.loads(candidate.read_text(encoding="utf-8"))
    issues = document["issues"]
    critical = [issue for issue in issues if issue["severity"] == "critical"]

    assert summary["publication_ready"] is False
    assert len(issues) == 30
    assert len({issue["change_ref"] for issue in issues}) == 20
    assert len(critical) == 9
    assert len({issue["change_ref"] for issue in critical}) >= 3
    for issue in issues:
        source = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "show",
                f"{issue['change_ref']}:{issue['file']}",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout
        assert source.splitlines()[issue["start_line"] - 1].strip()

    parsed = ground_truth_commands._GroundTruthDocument.model_validate(document)
    with pytest.raises(InvalidInputError, match="検証者"):
        ground_truth_commands._validate_document(parsed)

    repeated = subprocess.run(
        [sys.executable, str(script), "--repository", str(repository)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert repeated.returncode == 1
    assert "すでに存在" in repeated.stderr


@pytest.mark.case("IT-M2-018a")
def test_v3_candidate_includes_clean_cases_and_uses_schema_version_two(
    tmp_path: Path,
) -> None:
    """v3 候補の全ケース、問題位置、未承認状態を検証する."""
    project_root = Path(__file__).resolve().parents[2]
    script = project_root / "datasets/evaluation/prepare_test_v3_candidate.py"
    repository = tmp_path / "formal-evaluation-v3"

    completed = subprocess.run(
        [sys.executable, str(script), "--repository", str(repository)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    summary = json.loads(completed.stdout)
    candidate = repository / "datasets/evaluation/ground-truth-test-v3.candidate.json"
    document = json.loads(candidate.read_text(encoding="utf-8"))
    cases = document["cases"]
    issues = document["issues"]
    issue_change_refs = {issue["change_ref"] for issue in issues}

    assert document["schema_version"] == 2
    assert document["version"] == 3
    assert summary["publication_ready"] is False
    assert len(cases) == 28
    assert len({case["change_ref"] for case in cases}) == 28
    assert len({case["change_ref"] for case in cases} - issue_change_refs) == 8
    assert len(issues) == 30
    assert sum(issue["severity"] == "critical" for issue in issues) == 8
    scanner = DetectSecretsScanner()
    for case in cases:
        diff = subprocess.run(
            ["git", "-C", str(repository), "show", "--format=", case["change_ref"]],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout
        scan = scanner.scan(
            SourcePayload(SourceKind.DIFF, str(case["change_ref"]), diff),
            ScanProfile.STRICT,
        )
        assert scan.is_passed
    for issue in issues:
        source = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "show",
                f"{issue['change_ref']}:{issue['file']}",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout
        assert source.splitlines()[issue["start_line"] - 1].strip()

    parsed = ground_truth_commands._GroundTruthDocument.model_validate(document)
    with pytest.raises(InvalidInputError, match="検証者"):
        ground_truth_commands._validate_document(parsed)
