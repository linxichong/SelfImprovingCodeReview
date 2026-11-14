"""機密情報検査用の送信内容および検査結果モデル.

検出結果には検出機能名、パス、行番号だけを保持する. 機密情報候補の
文字列はログやデータベースへ漏洩しないよう、このモデルには保存しない.

"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from self_improving_code_review.domain.common.hashing import hash_text

__all__ = [
    "ScanProfile",
    "ScanResult",
    "ScannedPayload",
    "SecretFinding",
    "SourceKind",
    "SourcePayload",
]


class SourceKind(Enum):
    """検査対象となる送信内容の入力元."""

    DIFF = "diff"
    CONTEXT_TOOL = "context_tool"
    SCM_PUBLICATION = "scm_publication"


class ScanProfile(Enum):
    """検査に適用する抑制プロファイル.

    ``STRICT`` はリポジトリの基準値、行内の許可リストコメント、
    パス固有の抑制を無視し、LLM 呼び出し前に使用する唯一のプロファイルとする.
    """

    STRICT = "strict"


@dataclass(frozen=True, slots=True)
class SourcePayload:
    """信頼境界を越えようとしている未信頼テキスト."""

    kind: SourceKind
    label: str
    text: str

    @property
    def content_hash(self) -> str:
        """この送信内容を一意に識別する SHA-256 ダイジェストを返す."""
        return hash_text(self.text)


@dataclass(frozen=True, slots=True)
class SecretFinding:
    """送信内容内で検出された単一の機密情報候補."""

    detector: str
    path: str
    line_number: int
    line_side: str = "diff"


@dataclass(frozen=True, slots=True)
class ScanResult:
    """送信内容の検査結果を集約したもの."""

    payload_hash: str
    engine_version: str
    profile: ScanProfile
    findings: tuple[SecretFinding, ...]

    @property
    def is_passed(self) -> bool:
        """機密情報候補が検出されなかった場合は True を返す."""
        return not self.findings


@dataclass(frozen=True, slots=True)
class ScannedPayload:
    """厳格な検査に合格した送信内容.

    検査識別情報は ``payload_hash`` に紐づく. テキストが変更された場合は、
    LLM へ送信する前に再検査しなければならない.
    """

    scan_id: str
    engine_version: str
    payload_hash: str
    text: str

    def matches(self, text: str) -> bool:
        """``text`` が検査済み送信内容と完全に一致する場合は True を返す."""
        return hash_text(text) == self.payload_hash
