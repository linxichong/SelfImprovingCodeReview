"""HTTP 応答の異常経路も明示的に解放する未承認の v7 候補を生成する."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

from prepare_test_v3_candidate import _build_document, _commit, _git, _prepare_repository

from self_improving_code_review.messages import Messages

_ROOT = Path(__file__).resolve().parents[2]
_HTTP_FUNCTIONS = (
    ("fetch_catalog", "http_catalog_v7.py"),
    ("fetch_metadata", "http_metadata_v7.py"),
    ("fetch_version", "http_request_v7.py"),
    ("fetch_status", "http_status_v7.py"),
)


def main() -> int:
    """旧集合を書き換えず、全件が未承認の独立候補を作成する."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository", type=Path, default=_ROOT / ".review/formal-evaluation-v7-repository"
    )
    repository = parser.parse_args().repository.resolve()
    if repository.exists():
        sys.stderr.write(f"{Messages.EVALUATION_CANDIDATE_TARGET_EXISTS}: {repository}\n")
        return 1
    _prepare_repository(repository)
    config_path = repository / "evaluation.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "m2-formal-evaluation-v3", "m2-formal-evaluation-v7"
        ),
        encoding="utf-8",
    )
    (repository / "README.md").write_text(
        "# test-v7 評価候補\n\n全件の独立した検証と承認前は発行しないでください。\n",
        encoding="utf-8",
    )
    _git(repository, "add", "evaluation.yaml", "README.md")
    _commit(repository, "test-v7 評価基盤の設定", 0)
    document = _build_document(repository)
    document["version"] = 7
    _append_http_cases(repository, document)
    output = repository / "datasets/evaluation/ground-truth-test-v7.candidate.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(
        json.dumps(
            {
                "case_count": 32,
                "issue_count": 30,
                "critical_issue_count": 8,
                "publication_ready": False,
                "ground_truth_candidate": str(output),
            }
        )
        + "\n"
    )
    return 0


def _append_http_cases(repository: Path, document: dict[str, object]) -> None:
    """ネットワークなしで異常経路を検証済みの四つの対照例を追加する."""
    source = (_ROOT / "datasets/development/http_lifecycle_cases.py").read_text(encoding="utf-8")
    functions = {
        node.name: node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)
    }
    cases = document["cases"]
    assert isinstance(cases, list)
    for function_name, filename in _HTTP_FUNCTIONS:
        function_source = ast.get_source_segment(source, functions[function_name])
        assert function_source is not None
        imports = "Request, urlopen" if function_name == "fetch_version" else "urlopen"
        path = repository / "src/safe" / filename
        path.write_text(
            f"from urllib.request import {imports}\n\n\n{function_source}\n", encoding="utf-8"
        )
        _git(repository, "add", str(path.relative_to(repository)))
        sequence = len(cases) + 1
        _commit(repository, f"HTTP 対照例 {sequence} を追加", sequence)
        cases.append(
            {
                "sequence": sequence,
                "change_ref": _git(repository, "rev-parse", "HEAD"),
                "created_by": "benchmark-preparer",
                "verified_by": "benchmark-preparer",
                "approved_by": "REVIEW_REQUIRED",
            }
        )


if __name__ == "__main__":
    raise SystemExit(main())
