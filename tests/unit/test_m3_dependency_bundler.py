"""M3 の変更ファイル関係グラフと予算分割を検証する。"""

from __future__ import annotations

import pytest

from self_improving_code_review.application.review.dependency_bundler import (
    DependencyAwareBundler,
)
from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.common.enums import (
    BundleEdgeDecision,
    BundleRelation,
)
from self_improving_code_review.domain.common.values import RunId

RUN_ID = RunId("12345678-1234-4234-9234-123456789abc")


@pytest.mark.case("UT-M3-001")
def test_implementation_and_test_are_grouped_by_highest_priority_edge() -> None:
    files = (
        UnifiedDiffParser()
        .parse(
            _diff("src/user.py", "def load_user(): pass")
            + _diff("tests/test_user.py", "def test_load_user(): pass")
        )
        .files
    )

    result = DependencyAwareBundler(max_diff_tokens=8000).bundle(RUN_ID, files)

    assert result.bundles[0].paths == ("src/user.py", "tests/test_user.py")
    assert result.edges[0].relation is BundleRelation.IMPLEMENTATION_TEST
    assert result.edges[0].decision is BundleEdgeDecision.SELECTED


@pytest.mark.case("UT-M3-001")
def test_import_reference_is_detected_from_python_syntax_tree() -> None:
    files = (
        UnifiedDiffParser()
        .parse(
            _diff("src/service.py", "from src.user import User")
            + _diff("src/user.py", "class User: pass")
        )
        .files
    )

    result = DependencyAwareBundler(max_diff_tokens=8000).bundle(RUN_ID, files)

    assert len(result.bundles) == 1
    assert result.edges[0].relation is BundleRelation.IMPORT_REFERENCE


@pytest.mark.case("UT-M3-001")
def test_all_supported_python_import_forms_create_reference_edges() -> None:
    direct_import = DependencyAwareBundler(max_diff_tokens=8000).bundle(
        RUN_ID,
        UnifiedDiffParser()
        .parse(
            _diff("src/service.py", "import src.user") + _diff("src/user.py", "class User: pass")
        )
        .files,
    )
    imported_definition = DependencyAwareBundler(max_diff_tokens=8000).bundle(
        RUN_ID,
        UnifiedDiffParser()
        .parse(
            _diff("src/service.py", "from another.module import User")
            + _diff("src/user.py", "class User: pass")
        )
        .files,
    )

    assert direct_import.edges[0].relation is BundleRelation.IMPORT_REFERENCE
    assert imported_definition.edges[0].relation is BundleRelation.IMPORT_REFERENCE


def test_non_python_files_do_not_use_python_import_analysis() -> None:
    files = (
        UnifiedDiffParser()
        .parse(_diff("src/a.sql", "select 1;") + _diff("src/b.sql", "select 2;"))
        .files
    )

    result = DependencyAwareBundler(max_diff_tokens=8000).bundle(RUN_ID, files)

    assert result.edges == ()


@pytest.mark.case("UT-M3-002")
def test_cycle_and_budget_edges_are_recorded_without_losing_files() -> None:
    cycle_files = (
        UnifiedDiffParser()
        .parse(
            _diff("src/a.py", "from src.b import B\nfrom src.c import C")
            + _diff("src/b.py", "from src.c import C\nclass B: pass")
            + _diff("src/c.py", "class C: pass")
        )
        .files
    )
    cycle = DependencyAwareBundler(max_diff_tokens=8000).bundle(RUN_ID, cycle_files)
    budget_files = (
        UnifiedDiffParser()
        .parse(
            _diff("src/order.py", "a = '" + "x" * 240 + "'")
            + _diff("tests/test_order.py", "b = '" + "y" * 240 + "'")
        )
        .files
    )
    budget = DependencyAwareBundler(max_diff_tokens=100).bundle(RUN_ID, budget_files)

    assert BundleEdgeDecision.CUT_CYCLE in {edge.decision for edge in cycle.edges}
    assert BundleEdgeDecision.CUT_BUDGET in {edge.decision for edge in budget.edges}
    assert sorted(path for bundle in budget.bundles for path in bundle.paths) == [
        "src/order.py",
        "tests/test_order.py",
    ]


@pytest.mark.case("UT-M3-002")
def test_invalid_python_and_unrelated_files_remain_separate() -> None:
    files = (
        UnifiedDiffParser()
        .parse(_diff("src/a.py", "def broken(") + _diff("src/b.py", "value = 1"))
        .files
    )

    result = DependencyAwareBundler(max_diff_tokens=8000).bundle(RUN_ID, files)

    assert len(result.bundles) == 2
    assert result.edges == ()


@pytest.mark.case("UT-M3-001")
def test_schema_interface_and_model_migration_relations_are_supported() -> None:
    schema = DependencyAwareBundler(max_diff_tokens=8000).bundle(
        RUN_ID,
        UnifiedDiffParser()
        .parse(
            _diff("schemas/user_schema.py", "class UserSchema: pass")
            + _diff("src/user.py", "value = user_schema")
        )
        .files,
    )
    interface = DependencyAwareBundler(max_diff_tokens=8000).bundle(
        RUN_ID,
        UnifiedDiffParser()
        .parse(
            _diff("src/user_protocol.py", "class UserProtocol: pass")
            + _diff("src/user.py", "value = user")
        )
        .files,
    )
    migration = DependencyAwareBundler(max_diff_tokens=8000).bundle(
        RUN_ID,
        UnifiedDiffParser()
        .parse(
            _diff("src/user_model.py", "class User: pass")
            + _diff("migrations/user_migration.py", "value = 1")
        )
        .files,
    )

    assert schema.edges[0].relation is BundleRelation.SCHEMA_PROCESSING
    assert interface.edges[0].relation is BundleRelation.INTERFACE_IMPLEMENTATION
    assert migration.edges[0].relation is BundleRelation.MODEL_MIGRATION


def _diff(path: str, added: str) -> str:
    added_lines = added.splitlines()
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{len(added_lines)} @@\n" + "".join(f"+{line}\n" for line in added_lines)
    )
