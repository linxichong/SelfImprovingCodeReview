from __future__ import annotations

import importlib
import re
from argparse import Namespace
from pathlib import Path

import pytest

from self_improving_code_review.application.config.settings import load_config
from self_improving_code_review.application.ports.diff_provider import (
    DiffSource,
    RawDiff,
)
from self_improving_code_review.application.review.rule_diagnostics import RuleDiagnosticsUseCase
from self_improving_code_review.cli.rule_commands import rule_check_to_document
from self_improving_code_review.domain.common.enums import DiffSourceKind
from self_improving_code_review.domain.common.errors import InvalidInputError, PolicyViolationError
from self_improving_code_review.domain.common.values import NormalizedPath
from self_improving_code_review.messages import Messages


class _DiffProvider:
    def __init__(self, text: str) -> None:
        self._text = text

    def acquire(self, repository_path: Path, source: DiffSource) -> RawDiff:
        assert repository_path.is_absolute()
        assert source.kind is DiffSourceKind.WORKSPACE
        return RawDiff(text=self._text, source_revision="abc123")

    def resolve_head_sha(self, repository_path: Path) -> str | None:
        del repository_path
        return "abc123"


@pytest.mark.case("UT-M1-016")
def test_rule_diagnostics_explains_matches_failures_and_budget(tmp_path: Path) -> None:
    rules = tmp_path / "config" / "rules"
    rules.mkdir(parents=True)
    (rules / "critical.yaml").write_text(
        """id: SECURITY.SUBPROCESS
category: security
severity: critical
scope:
  languages: [python]
  paths: [src/*.py]
  keywords: [subprocess]
instruction: Check subprocess input.
""",
        encoding="utf-8",
    )
    (rules / "high.yaml").write_text(
        """id: PYTHON.ERROR_HANDLING
category: correctness
severity: high
scope:
  paths: [src/*.py]
instruction: Check error handling.
""",
        encoding="utf-8",
    )
    (rules / "other.yaml").write_text(
        """id: TEST.ASSERTION
category: test
severity: medium
scope:
  paths: [tests/*.py]
  keywords: [assert]
instruction: Check assertions.
""",
        encoding="utf-8",
    )
    config = load_config(None, environ={})
    config = config.model_copy(
        update={"review": config.review.model_copy(update={"rule_tokens": 1})}
    )
    result = RuleDiagnosticsUseCase(
        config=config,
        diff_provider=_DiffProvider(_diff("src/app.py", "subprocess.run(command)")),
    ).execute(
        tmp_path,
        DiffSource(kind=DiffSourceKind.WORKSPACE),
    )

    document = rule_check_to_document(result)

    assert document["source_revision"] == "abc123"
    assert document["rule_snapshot"]["rule_count"] == 3
    file_document = document["files"][0]
    assert file_document["file"] == "src/app.py"
    assert file_document["risk"] == "high"
    assert file_document["matched_rules"][0]["matched_by"] == {
        "languages": ["python"],
        "paths": ["src/*.py"],
        "keywords": ["subprocess"],
    }
    assert file_document["matched_rules"][0]["selected_for_prompt"] is True
    assert file_document["matched_rules"][1]["exclusion_reason"] == ("RULE_TOKEN_BUDGET_EXCEEDED")
    assert file_document["unmatched_rules"][0]["failed_scope_groups"] == [
        "paths",
        "keywords",
    ]
    assert document["filtered_files"] == []


@pytest.mark.case("UT-M1-016")
def test_rule_diagnostics_rejects_unknown_target_and_outside_rules_path(
    tmp_path: Path,
) -> None:
    source = DiffSource(kind=DiffSourceKind.WORKSPACE)
    provider = _DiffProvider(_diff("src/app.py", "value = 2"))
    config = load_config(None, environ={})
    use_case = RuleDiagnosticsUseCase(config=config, diff_provider=provider)

    selected = use_case.execute(
        tmp_path,
        source,
        NormalizedPath("src/app.py"),
    )
    assert selected.files[0].path.value == "src/app.py"

    with pytest.raises(InvalidInputError, match="対象ファイルが見つかりません"):
        use_case.execute(tmp_path, source, NormalizedPath("src/missing.py"))

    outside = config.model_copy(
        update={"review": config.review.model_copy(update={"rules_directory": Path("../outside")})}
    )
    with pytest.raises(
        PolicyViolationError, match=re.escape(Messages.RULES_DIRECTORY_OUTSIDE_REPOSITORY)
    ):
        RuleDiagnosticsUseCase(config=outside, diff_provider=provider).execute(tmp_path, source)


@pytest.mark.case("UT-M1-016")
def test_rule_diagnostics_reports_filtered_file(tmp_path: Path) -> None:
    result = RuleDiagnosticsUseCase(
        config=load_config(None, environ={}),
        diff_provider=_DiffProvider(_diff("uv.lock", "package = 2")),
    ).execute(tmp_path, DiffSource(kind=DiffSourceKind.WORKSPACE))

    document = rule_check_to_document(result)

    assert document["files"] == []
    assert document["filtered_files"] == [
        {"file": "uv.lock", "decision": "filter", "reason": "lock-file"}
    ]

    blocked = RuleDiagnosticsUseCase(
        config=load_config(None, environ={}),
        diff_provider=_DiffProvider(_diff(".env", "TOKEN=value")),
    ).execute(tmp_path, DiffSource(kind=DiffSourceKind.WORKSPACE))
    assert blocked.filtered_files[0].reason is not None
    assert blocked.filtered_files[0].reason.value == "excluded"


@pytest.mark.case("UT-M1-016")
def test_rules_check_command_wires_dependencies_and_writes_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("self_improving_code_review.cli.rule_commands")
    documents: list[dict[str, object]] = []
    monkeypatch.setattr(
        module,
        "GitDiffAdapter",
        lambda: _DiffProvider(_diff("src/app.py", "value = 2")),
    )
    monkeypatch.setattr(
        module,
        "write_document",
        lambda document, _output: documents.append(document),
    )
    arguments = Namespace(
        repository=str(tmp_path),
        config=None,
        file=None,
        output=None,
        git_diff=True,
        range_revisions=None,
        commit=None,
        diff_file=None,
    )

    assert module.execute_rules_check_command(arguments) == 0
    assert documents[0]["schema_version"] == 1


def _diff(path: str, added: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1,2 @@\n"
        " value = 1\n"
        f"+{added}\n"
    )
