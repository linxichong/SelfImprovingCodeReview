"""未有効化の Python 候補規則と開発ケースの構造契約を検証する。"""

from __future__ import annotations

import ast
import hashlib
from collections import Counter
from pathlib import Path

import pytest
import yaml

from self_improving_code_review.application.config.settings import ReviewConfig
from self_improving_code_review.application.review.bundler import SimpleBundler
from self_improving_code_review.application.review.planner import RuleBasedReviewPlanner
from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.common.enums import ReviewMode
from self_improving_code_review.domain.common.values import RunId
from self_improving_code_review.domain.rule.loader import RuleLoader
from self_improving_code_review.security.models import ScanProfile, SourceKind, SourcePayload
from self_improving_code_review.security.secret_scanner import DetectSecretsScanner

ROOT = Path(__file__).parents[2]
DATASET = ROOT / "datasets/development/python_rule_candidates/v1"
RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")
PATCHES = tuple(sorted((DATASET / "patches").glob("*.patch")))


def test_manifest_pins_all_rules_cases_and_reference_sources() -> None:
    """規則とケースの変更検出、出典対応、未承認状態を固定する。"""
    manifest = yaml.safe_load((DATASET / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["split"] == "dev"
    assert manifest["status"] == "development-only-not-formal-bc"
    assert manifest["default_activation"] is False
    assert len(manifest["rules"]) == 6
    assert len(manifest["cases"]) == 20
    sources = {source["id"] for source in manifest["sources"]}
    expected_paths = {case["patch"] for case in manifest["cases"]}
    assert expected_paths == {path.relative_to(DATASET).as_posix() for path in PATCHES}
    for entry in (*manifest["rules"], *manifest["cases"]):
        relative_path = entry.get("path", entry.get("patch"))
        path = (DATASET / relative_path).resolve()
        assert path.is_relative_to(DATASET.resolve())
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    for rule in manifest["rules"]:
        assert set(rule["source_ids"]) <= sources
        dispositions = Counter(
            case["expected_disposition"]
            for case in manifest["cases"]
            if case["rule_id"] == rule["id"]
        )
        assert dispositions == {"finding": 1, "no_finding": 1, "needs_context": 1}
    for case in manifest["cases"]:
        assert case["status"] == "candidate"
        assert case["verified_by"] is None
        assert case["approved_by"] is None


@pytest.mark.parametrize("patch_path", PATCHES, ids=lambda path: path.stem)
def test_case_diff_positions_syntax_and_strict_scan_are_valid(patch_path: Path) -> None:
    """行範囲と送信可能性を検証し、コードやモデル応答は実行しない。"""
    text = patch_path.read_text(encoding="utf-8")
    manifest = yaml.safe_load((DATASET / "manifest.yaml").read_text(encoding="utf-8"))
    case = next(case for case in manifest["cases"] if case["patch"] == f"patches/{patch_path.name}")
    files = UnifiedDiffParser().parse(text).files
    assert len(files) == 1
    changed_file = files[0]
    assert changed_file.new_path.value == case["target_file"]
    assert case["target_line"] in changed_file.added_line_numbers
    if changed_file.language == "python":
        source = "\n".join(
            line.text
            for hunk in changed_file.hunks
            for line in hunk.lines
            if line.new_line is not None
        )
        ast.parse(source)
    scan = DetectSecretsScanner().scan(
        SourcePayload(kind=SourceKind.DIFF, label="candidate-case", text=text),
        ScanProfile.STRICT,
    )
    assert scan.is_passed


@pytest.mark.parametrize("patch_path", PATCHES, ids=lambda path: path.stem)
def test_candidates_match_python_cases_and_baseline_remains_empty(patch_path: Path) -> None:
    """選択条件を検査し、期待問題の成立や LLM 精度とは区別する。"""
    manifest = yaml.safe_load((DATASET / "manifest.yaml").read_text(encoding="utf-8"))
    case = next(case for case in manifest["cases"] if case["patch"] == f"patches/{patch_path.name}")
    files = UnifiedDiffParser().parse(patch_path.read_text(encoding="utf-8")).files
    bundle = SimpleBundler(max_diff_tokens=8000).bundle(RUN_ID, files).bundles[0]
    snapshot = RuleLoader().load(DATASET / "rules", git_sha=None)
    planner = RuleBasedReviewPlanner(ReviewConfig())
    baseline, baseline_rules = planner.plan(bundle, snapshot.rules, ReviewMode.BASELINE)
    assert baseline_rules == ()
    assert baseline.applied_rule_ids == ()
    assert baseline.file_rule_bindings == ()
    plan, _ = planner.plan(bundle, snapshot.rules, ReviewMode.RULES)
    if case["rule_id"] is None:
        assert plan.applied_rule_ids == ()
    else:
        assert case["rule_id"] in plan.applied_rule_ids


def test_candidate_rules_do_not_enter_the_default_rule_directory() -> None:
    """候補追加が通常レビューの規則集合を変更しないことを確認する。"""
    active = RuleLoader().load(ROOT / "config/rules", git_sha=None)
    candidate = RuleLoader().load(DATASET / "rules", git_sha=None)
    active_ids = {rule.id for rule in active.rules}
    candidate_ids = {rule.id for rule in candidate.rules}
    assert not (active_ids & candidate_ids)
    assert not (ROOT / "config/rules").resolve().is_relative_to(DATASET.resolve())
