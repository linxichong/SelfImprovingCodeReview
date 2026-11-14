from __future__ import annotations

from pathlib import Path

import pytest

from self_improving_code_review.application.review.bundler import SimpleBundler
from self_improving_code_review.application.review.models import ReviewBundle
from self_improving_code_review.application.review.review_manager import ReviewManager
from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.common.enums import (
    ReflectionStatus,
    Severity,
    ValidationStatus,
)
from self_improving_code_review.domain.common.values import LineRange, NormalizedPath, RunId
from self_improving_code_review.domain.finding.models import (
    CandidateFinding,
    Evidence,
    build_general_rule_id,
)
from self_improving_code_review.domain.rule.loader import RuleLoader
from self_improving_code_review.domain.rule.models import (
    FileRuleBinding,
    RuleSnapshot,
    RuleSnapshotItem,
)

RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")
EMPTY_SNAPSHOT: RuleSnapshot = RuleLoader().load(Path("missing-rules"), git_sha=None)
PROJECT_RULE_ID = "PYTHON.NULL_DEREFERENCE"
PROJECT_SNAPSHOT = RuleSnapshot(
    snapshot_id="snapshot",
    git_sha="abc",
    rule_set_hash="hash",
    items=(RuleSnapshotItem(PROJECT_RULE_ID, "rule.yaml", "content-hash"),),
    rules=(),
)


@pytest.mark.case("UT-M1-010")
def test_valid_candidate_is_accepted_with_reflection_skipped() -> None:
    bundle = _bundle()

    outcome = ReviewManager().validate_bundle(
        bundle, (_candidate(),), bundle.diff_text, EMPTY_SNAPSHOT
    )

    finding = outcome.findings[0]
    assert outcome.rejected == ()
    assert finding.validation.position_status is ValidationStatus.VALID
    assert finding.validation.evidence_status is ValidationStatus.VALID
    assert finding.validation.reflection_status is ReflectionStatus.REFLECTION_SKIPPED
    assert finding.validation.reason_codes == ()
    assert finding.is_main
    assert str(finding.finding_id) == f"{bundle.bundle_id}-F001"
    assert finding.enclosing_symbol == "unknown"


@pytest.mark.case("UT-M1-010")
def test_candidate_for_a_file_outside_the_bundle_is_rejected() -> None:
    bundle = _bundle()
    candidate = _candidate(file="src/other.py")

    outcome = ReviewManager().validate_bundle(
        bundle, (candidate,), bundle.diff_text, EMPTY_SNAPSHOT
    )

    assert outcome.findings == ()
    assert outcome.rejected[0].reason_code == "FILE_NOT_IN_BUNDLE"
    assert outcome.rejected[0].detail == "src/other.py"


@pytest.mark.case("UT-M1-010")
def test_candidate_with_an_unknown_rule_id_is_rejected() -> None:
    bundle = _bundle()
    candidate = _candidate(rule_id="not-a-rule-id")

    outcome = ReviewManager().validate_bundle(
        bundle, (candidate,), bundle.diff_text, EMPTY_SNAPSHOT
    )

    assert outcome.findings == ()
    assert outcome.rejected[0].reason_code == "UNKNOWN_RULE_ID"


@pytest.mark.case("UT-M1-010")
def test_general_rule_id_is_normalized_from_issue_type() -> None:
    """一般名前空間の表記揺れを issue_type から決定的に補正する."""
    bundle = _bundle()
    candidate = _candidate(
        issue_type="null-dereference",
        rule_id="GENERAL.NULL_CHECK",
    )

    outcome = ReviewManager().validate_bundle(
        bundle, (candidate,), bundle.diff_text, EMPTY_SNAPSHOT
    )

    assert outcome.rejected == ()
    assert outcome.findings[0].candidate.rule_id == "GENERAL.NULL_DEREFERENCE"


@pytest.mark.case("UT-M1-010")
def test_model_position_is_replaced_by_the_evidence_line() -> None:
    bundle = _bundle()
    candidate = _candidate(line_range=LineRange(99, 99))

    outcome = ReviewManager().validate_bundle(
        bundle, (candidate,), bundle.diff_text, EMPTY_SNAPSHOT
    )

    finding = outcome.findings[0]
    assert finding.candidate.line_range == LineRange(2, 2)
    assert finding.validation.position_status is ValidationStatus.VALID
    assert finding.validation.reason_codes == ()
    assert finding.is_main


@pytest.mark.case("UT-M1-010")
def test_evidence_absent_from_the_sent_payload_is_recorded() -> None:
    bundle = _bundle()
    candidate = _candidate(quote="never.sent")

    outcome = ReviewManager().validate_bundle(
        bundle, (candidate,), bundle.diff_text, EMPTY_SNAPSHOT
    )

    finding = outcome.findings[0]
    assert finding.validation.evidence_status is ValidationStatus.INVALID
    assert "EVIDENCE_NOT_IN_PAYLOAD" in finding.validation.reason_codes
    assert not finding.is_main


@pytest.mark.case("UT-M1-010")
def test_valid_python_change_rejects_a_false_syntax_error() -> None:
    diff = (
        "diff --git a/src/archive.py b/src/archive.py\n"
        "--- a/src/archive.py\n"
        "+++ b/src/archive.py\n"
        "@@ -1,0 +1,2 @@\n"
        "+def load():\n"
        "+    return run(shell=False)\n"
    )
    bundle = _bundle_from_diff(diff)
    candidate = _candidate(
        file="src/archive.py",
        issue_type="syntax-error",
        rule_id="GENERAL.SYNTAX_ERROR",
        quote="return run(shell=False)",
    )

    outcome = ReviewManager().validate_bundle(
        bundle, (candidate,), bundle.diff_text, EMPTY_SNAPSHOT
    )

    assert outcome.findings == ()
    assert outcome.rejected[0].reason_code == "PYTHON_SYNTAX_VALID"


