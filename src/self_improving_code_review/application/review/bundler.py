"""M1 向けの単純かつ決定的なレビュー単位分割.

関連ファイルをまとめた後、大きすぎる集合をファイル境界、
続いて差分ブロック境界で分割する. 予算を超える単一差分ブロックは
LLM へ送信しない.
"""

from __future__ import annotations

from dataclasses import dataclass

from self_improving_code_review.application.review.models import ReviewBundle
from self_improving_code_review.application.review.risk import classify_risk
from self_improving_code_review.domain.change.models import ChangedFile, DiffHunk
from self_improving_code_review.domain.common.enums import FilterReason
from self_improving_code_review.domain.common.tokens import estimate_tokens
from self_improving_code_review.domain.common.values import BundleId, NormalizedPath, RunId

__all__ = ["BundlingResult", "OversizedFile", "SimpleBundler"]

_TEST_PATH_MARKERS: tuple[str, ...] = ("tests/", "test/", "spec/")
_TEST_NAME_PREFIXES: tuple[str, ...] = ("test_",)
_TEST_NAME_SUFFIXES: tuple[str, ...] = ("_test", ".test", ".spec", "test", "spec")


@dataclass(frozen=True, slots=True)
class OversizedFile:
    """単一の差分ブロックがレビュー単位のトークン予算を超えたため除外されたファイル."""

    path: NormalizedPath
    reason: FilterReason = FilterReason.BUDGET_EXCEEDED


@dataclass(frozen=True, slots=True)
class BundlingResult:
    """レビュー対象のレビュー単位と予算内に収まらなかったファイル."""

    bundles: tuple[ReviewBundle, ...]
    oversized_files: tuple[OversizedFile, ...]


