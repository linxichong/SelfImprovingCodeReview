"""振り分けと予算決定に使用するリスク分類.

計画確定前に LLM 呼び出しが不要となるよう、分類はキーワードとパスに基づく.

"""

from __future__ import annotations

from self_improving_code_review.domain.change.models import ChangedFile
from self_improving_code_review.domain.common.enums import LineKind, RiskLevel

__all__ = ["RiskAssessment", "classify_risk"]

from dataclasses import dataclass

_SECURITY_KEYWORDS: tuple[str, ...] = (
    "authenticate",
    "authorization",
    "authorize",
    "cipher",
    "crypt",
    "debug",
    "hmac",
    "jwt",
    "oauth",
    "password",
    "permission",
    "private_key",
    "propagate_exceptions",
    "resolve_entities",
    "secret",
    "session",
    "token",
    "verify_signature",
)

_DATA_KEYWORDS: tuple[str, ...] = (
    "alter table",
    "delete from",
    "deserialize",
    "drop table",
    "eval(",
    "exec(",
    "insert into",
    "os.system",
    "pickle.load",
    "select ",
    "subprocess",
    "update ",
    "yaml.load",
)

_API_KEYWORDS: tuple[str, ...] = (
    "@app.",
    "@router.",
    "apiroute",
    "basemodel",
    "controller",
    "endpoint",
    "fastapi",
    "requestmapping",
    "schema",
)

_SECURITY_PATH_MARKERS: tuple[str, ...] = ("/auth", "/security", "security/", "auth/")
_DATA_PATH_MARKERS: tuple[str, ...] = ("migrations/", "/persistence", "/repository", "/dao")
_API_PATH_MARKERS: tuple[str, ...] = ("/api", "/controller", "/routes", "/schema")
_TEST_PATH_MARKERS: tuple[str, ...] = ("tests/", "test/", "spec/")


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """リスク水準と、そこから決まるレビュー観点."""

    risk: RiskLevel
    perspective: str


def classify_risk(files: tuple[ChangedFile, ...]) -> RiskAssessment:
    """``files`` に対するリスク評価を返す.

    最も高い一致分類を採用するため、テストと認証変更が混在する
    レビュー単位も高リスクとして扱う.
    """
    if not files:
        return RiskAssessment(risk=RiskLevel.MEDIUM, perspective="general")

    added_text = _collect_added_text(files)
    paths = tuple(changed_file.new_path.value.lower() for changed_file in files)

    if _matches(paths, _SECURITY_PATH_MARKERS) or _contains(added_text, _SECURITY_KEYWORDS):
        return RiskAssessment(risk=RiskLevel.HIGH, perspective="security")
    if _matches(paths, _DATA_PATH_MARKERS) or _contains(added_text, _DATA_KEYWORDS):
        return RiskAssessment(risk=RiskLevel.HIGH, perspective="security-data")
    if _matches(paths, _API_PATH_MARKERS) or _contains(added_text, _API_KEYWORDS):
        return RiskAssessment(risk=RiskLevel.MEDIUM, perspective="api-validation")
    if all(_matches((path,), _TEST_PATH_MARKERS) for path in paths):
        return RiskAssessment(risk=RiskLevel.LOW, perspective="test-regression")
    return RiskAssessment(risk=RiskLevel.MEDIUM, perspective="general")


def _collect_added_text(files: tuple[ChangedFile, ...]) -> str:
    """追加行を連結して小文字化した文字列を返す."""
    fragments = [
        line.text
        for changed_file in files
        for hunk in changed_file.hunks
        for line in hunk.lines
        if line.kind is LineKind.ADDED
    ]
    return "\n".join(fragments).lower()


def _contains(text: str, keywords: tuple[str, ...]) -> bool:
    """``text`` にいずれかのキーワードが含まれる場合は True を返す."""
    return any(keyword in text for keyword in keywords)


def _matches(paths: tuple[str, ...], markers: tuple[str, ...]) -> bool:
    """いずれかのパスにいずれかの印が含まれる場合は True を返す."""
    return any(marker in path for path in paths for marker in markers)