@pytest.mark.case("UT-M1-010")
def test_needs_context_candidate_is_marked_as_needing_context() -> None:
    bundle = _bundle()
    candidate = _candidate(needs_context=True)

    outcome = ReviewManager().validate_bundle(
        bundle, (candidate,), bundle.diff_text, EMPTY_SNAPSHOT
    )

    assert outcome.findings[0].validation.reflection_status is ReflectionStatus.NEEDS_CONTEXT
    assert not outcome.findings[0].is_main


@pytest.mark.case("UT-M1-010")
def test_finding_sequence_only_advances_for_accepted_candidates() -> None:
    bundle = _bundle()
    candidates = (_candidate(file="src/other.py"), _candidate(), _candidate(primary_entity="order"))

    outcome = ReviewManager().validate_bundle(bundle, candidates, bundle.diff_text, EMPTY_SNAPSHOT)

    assert [str(item.finding_id) for item in outcome.findings] == [
        f"{bundle.bundle_id}-F001",
        f"{bundle.bundle_id}-F002",
    ]


def _bundle() -> ReviewBundle:
    diff = (
        "diff --git a/src/example.py b/src/example.py\n"
        "--- a/src/example.py\n"
        "+++ b/src/example.py\n"
        "@@ -1 +1,2 @@\n"
        " value = 1\n"
        "+unsafe = user.name\n"
    )
    return _bundle_from_diff(diff)


def _bundle_from_diff(diff: str) -> ReviewBundle:
    """テスト用差分を単一レビュー単位へ変換する."""
    files = UnifiedDiffParser().parse(diff).files
    return SimpleBundler(max_diff_tokens=8000).bundle(RUN_ID, files).bundles[0]


def _candidate(
    *,
    file: str = "src/example.py",
    line_range: LineRange | None = None,
    issue_type: str = "null-dereference",
    rule_id: str = "GENERAL.NULL_DEREFERENCE",
    primary_entity: str = "user",
    quote: str = "user.name",
    severity: Severity = Severity.HIGH,
    needs_context: bool = False,
) -> CandidateFinding:
    return CandidateFinding(
        file=NormalizedPath(file),
        line_range=line_range or LineRange(2, 2),
        category="correctness",
        issue_type=issue_type,
        rule_id=rule_id,
        primary_entity=primary_entity,
        severity=severity,
        title="Guard nullable user",
        description="The value can be None.",
        evidence=Evidence(quote=quote, reason="None reaches this line."),
        suggestion="Return before dereferencing user.",
        needs_context=needs_context,
    )


@pytest.mark.case("UT-M1-010")
def test_well_formed_but_unregistered_rule_id_is_rejected() -> None:
    bundle = _bundle()
    candidate = _candidate(rule_id="SECURITY.INVENTED_RULE")

    outcome = ReviewManager().validate_bundle(
        bundle, (candidate,), bundle.diff_text, EMPTY_SNAPSHOT
    )

    assert outcome.findings == ()
    assert outcome.rejected[0].reason_code == "UNKNOWN_RULE_ID"


@pytest.mark.case("UT-M1-010")
def test_project_rule_must_have_been_rendered() -> None:
    bundle = _bundle()
    candidate = _candidate(rule_id=PROJECT_RULE_ID)

    outcome = ReviewManager().validate_bundle(
        bundle, (candidate,), bundle.diff_text, PROJECT_SNAPSHOT
    )

    assert outcome.findings == ()
    assert outcome.rejected[0].reason_code == "RULE_NOT_SENT"


@pytest.mark.case("UT-M1-010")
def test_project_rule_must_apply_to_finding_file() -> None:
    bundle = _bundle()
    candidate = _candidate(rule_id=PROJECT_RULE_ID)

    outcome = ReviewManager().validate_bundle(
        bundle,
        (candidate,),
        bundle.diff_text,
        PROJECT_SNAPSHOT,
        (PROJECT_RULE_ID,),
        (FileRuleBinding(candidate.file, ()),),
    )

    assert outcome.findings == ()
    assert outcome.rejected[0].reason_code == "RULE_NOT_APPLICABLE_TO_FILE"


@pytest.mark.case("UT-M1-010")
def test_sent_project_rule_for_finding_file_is_accepted() -> None:
    bundle = _bundle()
    candidate = _candidate(rule_id=PROJECT_RULE_ID)

    outcome = ReviewManager().validate_bundle(
        bundle,
        (candidate,),
        bundle.diff_text,
        PROJECT_SNAPSHOT,
        (PROJECT_RULE_ID,),
        (FileRuleBinding(candidate.file, (PROJECT_RULE_ID,)),),
    )

    assert len(outcome.findings) == 1
    assert outcome.rejected == ()


@pytest.mark.case("UT-M1-010")
def test_general_rule_id_is_derived_from_issue_type() -> None:
    assert build_general_rule_id("null-dereference") == "GENERAL.NULL_DEREFERENCE"
    assert build_general_rule_id("---") == "GENERAL.ISSUE"
