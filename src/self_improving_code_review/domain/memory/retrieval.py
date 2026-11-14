"""メモリ検索のスコア計算と切詰め.

スコアは決定的に計算し、LLM を呼び出さない. トークン上限を超える
メモリは途中で切断せず除外し、除外件数を測定値として報告する.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatch

from self_improving_code_review.domain.change.models import ChangedFile
from self_improving_code_review.domain.common.enums import LineKind, RiskLevel
from self_improving_code_review.domain.common.tokens import estimate_tokens
from self_improving_code_review.domain.memory.models import MemoryContent

__all__ = [
    "DEFAULT_MAX_MEMORIES",
    "DEFAULT_MEMORY_TOKENS",
    "DEFAULT_PER_MEMORY_TOKENS",
    "RetrievalSelection",
    "ScoredMemory",
    "estimate_memory_tokens",
    "render_memory_block",
    "score_memory",
    "select_memories",
]

DEFAULT_MAX_MEMORIES = 5
DEFAULT_MEMORY_TOKENS = 1200
DEFAULT_PER_MEMORY_TOKENS = 200

_SCORE_EXACT_PATH = 100
_SCORE_SYMBOL = 80
_SCORE_FRAMEWORK = 60
_SCORE_LANGUAGE = 40
_SCORE_RISK_OR_CATEGORY = 30
_SCORE_KEYWORD = 10


@dataclass(frozen=True, slots=True)
class ScoredMemory:
    """検索スコアを付与したメモリ内容."""

    content: MemoryContent
    score: int
    estimated_tokens: int
    confirmed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RetrievalSelection:
    """プロンプトへ注入するメモリと除外件数."""

    selected: tuple[ScoredMemory, ...]
    truncated_count: int
    total_tokens: int


def render_memory_block(content: MemoryContent) -> str:
    """プロンプトへ埋め込む準信頼メモリブロックを返す."""
    return (
        f"- memory_id: {content.memory_id}\n"
        f"  version: {content.version}\n"
        f"  category: {content.category.value}\n"
        f"  polarity: {content.polarity.value}\n"
        f"  fact: {content.fact.strip()}\n"
        f"  review_instruction: {content.review_instruction.strip()}"
    )


def score_memory(
    content: MemoryContent,
    files: tuple[ChangedFile, ...],
    *,
    risk: RiskLevel,
    perspective: str,
) -> int:
    """``content`` の検索スコアを返す.

    Args:
        content: 有効メモリの不変内容.
        files: レビュー単位の変更ファイル.
        risk: レビュー単位のリスク水準.
        perspective: 計画処理が決めたレビュー観点.

    Returns:
        一致した条件の加算スコア. 一致がない場合は 0.
    """
    return max(
        (_score_file(content, item, risk=risk, perspective=perspective) for item in files),
        default=0,
    )


def _score_file(
    content: MemoryContent,
    changed_file: ChangedFile,
    *,
    risk: RiskLevel,
    perspective: str,
) -> int:
    """同じファイルが全指定条件を満たす場合だけ一致スコアを返す.

    ファイル間で一致条件を合成して、範囲外の知見を注入しない。
    """
    scope = content.scope
    files = (changed_file,)
    groups = (
        (scope.paths, _matches_path(scope.paths, changed_file), _SCORE_EXACT_PATH),
        (scope.symbols, _matches_symbols(scope.symbols, files), _SCORE_SYMBOL),
        (
            scope.frameworks,
            _matches_keywords(scope.frameworks, files, case_sensitive=False),
            _SCORE_FRAMEWORK,
        ),
        (scope.languages, _matches_languages(scope.languages, files), _SCORE_LANGUAGE),
        (
            scope.risk_tags,
            _matches_risk(scope.risk_tags, risk=risk, perspective=perspective),
            _SCORE_RISK_OR_CATEGORY,
        ),
        (
            scope.keywords,
            _matches_keywords(scope.keywords, files, case_sensitive=True),
            _SCORE_KEYWORD,
        ),
    )
    if any(values and not is_match for values, is_match, _weight in groups):
        return 0
    return sum(weight for values, _is_match, weight in groups if values)


def select_memories(
    scored: tuple[ScoredMemory, ...],
    *,
    max_memories: int = DEFAULT_MAX_MEMORIES,
    memory_token_budget: int = DEFAULT_MEMORY_TOKENS,
    per_memory_token_budget: int = DEFAULT_PER_MEMORY_TOKENS,
) -> RetrievalSelection:
    """スコア順に並べたメモリを予算内へ収める.

    同点は確信度の降順、次にメモリ識別子の昇順で解決し、同じ入力から
    常に同じ選択結果を生成する.

    Args:
        scored: スコア付与済みのメモリ.
        max_memories: 注入する最大件数.
        memory_token_budget: メモリ全体のトークン上限.
        per_memory_token_budget: 1 件あたりのトークン上限.

    Returns:
        注入するメモリ、除外件数、合計トークン数.
    """
    ordered = sorted(
        (item for item in scored if item.score > 0),
        key=lambda item: (
            -item.score,
            -item.content.confidence,
            -(item.confirmed_at.timestamp() if item.confirmed_at is not None else float("-inf")),
            item.content.memory_id.value,
        ),
    )

    selected: list[ScoredMemory] = []
    truncated = 0
    total_tokens = 0
    for item in ordered:
        if len(selected) >= max_memories:
            truncated += 1
            continue
        if item.estimated_tokens > per_memory_token_budget:
            truncated += 1
            continue
        if total_tokens + item.estimated_tokens > memory_token_budget:
            truncated += 1
            continue
        selected.append(item)
        total_tokens += item.estimated_tokens

    return RetrievalSelection(
        selected=tuple(selected),
        truncated_count=truncated,
        total_tokens=total_tokens,
    )


def estimate_memory_tokens(content: MemoryContent) -> int:
    """構築済みブロックのトークン数見積もりを返す."""
    return estimate_tokens(render_memory_block(content))


def _matches_path(patterns: tuple[str, ...], changed_file: ChangedFile) -> bool:
    """パスグロブに一致する場合は True を返す."""
    return any(fnmatch(changed_file.new_path.value, pattern) for pattern in patterns)


def _matches_languages(languages: tuple[str, ...], files: tuple[ChangedFile, ...]) -> bool:
    """いずれかのファイルの言語が一致する場合は True を返す."""
    lowered = {value.strip().lower() for value in languages}
    return any(item.language is not None and item.language.lower() in lowered for item in files)


def _matches_symbols(symbols: tuple[str, ...], files: tuple[ChangedFile, ...]) -> bool:
    """追加行にシンボル名が現れる場合は True を返す."""
    return _matches_keywords(symbols, files, case_sensitive=True)


def _matches_keywords(
    keywords: tuple[str, ...],
    files: tuple[ChangedFile, ...],
    *,
    case_sensitive: bool,
) -> bool:
    """追加行にキーワードが含まれる場合は True を返す.

    変更されていないコンテキスト行でメモリが発動しないよう、
    照合対象は追加行だけとする.
    """
    candidates = tuple(keyword if case_sensitive else keyword.lower() for keyword in keywords)
    for changed_file in files:
        for hunk in changed_file.hunks:
            for line in hunk.lines:
                if line.kind is not LineKind.ADDED:
                    continue
                text = line.text if case_sensitive else line.text.lower()
                if any(keyword in text for keyword in candidates):
                    return True
    return False


def _matches_risk(
    risk_tags: tuple[str, ...],
    *,
    risk: RiskLevel,
    perspective: str,
) -> bool:
    """リスク水準または観点がタグに一致する場合は True を返す."""
    lowered = {value.strip().lower() for value in risk_tags}
    return risk.value in lowered or perspective.strip().lower() in lowered
