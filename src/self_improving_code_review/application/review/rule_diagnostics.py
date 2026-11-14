"""実レビューと同じ決定処理を使うルール適用診断."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from self_improving_code_review.application.config.settings import AppConfig
from self_improving_code_review.application.ports.diff_provider import DiffProvider, DiffSource
from self_improving_code_review.application.review.bundler import SimpleBundler
from self_improving_code_review.application.review.planner import RuleBasedReviewPlanner
from self_improving_code_review.application.review.rule_budget import select_rules_for_prompt
from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.change.file_filter import (
    FileFilter,
    FileFilterResult,
    FilterPolicy,
)
from self_improving_code_review.domain.change.models import ChangedFile
from self_improving_code_review.domain.common.enums import (
    FileDecision,
    FilterReason,
    ReviewMode,
)
from self_improving_code_review.domain.common.errors import InvalidInputError, PolicyViolationError
from self_improving_code_review.domain.common.values import NormalizedPath, RunId
from self_improving_code_review.domain.rule.loader import RuleLoader
from self_improving_code_review.domain.rule.matcher import RuleMatcher
from self_improving_code_review.domain.rule.models import ReviewRule, RuleMatchExplanation
from self_improving_code_review.messages import Messages
from self_improving_code_review.security.path_policy import PathPolicy

__all__ = [
    "FileRuleDiagnostic",
    "RuleCheckResult",
    "RuleDiagnostic",
    "RuleDiagnosticsUseCase",
]

_DIAGNOSTIC_RUN_ID = RunId("00000000-0000-4000-8000-000000000000")
_BUDGET_EXCLUSION = "RULE_TOKEN_BUDGET_EXCEEDED"


@dataclass(frozen=True, slots=True)
class RuleDiagnostic:
    """単一ルールのファイル照合および送信可否の診断."""

    rule_id: str
    category: str
    severity: str
    explanation: RuleMatchExplanation
    selected_for_prompt: bool
    exclusion_reason: str | None


@dataclass(frozen=True, slots=True)
class FileRuleDiagnostic:
    """レビュー単位内の単一ファイルに対するルール診断."""

    path: NormalizedPath
    bundle_id: str
    risk: str
    rule_token_budget: int
    selected_rule_tokens: int
    matched_rules: tuple[RuleDiagnostic, ...]
    unmatched_rules: tuple[RuleDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class RuleCheckResult:
    """差分全体のルール診断結果."""

    source_revision: str | None
    snapshot_id: str
    rule_set_hash: str
    rule_count: int
    files: tuple[FileRuleDiagnostic, ...]
    filtered_files: tuple[FileFilterResult, ...]


class RuleDiagnosticsUseCase:
    """LLM を呼ばずに実レビューのルール決定過程を説明する."""

    def __init__(self, *, config: AppConfig, diff_provider: DiffProvider) -> None:
        """設定と差分取得ポートを保持し、本番と同じ決定サービスを構成する."""
        self._config = config
        self._diff_provider = diff_provider
        self._parser = UnifiedDiffParser()
        self._filter = FileFilter(FilterPolicy(exclude_patterns=config.security.exclude_patterns))
        self._path_policy = PathPolicy(extra_patterns=config.security.blocked_paths)
        self._bundler = SimpleBundler(max_diff_tokens=config.review.bundle_diff_tokens)
        self._loader = RuleLoader()
        self._matcher = RuleMatcher()
        self._planner = RuleBasedReviewPlanner(config.review, self._matcher)

    def execute(
        self,
        repository_path: Path,
        diff_source: DiffSource,
        target_file: NormalizedPath | None = None,
    ) -> RuleCheckResult:
        """指定差分に対する命中根拠、非命中理由、予算除外を返す."""
        raw_diff = self._diff_provider.acquire(repository_path, diff_source)
        changeset = self._parser.parse(raw_diff.text)
        snapshot = self._loader.load(
            self._resolve_rules_root(repository_path),
            git_sha=raw_diff.source_revision,
        )
        files = changeset.files
        if target_file is not None:
            target = changeset.find(target_file)
            if target is None:
                raise InvalidInputError(Messages.file_not_found(target_file.value))
            files = (target,)

        decisions = tuple(self._decide_file(changed_file) for changed_file in files)
        reviewable_paths = {item.path.value for item in decisions if item.is_reviewable}
        reviewable_files = tuple(
            changed_file
            for changed_file in files
            if changed_file.new_path.value in reviewable_paths
        )
        bundling = self._bundler.bundle(_DIAGNOSTIC_RUN_ID, reviewable_files)
        filtered = tuple(item for item in decisions if not item.is_reviewable) + tuple(
            FileFilterResult(
                path=item.path,
                decision=FileDecision.FILTER,
                reason=FilterReason.BUDGET_EXCEEDED,
            )
            for item in bundling.oversized_files
        )
        diagnostics: list[FileRuleDiagnostic] = []
        for bundle in bundling.bundles:
            plan, applicable_rules = self._planner.plan(bundle, snapshot.rules, ReviewMode.RULES)
            selection = select_rules_for_prompt(applicable_rules, plan.budget.rule_tokens)
            selected_ids = {rule.id for rule in selection.selected}
            for changed_file in bundle.files:
                diagnostics.extend(
                    (
                        self._diagnose_file(
                            changed_file,
                            str(bundle.bundle_id),
                            bundle.risk.value,
                            plan.budget.rule_tokens,
                            selection.estimated_tokens,
                            selected_ids,
                            snapshot.rules,
                        ),
                    )
                )
        return RuleCheckResult(
            source_revision=raw_diff.source_revision,
            snapshot_id=snapshot.snapshot_id,
            rule_set_hash=snapshot.rule_set_hash,
            rule_count=len(snapshot.rules),
            files=tuple(diagnostics),
            filtered_files=filtered,
        )

    def _diagnose_file(
        self,
        changed_file: ChangedFile,
        bundle_id: str,
        risk: str,
        rule_token_budget: int,
        selected_rule_tokens: int,
        selected_ids: set[str],
        rules: tuple[ReviewRule, ...],
    ) -> FileRuleDiagnostic:
        """単一ファイルに対する全ルールの照合結果を構築する."""
        matched: list[RuleDiagnostic] = []
        unmatched: list[RuleDiagnostic] = []
        for rule in rules:
            explanation = self._matcher.explain(rule, changed_file)
            is_selected = rule.id in selected_ids
            item = RuleDiagnostic(
                rule_id=rule.id,
                category=rule.category,
                severity=rule.severity.value,
                explanation=explanation,
                selected_for_prompt=is_selected,
                exclusion_reason=(
                    _BUDGET_EXCLUSION if explanation.is_match and not is_selected else None
                ),
            )
            (matched if explanation.is_match else unmatched).append(item)
        return FileRuleDiagnostic(
            path=changed_file.new_path,
            bundle_id=bundle_id,
            risk=risk,
            rule_token_budget=rule_token_budget,
            selected_rule_tokens=selected_rule_tokens,
            matched_rules=tuple(matched),
            unmatched_rules=tuple(unmatched),
        )

    def _resolve_rules_root(self, repository_root: Path) -> Path:
        """リポジトリルート配下に制限したルールディレクトリを返す."""
        root = repository_root.resolve()
        candidate = (root / self._config.review.rules_directory).resolve()
        if not candidate.is_relative_to(root):
            raise PolicyViolationError(Messages.RULES_DIRECTORY_OUTSIDE_REPOSITORY)
        return candidate

    def _decide_file(self, changed_file: ChangedFile) -> FileFilterResult:
        """正式レビューと同じ順序でパス方針とファイル選別を適用する."""
        if self._path_policy.is_blocked(changed_file.new_path):
            return FileFilterResult(
                path=changed_file.new_path,
                decision=FileDecision.FILTER,
                reason=FilterReason.EXCLUDED,
            )
        return self._filter.decide(changed_file)