class SimpleBundler:
    """レビュー可能なファイルをトークン上限付きのレビュー単位にまとめる."""

    def __init__(self, *, max_diff_tokens: int) -> None:
        """レビュー単位ごとの差分トークン予算を保持する.

        Args:
            max_diff_tokens: レビュー単位の差分トークン見積もり上限.
        """
        self._max_diff_tokens = max_diff_tokens

    def bundle(self, run_id: RunId, files: tuple[ChangedFile, ...]) -> BundlingResult:
        """``files`` を決定的な順序でレビュー単位にまとめる.

        Args:
            run_id: レビュー単位識別子が属する実行.
            files: 差分順のレビュー可能ファイル.

        Returns:
            レビュー単位と、差分ブロック単位で予算を超えたファイル.
        """
        groups = self._group_related(files)
        return self.bundle_groups(run_id, groups)

    def bundle_groups(
        self,
        run_id: RunId,
        groups: tuple[tuple[ChangedFile, ...], ...],
    ) -> BundlingResult:
        """決定済みの関連ファイル群を予算内のレビュー単位へ変換する."""
        bundles: list[ReviewBundle] = []
        oversized: list[OversizedFile] = []
        sequence = 1

        for group in groups:
            for chunk in self._split_group(group, oversized):
                bundle_id = BundleId(run_id=run_id, sequence=sequence)
                diff_text = "\n".join(changed_file.to_diff_text() for changed_file in chunk)
                bundles.append(
                    ReviewBundle(
                        bundle_id=bundle_id,
                        files=chunk,
                        risk=classify_risk(chunk).risk,
                        diff_text=diff_text,
                        diff_tokens=estimate_tokens(diff_text),
                    )
                )
                sequence += 1

        return BundlingResult(bundles=tuple(bundles), oversized_files=tuple(oversized))

    def _group_related(self, files: tuple[ChangedFile, ...]) -> tuple[tuple[ChangedFile, ...], ...]:
        """実装ファイルを対応するテストおよび同一ディレクトリのスキーマとまとめる."""
        remaining = list(files)
        groups: list[tuple[ChangedFile, ...]] = []
        consumed: set[str] = set()

        for changed_file in files:
            path = changed_file.new_path
            if path.value in consumed:
                continue
            group = [changed_file]
            consumed.add(path.value)
            for candidate in remaining:
                candidate_path = candidate.new_path
                if candidate_path.value in consumed:
                    continue
                if self._is_related(changed_file, candidate):
                    group.append(candidate)
                    consumed.add(candidate_path.value)
            groups.append(tuple(group))
        return tuple(groups)

    def _is_related(self, left: ChangedFile, right: ChangedFile) -> bool:
        """2つのファイルが同じレビュー単位に属する場合は True を返す."""
        left_path = left.new_path
        right_path = right.new_path
        if self._is_test(left_path) is not self._is_test(right_path):
            production, test = (
                (left_path, right_path) if self._is_test(right_path) else (right_path, left_path)
            )
            return self._normalize_test_stem(test.stem) == production.stem
        if left_path.parent and left_path.parent == right_path.parent:
            return _is_schema_or_migration(left_path) and _is_schema_or_migration(right_path)
        return False

    def _is_test(self, path: NormalizedPath) -> bool:
        """``path`` がテストファイルとみなせる場合は True を返す."""
        lowered = path.value.lower()
        if any(marker in lowered for marker in _TEST_PATH_MARKERS):
            return True
        stem = path.stem.lower()
        return stem.startswith(_TEST_NAME_PREFIXES) or stem.endswith(_TEST_NAME_SUFFIXES)

    def _normalize_test_stem(self, stem: str) -> str:
        """テストと対象を照合できるよう、一般的なテスト接辞を取り除く."""
        normalized = stem
        for prefix in _TEST_NAME_PREFIXES:
            normalized = normalized.removeprefix(prefix)
        for suffix in ("_test", ".test", ".spec"):
            normalized = normalized.removesuffix(suffix)
        return normalized

    def _split_group(
        self, group: tuple[ChangedFile, ...], oversized: list[OversizedFile]
    ) -> tuple[tuple[ChangedFile, ...], ...]:
        """すべてのまとまりが差分トークン予算内に収まるよう ``group`` を分割する."""
        chunks: list[tuple[ChangedFile, ...]] = []
        current: list[ChangedFile] = []
        current_tokens = 0

        for changed_file in group:
            file_tokens = estimate_tokens(changed_file.to_diff_text())
            if file_tokens > self._max_diff_tokens:
                if current:
                    chunks.append(tuple(current))
                    current = []
                    current_tokens = 0
                chunks.extend(self._split_file(changed_file, oversized))
                continue
            if current and current_tokens + file_tokens > self._max_diff_tokens:
                chunks.append(tuple(current))
                current = []
                current_tokens = 0
            current.append(changed_file)
            current_tokens += file_tokens

        if current:
            chunks.append(tuple(current))
        return tuple(chunks)

    def _split_file(
        self, changed_file: ChangedFile, oversized: list[OversizedFile]
    ) -> tuple[tuple[ChangedFile, ...], ...]:
        """単一ファイルを差分ブロック境界で分割し、収まらない差分ブロックを報告する."""
        chunks: list[tuple[ChangedFile, ...]] = []
        current_hunks: list[DiffHunk] = []
        current_tokens = 0
        has_oversized_hunk = False

        for hunk in changed_file.hunks:
            hunk_tokens = estimate_tokens(hunk.to_diff_text())
            if hunk_tokens > self._max_diff_tokens:
                has_oversized_hunk = True
                continue
            if current_hunks and current_tokens + hunk_tokens > self._max_diff_tokens:
                chunks.append((_with_hunks(changed_file, tuple(current_hunks)),))
                current_hunks = []
                current_tokens = 0
            current_hunks.append(hunk)
            current_tokens += hunk_tokens

        if current_hunks:
            chunks.append((_with_hunks(changed_file, tuple(current_hunks)),))
        if has_oversized_hunk:
            oversized.append(OversizedFile(path=changed_file.new_path))
        return tuple(chunks)


def _with_hunks(changed_file: ChangedFile, hunks: tuple[DiffHunk, ...]) -> ChangedFile:
    """``hunks`` のみに限定した ``changed_file`` の複製を返す."""
    return ChangedFile(
        new_path=changed_file.new_path,
        old_path=changed_file.old_path,
        change_type=changed_file.change_type,
        language=changed_file.language,
        hunks=hunks,
        is_binary=changed_file.is_binary,
    )


def _is_schema_or_migration(path: NormalizedPath) -> bool:
    """スキーマ、モデル、移行ファイルの場合は True を返す."""
    lowered = path.value.lower()
    return (
        "migration" in lowered
        or "schema" in lowered
        or "model" in lowered
        or path.suffix in {".sql", ".graphql"}
    )
