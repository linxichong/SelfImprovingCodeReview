from __future__ import annotations

import re
from pathlib import Path

import pytest

from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.common.enums import Severity
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.rule.loader import RuleLoader
from self_improving_code_review.domain.rule.matcher import RuleMatcher
from self_improving_code_review.domain.rule.models import (
    ReviewRule,
    RuleScope,
    RuleSnapshotItem,
    compute_rule_set_hash,
    is_valid_rule_id,
)
from self_improving_code_review.messages import Messages

_VALID_RULE = """id: PYTHON.NULL_DEREFERENCE
category: correctness
severity: high
scope:
  paths: [src/*.py]
instruction: Check the nullable value.
"""


@pytest.mark.case("UT-M1-007")
def test_missing_rules_directory_produces_empty_snapshot() -> None:
    snapshot = RuleLoader().load(Path("does-not-exist"), git_sha=None)

    assert snapshot.is_empty
    assert snapshot.rules == ()
    assert snapshot.rule_set_hash == compute_rule_set_hash(())
    assert snapshot.snapshot_id == snapshot.rule_set_hash[:32]


@pytest.mark.case("UT-M1-007")
def test_nested_rule_files_are_loaded_in_path_order(tmp_path: Path) -> None:
    rules = tmp_path / "rules"
    (rules / "security").mkdir(parents=True)
    (rules / "a.yaml").write_text(_VALID_RULE, encoding="utf-8")
    (rules / "security" / "b.yaml").write_text(
        _VALID_RULE.replace("PYTHON.NULL_DEREFERENCE", "SECURITY.SQL_INJECTION"),
        encoding="utf-8",
    )

    snapshot = RuleLoader().load(rules, git_sha="abc")

    assert {item.path for item in snapshot.items} == {"a.yaml", "security/b.yaml"}
    assert snapshot.contains_rule("SECURITY.SQL_INJECTION")
    assert not snapshot.contains_rule("PYTHON.UNKNOWN")
    assert not snapshot.is_empty


@pytest.mark.case("UT-M1-007")
def test_duplicate_rule_id_across_files_is_rejected(tmp_path: Path) -> None:
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "a.yaml").write_text(_VALID_RULE, encoding="utf-8")
    (rules / "b.yaml").write_text(_VALID_RULE, encoding="utf-8")

    with pytest.raises(InvalidInputError, match=re.escape(Messages.RULE_ID_DUPLICATED)):
        RuleLoader().load(rules, git_sha=None)


@pytest.mark.case("UT-M1-007")
def test_unreadable_rule_file_is_rejected(tmp_path: Path) -> None:
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "broken.yaml").write_bytes(b"\xff\xfe\x00")

    with pytest.raises(InvalidInputError, match=re.escape(Messages.RULE_FILE_READ_FAILED)):
        RuleLoader().load(rules, git_sha=None)


@pytest.mark.case("UT-M1-007")
@pytest.mark.parametrize(
    ("content", "expected_message"),
    [
        ("id: [unterminated\n", Messages.RULE_FILE_YAML_INVALID),
        ("- not-a-mapping\n", Messages.RULE_FILE_NOT_MAPPING),
        ("id: PYTHON.X\n", Messages.RULE_FILE_FIELDS_MISSING),
        (_VALID_RULE + "extra: true\n", Messages.RULE_FILE_FIELDS_UNKNOWN),
        (
            _VALID_RULE.replace("id: PYTHON.NULL_DEREFERENCE", "id: '   '"),
            Messages.RULE_FILE_FIELD_NOT_STRING,
        ),
        (
            _VALID_RULE.replace("category: correctness", "category: 5"),
            Messages.RULE_FILE_FIELD_NOT_STRING,
        ),
        (
            _VALID_RULE.replace("severity: high", "severity: 5"),
            Messages.RULE_FILE_SEVERITY_NOT_STRING,
        ),
        (
            _VALID_RULE.replace("severity: high", "severity: urgent"),
            Messages.RULE_FILE_SEVERITY_INVALID,
        ),
        (
            _VALID_RULE.replace("scope:\n  paths: [src/*.py]", "scope: []"),
            Messages.RULE_FILE_SCOPE_NOT_MAPPING,
        ),
        (
            _VALID_RULE.replace("  paths: [src/*.py]", "  unknown: [x]"),
            Messages.RULE_FILE_SCOPE_FIELDS_UNKNOWN,
        ),
        (
            _VALID_RULE.replace("  paths: [src/*.py]", "  paths: src/*.py"),
            Messages.RULE_FILE_INVALID,
        ),
        (_VALID_RULE.replace("  paths: [src/*.py]", "  paths: ['  ']"), Messages.RULE_FILE_INVALID),
        (_VALID_RULE.replace("  paths: [src/*.py]", "  paths: []"), Messages.RULE_FILE_INVALID),
    ],
)
def test_invalid_rule_files_are_rejected(
    tmp_path: Path,
    content: str,
    expected_message: str,
) -> None:
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "rule.yaml").write_text(content, encoding="utf-8")

    with pytest.raises(InvalidInputError, match=re.escape(expected_message)):
        RuleLoader().load(rules, git_sha=None)


