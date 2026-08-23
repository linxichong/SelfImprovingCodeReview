#!/usr/bin/env python3
"""Evaluation metrics script. §22"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EvaluationMetrics:
    """Metrics for a single evaluation run."""
    case_id: str
    arm: str  # A, B, or C

    # Quality metrics
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    duplicates: int = 0

    # Cost metrics
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None

    # Derived metrics
    precision: float = 0.0
    recall: float = 0.0
    false_positive_rate: float = 0.0

    @property
    def total_findings(self) -> int:
        return self.true_positives + self.false_positives

    def compute_derived_metrics(self) -> None:
        """Compute precision, recall, FPR from base counts."""
        if self.total_findings > 0:
            self.precision = self.true_positives / self.total_findings
        else:
            self.precision = 0.0

        relevant = self.true_positives + self.false_negatives
        if relevant > 0:
            self.recall = self.true_positives / relevant
        else:
            self.recall = 0.0

        # FPR = FP / (FP + TN), but we don't have TN in this context
        # Using a simpler version: FP / total_findings
        if self.total_findings > 0:
            self.false_positive_rate = self.false_positives / self.total_findings
        else:
            self.false_positive_rate = 0.0


def load_expected(case_id: str) -> dict[str, Any]:
    """Load expected findings for a case."""
    expected_path = Path("eval/expected") / f"{case_id}.json"
    if not expected_path.exists():
        return {"expected_findings": [], "expected_no_issue": []}
    return json.loads(expected_path.read_text())


def load_actual(case_id: str, arm: str) -> dict[str, Any]:
    """Load actual review output for a case and arm."""
    # Normalize case_id
    if not case_id.endswith(".diff"):
        case_id = case_id + ".diff"

    # Arm to directory mapping per §20.2
    arm_dir = {
        "A": "baseline",
        "B": "rules",
        "C": "self-improving",
    }.get(arm, arm.lower())

    # Try different paths
    paths = [
        Path(f"eval/{arm_dir}/{case_id}"),
        Path(f"eval/{arm_dir}/{case_id}.json"),
    ]
    for path in paths:
        if path.exists():
            return json.loads(path.read_text())
    raise FileNotFoundError(f"Could not find output for {case_id} arm {arm}")


def compute_metrics(case_id: str, arm: str) -> EvaluationMetrics:
    """Compute metrics for a single case and arm."""
    expected = load_expected(case_id)
    actual = load_actual(case_id, arm)

    metrics = EvaluationMetrics(case_id=case_id, arm=arm)

    # Extract expected findings
    expected_findings = {
        f["title"]: f for f in expected.get("expected_findings", [])
        if f.get("should_find", False)
    }

    # Extract actual findings (use fingerprint if available, else title)
    actual_findings = actual.get("reviews", [])
    actual_by_key = {}
    for f in actual_findings:
        key = f.get("fingerprint") or f.get("title")
        actual_by_key[key] = f

    # Count TP/FP/FN
    matched_actual = set()
    for exp_title, exp_data in expected_findings.items():
        # Try to find matching actual finding
        found = False
        for act_key, act_data in actual_by_key.items():
            if exp_title.lower() in act_data.get("title", "").lower():
                found = True
                matched_actual.add(act_key)
                metrics.true_positives += 1
                break
        if not found:
            metrics.false_negatives += 1

    # Remaining actual findings are FP
    for act_key in actual_by_key:
        if act_key not in matched_actual:
            metrics.false_positives += 1

    # Extract cost metrics from metadata
    metadata = actual.get("metadata", {})
    metrics.input_tokens = metadata.get("input_tokens")
    metrics.output_tokens = metadata.get("output_tokens")
    metrics.latency_ms = metadata.get("review_time_ms")

    metrics.compute_derived_metrics()
    return metrics


def compare_arms(case_id: str) -> dict[str, Any]:
    """Compare arms A, B, C for a case."""
    results = {}
    for arm in ["A", "B", "C"]:
        try:
            results[arm] = compute_metrics(case_id, arm)
        except FileNotFoundError:
            results[arm] = None

    # Compute deltas
    comparison = {
        "case_id": case_id,
        "arms": {},
    }

    for arm, metrics in results.items():
        if metrics:
            comparison["arms"][arm] = {
                "precision": metrics.precision,
                "recall": metrics.recall,
                "fpr": metrics.false_positive_rate,
                "tp": metrics.true_positives,
                "fp": metrics.false_positives,
                "fn": metrics.false_negatives,
                "latency_ms": metrics.latency_ms,
                "input_tokens": metrics.input_tokens,
                "output_tokens": metrics.output_tokens,
            }

    # Compute deltas (C - B, B - A)
    if results.get("A") and results.get("B"):
        a = results["A"]
        b = results["B"]
        comparison["arms"]["B_minus_A"] = {
            "precision_delta": b.precision - a.precision,
            "recall_delta": b.recall - a.recall,
        }

    if results.get("B") and results.get("C"):
        b = results["B"]
        c = results["C"]
        comparison["arms"]["C_minus_B"] = {
            "precision_delta": c.precision - b.precision,
            "recall_delta": c.recall - b.recall,
        }

    return comparison


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        # Default: process all cases in split.json
        split = json.loads(Path("eval/split.json").read_text())
        cases = split.get("dev", []) + split.get("test", [])
    else:
        cases = argv

    all_results = []
    for case_id in cases:
        case_id = case_id.replace(".diff", "")  # Normalize
        try:
            result = compare_arms(case_id)
            all_results.append(result)
            print(f"Processed {case_id}")
        except Exception as e:
            print(f"Error processing {case_id}: {e}", file=sys.stderr)

    # Print summary
    print("\n=== Summary ===")
    for result in all_results:
        print(f"\nCase: {result['case_id']}")
        for arm, data in result.get("arms", {}).items():
            if isinstance(data, dict) and "precision" in data:
                print(f"  {arm}: P={data['precision']:.2%} R={data['recall']:.2%} "
                      f"TP={data['tp']} FP={data['fp']} FN={data['fn']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())