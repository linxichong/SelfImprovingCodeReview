"""固定済み版8向けの独立した未承認受入データと確認資料を生成する."""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import yaml
from prepare_test_v3_candidate import _commit, _git
from pydantic import BaseModel, ConfigDict, Field

from self_improving_code_review.domain.common.enums import Severity
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.domain.common.values import NormalizedPath, ProjectId
from self_improving_code_review.messages import Messages

_ROOT = Path(__file__).resolve().parents[2]
_CATALOG = Path(__file__).parent / "independent_v8/catalog.json"


class IssueSeed(BaseModel):
    """決定的な位置算出と人手確認に使う未承認の問題定義."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    sequence: int | None = Field(default=None, ge=1)
    symbol: str = Field(min_length=1)
    marker: str = Field(min_length=1)
    occurrence: int = Field(default=1, ge=1)
    issue_type: str = Field(min_length=1)
    severity: Severity
    evidence: str = Field(min_length=1)


class ChangeSeed(BaseModel):
    """一変更の独立したコードと問題候補を保持する."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str = Field(min_length=1)
    source: str = Field(min_length=1)
    issues: tuple[IssueSeed, ...]


class Catalog(BaseModel):
    """追加フィールドを拒否する独立候補カタログ."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = Field(ge=1, le=1)
    cases: tuple[ChangeSeed, ...] = Field(min_length=1)


def main(*, version: int = 8, catalog_path: Path = _CATALOG) -> int:
    """既存先を上書きせず、未承認の集合・履歴・承認用資料を生成する."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--memory-database", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    arguments = parser.parse_args()
    repository = arguments.repository.resolve()
    if repository.exists():
        sys.stderr.write(f"{Messages.EVALUATION_CANDIDATE_TARGET_EXISTS}: {repository}\n")
        return 1
    catalog = Catalog.model_validate_json(catalog_path.read_text(encoding="utf-8"))
    _validate_sources(catalog)
    memory_database = arguments.memory_database.resolve(strict=True)
    repository.mkdir(parents=True)
    _git(repository, "init", "--quiet", "--initial-branch=main")
    _git(repository, "config", "user.name", "Independent Acceptance Preparer")
    _git(repository, "config", "user.email", "preparer@example.invalid")
    shutil.copytree(_ROOT / "config/rules", repository / "config/rules")
    (repository / "prompts").mkdir()
    for prompt in (_ROOT / "prompts").glob("*.yaml"):
        shutil.copyfile(prompt, repository / "prompts" / prompt.name)
    selected_prompt = _ROOT / "prompts/candidates/v8/review_general_v1.yaml"
    shutil.copyfile(selected_prompt, repository / "prompts/review_general_v1.yaml")
    config = yaml.safe_load((_ROOT / "config/evaluation.example.yaml").read_text(encoding="utf-8"))
    config["project_id"] = str(ProjectId(arguments.project_id))
    config["database"] = {"path": ".review/evaluation.db"}
    (repository / "evaluation.yaml").write_text(yaml.safe_dump(config, allow_unicode=True))
    (repository / ".gitignore").write_text(".review/\n", encoding="utf-8")
    _git(repository, "add", ".")
    _commit(repository, "独立受入基盤と候補版8を固定", 0)
    (repository / ".review").mkdir()
    with (
        closing(sqlite3.connect(f"file:{memory_database}?mode=ro", uri=True)) as source,
        closing(sqlite3.connect(repository / ".review/evaluation.db")) as destination,
        source,
        destination,
    ):
        source.backup(destination)
    document, packet = _build_document(repository, catalog, version=version)
    output = repository / f"datasets/evaluation/ground-truth-test-v{version}.candidate.json"
    output.parent.mkdir(parents=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (repository / "review-packet.md").write_text(packet, encoding="utf-8")
    manifest = {
        "publication_ready": False,
        "dataset_version": version,
        "case_count": len(catalog.cases),
        "issue_count": sum(len(case.issues) for case in catalog.cases),
        "prompt_version": 8,
        "prompt_hash": hash_text(selected_prompt.read_text(encoding="utf-8")),
        "catalog_hash": hash_text(catalog_path.read_text(encoding="utf-8")),
        "candidate_hash": hash_text(output.read_text(encoding="utf-8")),
        "memory_database_source": str(memory_database),
    }
    if version != 8:
        manifest["is_unseen_holdout"] = False
    (repository / "preparation-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    sys.stdout.write(json.dumps(manifest) + "\n")
    return 0


def _validate_sources(catalog: Catalog) -> None:
    """書込先とコード構文を検証し、変更パスの重複を拒否する."""
    paths = tuple(NormalizedPath(case.path).value for case in catalog.cases)
    assert len(set(paths)) == len(paths), "duplicate catalog paths"
    assert all(path.startswith("src/") for path in paths), "catalog paths must be sources"
    for case in catalog.cases:
        ast.parse(case.source)
    sequences = [
        seed.sequence if seed.sequence is not None else index
        for index, seed in enumerate((seed for case in catalog.cases for seed in case.issues), 1)
    ]
    if len(set(sequences)) != len(sequences):
        raise InvalidInputError(Messages.DUPLICATE_GROUND_TRUTH_SEQUENCE)
    if sorted(sequences) != list(range(1, len(sequences) + 1)):
        raise InvalidInputError(Messages.GROUND_TRUTH_ISSUE_SEQUENCE_INVALID)


def _build_document(
    repository: Path, catalog: Catalog, *, version: int = 8
) -> tuple[dict[str, object], str]:
    """各変更を別コミットにして、AST 内の原文から問題位置を算出する."""
    cases: list[dict[str, object]] = []
    issues: list[dict[str, object]] = []
    packet = [
        "# 独立受入 test-v8 確認資料" if version == 8 else f"# 受入候補 test-v{version} 確認資料",
        "",
        "全件未承認。差分・根拠・重大度・安全対照を確認してください。",
        "",
    ]
    if version != 8:
        packet.extend(
            [
                "既存 Test の裁定結果に基づく訂正版であり、未見の独立受入集合ではありません。",
                "提示詞とメモリの調整には使用せず、新版の全比較群を同じ条件で再実行してください。",
                "",
            ]
        )
    approval = {
        "created_by": "independent-benchmark-preparer",
        "verified_by": "independent-benchmark-preparer",
        "approved_by": "REVIEW_REQUIRED",
    }
    for sequence, case in enumerate(catalog.cases, 1):
        path = repository / case.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(case.source, encoding="utf-8")
        _git(repository, "add", case.path)
        _commit(repository, f"独立変更 {sequence}", sequence)
        revision = _git(repository, "rev-parse", "HEAD")
        cases.append({"sequence": sequence, "change_ref": revision, **approval})
        packet.extend(
            [
                f"## 変更 {sequence}: {case.path}",
                f"Commit: `{revision}`",
                "",
                "```python",
                case.source.rstrip(),
                "```",
                "",
            ]
        )
        functions = {
            node.name: node
            for node in ast.parse(case.source).body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        for seed in case.issues:
            sequence = seed.sequence if seed.sequence is not None else len(issues) + 1
            function = functions[seed.symbol]
            assert function.end_lineno is not None
            matches = [
                number
                for number, line in enumerate(case.source.splitlines(), 1)
                if function.lineno <= number <= function.end_lineno and seed.marker in line
            ]
            line = matches[seed.occurrence - 1]
            issue = {
                "sequence": sequence,
                "change_ref": revision,
                "file": case.path,
                "start_line": line,
                "symbol": seed.symbol,
                "issue_type": seed.issue_type,
                "severity": seed.severity.value,
                "evidence": seed.evidence,
                "origin": "independent-offline-reproduction",
                **approval,
            }
            issues.append(issue)
            packet.append(
                f"- I{sequence:04d}: `{seed.symbol}` L{line} / {seed.severity.value} / "
                f"{seed.issue_type}: {seed.evidence}"
            )
        if not case.issues:
            packet.append("- 安全対照候補。変更全体に問題がないことを独立に確認してください。")
        packet.append("")
    return {
        "schema_version": 2,
        "split": "test",
        "version": version,
        "cases": cases,
        "issues": sorted(issues, key=lambda issue: issue["sequence"]),
    }, "\n".join(packet)


if __name__ == "__main__":
    raise SystemExit(main())