@pytest.mark.case("UT-M1-007")
@pytest.mark.parametrize(
    "rule_id",
    ["python.lower", "PYTHON", "PYTHON.", ".NULL", "PYTHON..NULL", ""],
)
def test_rule_id_convention_is_enforced(rule_id: str) -> None:
    assert not is_valid_rule_id(rule_id)

    with pytest.raises(InvalidInputError, match=re.escape(Messages.RULE_ID_FORMAT_INVALID)):
        _rule(rule_id=rule_id)


@pytest.mark.case("UT-M1-007")
def test_general_rule_namespace_is_reserved() -> None:
    with pytest.raises(InvalidInputError, match=re.escape(Messages.RULE_ID_GENERAL_RESERVED)):
        _rule(rule_id="GENERAL.NULL_DEREFERENCE")


@pytest.mark.case("UT-M1-007")
@pytest.mark.parametrize(
    ("category", "instruction", "expected_message"),
    [
        ("   ", "Check it.", Messages.RULE_CATEGORY_REQUIRED),
        ("correctness", "   ", Messages.RULE_INSTRUCTION_REQUIRED),
    ],
)
def test_rule_requires_category_and_instruction(
    category: str,
    instruction: str,
    expected_message: str,
) -> None:
    with pytest.raises(InvalidInputError, match=re.escape(expected_message)):
        _rule(category=category, instruction=instruction)


@pytest.mark.case("UT-M1-007")
def test_rule_scope_requires_at_least_one_group() -> None:
    with pytest.raises(InvalidInputError, match=re.escape(Messages.RULE_SCOPE_GROUP_REQUIRED)):
        RuleScope()


@pytest.mark.case("UT-M1-007")
def test_rule_set_hash_is_order_independent_but_content_sensitive() -> None:
    first = RuleSnapshotItem(rule_id="A.B", path="a.yaml", content_hash="1")
    second = RuleSnapshotItem(rule_id="C.D", path="b.yaml", content_hash="2")

    assert compute_rule_set_hash((first, second)) == compute_rule_set_hash((second, first))
    assert compute_rule_set_hash((first,)) != compute_rule_set_hash((second,))


@pytest.mark.case("UT-M1-007")
def test_language_scope_excludes_unknown_language_files() -> None:
    rule = _rule(scope=RuleScope(languages=("python",)))
    unknown = UnifiedDiffParser().parse(_diff("data/values.unknownext")).files[0]
    python = UnifiedDiffParser().parse(_diff("src/app.py")).files[0]

    assert not RuleMatcher().matches(rule, unknown)
    assert RuleMatcher().matches(rule, python)


@pytest.mark.case("UT-M1-007")
def test_keyword_scope_is_case_sensitive_and_ignores_context_lines() -> None:
    rule = _rule(scope=RuleScope(keywords=("subprocess",)))
    added = UnifiedDiffParser().parse(_diff("src/app.py", added="subprocess.run(x)")).files[0]
    uppercase = UnifiedDiffParser().parse(_diff("src/app.py", added="SUBPROCESS.run(x)")).files[0]
    context_only = UnifiedDiffParser().parse(_context_diff("src/app.py")).files[0]

    assert RuleMatcher().matches(rule, added)
    assert not RuleMatcher().matches(rule, uppercase)
    assert not RuleMatcher().matches(rule, context_only)


@pytest.mark.case("UT-M1-007")
def test_select_returns_rules_matching_any_file() -> None:
    python_rule = _rule(rule_id="PYTHON.A", scope=RuleScope(paths=("src/*.py",)))
    other_rule = _rule(rule_id="OTHER.B", scope=RuleScope(paths=("lib/*.py",)))
    changed_file = UnifiedDiffParser().parse(_diff("src/app.py")).files[0]

    selected = RuleMatcher().select((python_rule, other_rule), (changed_file,))

    assert [rule.id for rule in selected] == ["PYTHON.A"]


@pytest.mark.case("UT-M1-007")
def test_resolve_preserves_rule_bindings_for_each_file() -> None:
    source_rule = _rule(rule_id="PYTHON.SOURCE", scope=RuleScope(paths=("src/*.py",)))
    test_rule = _rule(rule_id="PYTHON.TEST", scope=RuleScope(paths=("tests/*.py",)))
    changed_files = (
        UnifiedDiffParser().parse(_diff("src/app.py") + _diff("tests/test_app.py")).files
    )

    resolution = RuleMatcher().resolve((source_rule, test_rule), changed_files)

    assert [rule.id for rule in resolution.rules] == ["PYTHON.SOURCE", "PYTHON.TEST"]
    assert [(binding.path.value, binding.rule_ids) for binding in resolution.file_bindings] == [
        ("src/app.py", ("PYTHON.SOURCE",)),
        ("tests/test_app.py", ("PYTHON.TEST",)),
    ]


def _rule(
    *,
    rule_id: str = "PYTHON.NULL_DEREFERENCE",
    category: str = "correctness",
    instruction: str = "Check the nullable value.",
    scope: RuleScope | None = None,
) -> ReviewRule:
    return ReviewRule(
        id=rule_id,
        category=category,
        severity=Severity.HIGH,
        scope=scope or RuleScope(paths=("src/*.py",)),
        instruction=instruction,
        source_path="rule.yaml",
        content_hash="hash",
    )


def _diff(path: str, *, added: str = "value = 2") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1,2 @@\n"
        " value = 1\n"
        f"+{added}\n"
    )


def _context_diff(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,2 +1,2 @@\n"
        " subprocess.run(x)\n"
        "-value = 1\n"
        "+value = 2\n"
    )
