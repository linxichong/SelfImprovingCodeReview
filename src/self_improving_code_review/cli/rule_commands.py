"""ルール診断コマンドの構成起点."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from self_improving_code_review.application.config.settings import load_config
from self_improving_code_review.application.review.rule_diagnostics import (
    RuleCheckResult,
    RuleDiagnostic,
    RuleDiagnosticsUseCase,
)
from self_improving_code_review.cli.output import write_document
from self_improving_code_review.cli.review_commands import _diff_source
from self_improving_code_review.domain.common.values import NormalizedPath
from self_improving_code_review.infrastructure.git import GitDiffAdapter

__all__ = ["execute_rules_check_command", "rule_check_to_document"]


def execute_rules_check_command(arguments: Namespace) -> int:
    """CLI 引数からルール診断を実行し JSON を出力する."""
    repository = Path(arguments.repository).resolve()
    config = load_config(Path(arguments.config) if arguments.config else None)
    result = RuleDiagnosticsUseCase(
        config=config,
        diff_provider=GitDiffAdapter(),
    ).execute(
        repository,
        _diff_source(arguments),
        NormalizedPath(arguments.file) if arguments.file else None,
    )
    write_document(
        rule_check_to_document(result),
        Path(arguments.output) if arguments.output else None,
    )
    return 0


def rule_check_to_document(result: RuleCheckResult) -> dict[str, Any]:
    """ルール診断結果の公開 JSON 文書を返す."""
    return {
        "schema_version": 1,
        "source_revision": result.source_revision,
        "rule_snapshot": {
            "snapshot_id": result.snapshot_id,
            "rule_set_hash": result.rule_set_hash,
            "rule_count": result.rule_count,
        },
        "files": [
            {
                "file": item.path.value,
                "bundle_id": item.bundle_id,
                "risk": item.risk,
                "rule_token_budget": item.rule_token_budget,
                "selected_rule_tokens": item.selected_rule_tokens,
                "matched_rules": [_rule_document(rule) for rule in item.matched_rules],
                "unmatched_rules": [_rule_document(rule) for rule in item.unmatched_rules],
            }
            for item in result.files
        ],
        "filtered_files": [
            {
                "file": item.path.value,
                "decision": item.decision.value,
                "reason": item.reason.value if item.reason else None,
            }
            for item in result.filtered_files
        ],
    }


def _rule_document(rule: RuleDiagnostic) -> dict[str, Any]:
    """単一ルールの公開診断表現を返す."""
    return {
        "rule_id": rule.rule_id,
        "category": rule.category,
        "severity": rule.severity,
        "matched_by": {
            "languages": list(rule.explanation.matched_languages),
            "paths": list(rule.explanation.matched_paths),
            "keywords": list(rule.explanation.matched_keywords),
        },
        "failed_scope_groups": list(rule.explanation.failed_scope_groups),
        "selected_for_prompt": rule.selected_for_prompt,
        "exclusion_reason": rule.exclusion_reason,
    }
