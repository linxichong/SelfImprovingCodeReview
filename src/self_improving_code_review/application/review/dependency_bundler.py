"""変更ファイル関係グラフを使用する決定的なレビュー単位分割."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from self_improving_code_review.application.review.bundler import (
    BundlingResult,
    OversizedFile,
    SimpleBundler,
)
from self_improving_code_review.application.review.models import ReviewBundle
from self_improving_code_review.domain.change.models import ChangedFile
from self_improving_code_review.domain.common.enums import (
    BundleEdgeDecision,
    BundleRelation,
)
from self_improving_code_review.domain.common.tokens import estimate_tokens
from self_improving_code_review.domain.common.values import RunId

__all__ = ["BundleEdge", "DependencyAwareBundler", "DependencyBundlingResult"]

_WEIGHTS: dict[BundleRelation, int] = {
    BundleRelation.IMPLEMENTATION_TEST: 100,
    BundleRelation.INTERFACE_IMPLEMENTATION: 90,
    BundleRelation.SCHEMA_PROCESSING: 80,
    BundleRelation.MODEL_MIGRATION: 80,
    BundleRelation.IMPORT_REFERENCE: 60,
    BundleRelation.SAME_DIRECTORY_STEM: 20,
}
_RELATION_ORDER = {relation: index for index, relation in enumerate(_WEIGHTS)}


@dataclass(frozen=True, slots=True)
class BundleEdge:
    """2 ファイル間の関係とグラフ結合時の判定."""

    left_path: str
    right_path: str
    relation: BundleRelation
    weight: int
    decision: BundleEdgeDecision


@dataclass(frozen=True, slots=True)
class DependencyBundlingResult:
    """レビュー単位分割結果と監査可能な関係辺."""

    bundles: tuple[ReviewBundle, ...]
    oversized_files: tuple[OversizedFile, ...]
    edges: tuple[BundleEdge, ...]


@dataclass(slots=True)
class _Component:
    """Union-Find の構成要素情報."""

    parent: str
    paths: set[str]
    tokens: int


class DependencyAwareBundler:
    """関係辺を安定順で評価してレビュー単位を構成する."""

    def __init__(self, *, max_diff_tokens: int) -> None:
        """レビュー単位の差分予算を保持する."""
        self._max_diff_tokens = max_diff_tokens
        self._splitter = SimpleBundler(max_diff_tokens=max_diff_tokens)

    def bundle(
        self,
        run_id: RunId,
        files: tuple[ChangedFile, ...],
    ) -> DependencyBundlingResult:
        """変更ファイルを関係グラフと予算に基づいて分割する."""
        ordered_files = tuple(sorted(files, key=lambda item: item.new_path.value))
        by_path = {item.new_path.value: item for item in ordered_files}
        components = {
            path: _Component(
                parent=path,
                paths={path},
                tokens=estimate_tokens(changed_file.to_diff_text()),
            )
            for path, changed_file in by_path.items()
        }
        candidates = self._candidate_edges(ordered_files)
        decided: list[BundleEdge] = []
        for left_path, right_path, relation in candidates:
            left_root = _find(components, left_path)
            right_root = _find(components, right_path)
            if left_root == right_root:
                decision = BundleEdgeDecision.CUT_CYCLE
            elif (
                components[left_root].tokens + components[right_root].tokens > self._max_diff_tokens
            ):
                decision = BundleEdgeDecision.CUT_BUDGET
            else:
                decision = BundleEdgeDecision.SELECTED
                _union(components, left_root, right_root)
            decided.append(
                BundleEdge(
                    left_path=left_path,
                    right_path=right_path,
                    relation=relation,
                    weight=_WEIGHTS[relation],
                    decision=decision,
                )
            )

        grouped_paths: dict[str, set[str]] = {}
        for path in by_path:
            grouped_paths.setdefault(_find(components, path), set()).add(path)
        groups = tuple(
            tuple(by_path[path] for path in sorted(paths))
            for paths in sorted(grouped_paths.values(), key=lambda item: min(item))
        )
        result: BundlingResult = self._splitter.bundle_groups(run_id, groups)
        return DependencyBundlingResult(
            bundles=result.bundles,
            oversized_files=result.oversized_files,
            edges=tuple(decided),
        )

    def _candidate_edges(
        self,
        files: tuple[ChangedFile, ...],
    ) -> tuple[tuple[str, str, BundleRelation], ...]:
        """各ファイル対から優先度が最も高い関係だけを返す."""
        candidates: list[tuple[str, str, BundleRelation]] = []
        for index, left in enumerate(files):
            for right in files[index + 1 :]:
                relations = _relations(left, right)
                if not relations:
                    continue
                relation = min(
                    relations,
                    key=lambda item: (-_WEIGHTS[item], _RELATION_ORDER[item]),
                )
                candidates.append((left.new_path.value, right.new_path.value, relation))
        return tuple(
            sorted(
                candidates,
                key=lambda item: (
                    -_WEIGHTS[item[2]],
                    item[0],
                    item[1],
                    item[2].value,
                ),
            )
        )


def _relations(left: ChangedFile, right: ChangedFile) -> tuple[BundleRelation, ...]:
    """ファイル対に成立する関係を返す."""
    relations: list[BundleRelation] = []
    left_path = left.new_path.value.lower()
    right_path = right.new_path.value.lower()
    left_stem = _normalized_stem(left.new_path.stem)
    right_stem = _normalized_stem(right.new_path.stem)
    if _is_test(left_path) is not _is_test(right_path) and left_stem == right_stem:
        relations.append(BundleRelation.IMPLEMENTATION_TEST)
    if _interface_relation(left, right, left_stem, right_stem):
        relations.append(BundleRelation.INTERFACE_IMPLEMENTATION)
    if _schema_relation(left, right, left_stem, right_stem):
        relations.append(BundleRelation.SCHEMA_PROCESSING)
    if _model_migration_relation(left_path, right_path, left_stem, right_stem):
        relations.append(BundleRelation.MODEL_MIGRATION)
    if _has_import_reference(left, right) or _has_import_reference(right, left):
        relations.append(BundleRelation.IMPORT_REFERENCE)
    if left.new_path.parent == right.new_path.parent and left_stem == right_stem:
        relations.append(BundleRelation.SAME_DIRECTORY_STEM)
    return tuple(relations)


def _interface_relation(
    left: ChangedFile,
    right: ChangedFile,
    left_stem: str,
    right_stem: str,
) -> bool:
    """インターフェースと実装の参照関係がある場合は True を返す."""
    left_marker = _has_marker(left.new_path.value, ("interface", "protocol", "abc"))
    right_marker = _has_marker(right.new_path.value, ("interface", "protocol", "abc"))
    return (left_marker and left_stem in _source_text(right).lower()) or (
        right_marker and right_stem in _source_text(left).lower()
    )


def _schema_relation(
    left: ChangedFile,
    right: ChangedFile,
    left_stem: str,
    right_stem: str,
) -> bool:
    """スキーマと処理ファイルの参照関係がある場合は True を返す."""
    left_schema = _has_marker(left.new_path.value, ("schema", "openapi", "graphql"))
    right_schema = _has_marker(right.new_path.value, ("schema", "openapi", "graphql"))
    return (left_schema and left_stem in _source_text(right).lower()) or (
        right_schema and right_stem in _source_text(left).lower()
    )


def _model_migration_relation(
    left_path: str,
    right_path: str,
    left_stem: str,
    right_stem: str,
) -> bool:
    """モデルと移行ファイルのステムが一致する場合は True を返す."""
    left_model = _has_marker(left_path, ("model",))
    right_model = _has_marker(right_path, ("model",))
    left_migration = _has_marker(left_path, ("migration", "migrations"))
    right_migration = _has_marker(right_path, ("migration", "migrations"))
    return left_stem == right_stem and (
        (left_model and right_migration) or (right_model and left_migration)
    )


def _has_import_reference(source: ChangedFile, target: ChangedFile) -> bool:
    """Python 抽象構文木で対象モジュールまたは定義を参照する場合は True を返す."""
    if source.language != "python" or target.language != "python":
        return False
    try:
        tree = ast.parse(_source_text(source))
        target_tree = ast.parse(_source_text(target))
    except SyntaxError:
        return False
    target_module = target.new_path.value.removesuffix(".py").replace("/", ".")
    target_name = target.new_path.stem
    definitions = {
        node.name
        for node in ast.walk(target_tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name == target_module or alias.name.endswith(f".{target_name}")
            for alias in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == target_module or module.endswith(f".{target_name}"):
                return True
            if any(alias.name in definitions for alias in node.names):
                return True
    return False


def _source_text(changed_file: ChangedFile) -> str:
    """差分に含まれる変更後側のソース断片を返す."""
    return "\n".join(
        line.text for hunk in changed_file.hunks for line in hunk.lines if line.new_line is not None
    )


def _normalized_stem(stem: str) -> str:
    """関係照合用に一般的な役割接辞を除いたステムを返す."""
    value = stem.lower().removeprefix("test_")
    for suffix in (
        "_test",
        ".test",
        ".spec",
        "_schema",
        "_model",
        "_migration",
        "_interface",
        "_protocol",
        "_impl",
    ):
        value = value.removesuffix(suffix)
    return value


def _is_test(path: str) -> bool:
    """テスト配置または命名の場合は True を返す."""
    name = path.rsplit("/", maxsplit=1)[-1]
    stem = name.rsplit(".", maxsplit=1)[0]
    return (
        path.startswith(("tests/", "test/", "spec/"))
        or "/tests/" in path
        or stem.startswith("test_")
        or stem.endswith(("_test", ".test", ".spec"))
    )


def _has_marker(path: str, markers: tuple[str, ...]) -> bool:
    """パスにいずれかの役割語が含まれる場合は True を返す."""
    lowered = path.lower()
    return any(marker in lowered for marker in markers)


def _find(components: dict[str, _Component], path: str) -> str:
    """構成要素の根を経路圧縮して返す."""
    parent = components[path].parent
    if parent != path:
        components[path].parent = _find(components, parent)
    return components[path].parent


def _union(components: dict[str, _Component], left_root: str, right_root: str) -> None:
    """辞書順で安定した根へ 2 構成要素を結合する."""
    root, child = sorted((left_root, right_root))
    components[child].parent = root
    components[root].paths.update(components[child].paths)
    components[root].tokens += components[child].tokens
