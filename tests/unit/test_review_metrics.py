from __future__ import annotations

from decimal import Decimal

import pytest

from self_improving_code_review.application.review.models import (
    ReviewBundle,
    ReviewMetrics,
)
from self_improving_code_review.application.review.review_source_mapping import map_secret_findings
from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.common.enums import RiskLevel
from self_improving_code_review.domain.common.values import BundleId, RunId
from self_improving_code_review.security.models import SecretFinding


@pytest.mark.parametrize(
    ("candidate_count", "main_finding_count", "expected_rate"),
    [
        (0, 0, 0.0),
        (4, 1, 0.25),
        (2, 2, 1.0),
    ],
)
def test_validation_pass_rate_is_derived_from_candidate_count(
    candidate_count: int,
    main_finding_count: int,
    expected_rate: float,
) -> None:
    metrics = ReviewMetrics(
        input_tokens=100,
        output_tokens=20,
        latency_ms=5,
        cost_usd=Decimal("0.01"),
        candidate_count=candidate_count,
        main_finding_count=main_finding_count,
        reference_finding_count=0,
        invalid_position_count=0,
        invalid_evidence_count=0,
        duplicate_count=0,
        bundle_count=1,
        failed_bundle_count=0,
    )

    assert metrics.validation_pass_rate == expected_rate


def test_candidate_acceptance_counts_are_derived_from_rejection_reasons() -> None:
    metrics = ReviewMetrics(
        input_tokens=100,
        output_tokens=20,
        latency_ms=5,
        cost_usd=Decimal("0.01"),
        candidate_count=5,
        main_finding_count=1,
        reference_finding_count=0,
        invalid_position_count=0,
        invalid_evidence_count=0,
        duplicate_count=0,
        bundle_count=1,
        failed_bundle_count=0,
        rejection_reason_counts=(("INVALID_PATH", 1), ("UNKNOWN_RULE_ID", 2)),
    )

    assert metrics.rejected_candidate_count == 3
    assert metrics.accepted_candidate_count == 2


def test_secret_mapping_checks_later_files_and_removes_duplicates() -> None:
    files = UnifiedDiffParser().parse(_diff("src/first.py") + _diff("src/second.py")).files
    bundle = ReviewBundle(
        bundle_id=BundleId(
            run_id=RunId("12345678-1234-4234-9234-123456789abc"),
            sequence=1,
        ),
        files=files,
        risk=RiskLevel.MEDIUM,
        diff_text="",
        diff_tokens=0,
    )
    second_added_line = len(files[0].to_diff_text().splitlines()) + 4
    duplicate = SecretFinding(
        detector="TestSecret",
        path="bundle-diff",
        line_number=second_added_line,
    )
    unmatched = SecretFinding(
        detector="OtherSecret",
        path="bundle-diff",
        line_number=999,
    )

    mapped = map_secret_findings(bundle, (duplicate, duplicate, unmatched))

    assert len(mapped) == 2
    assert mapped[0].path == "src/second.py"
    assert mapped[0].line_number == 2
    assert mapped[0].line_side == "new"
    assert mapped[1] == unmatched


def _diff(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1,2 @@\n"
        " value = 1\n"
        "+added = 2\n"
    )
