"""プロンプトテンプレートの読み込み.

プロンプトはバージョン管理されたリソースである. アプリケーションは
バージョン、内容ハッシュ、トークン数だけを記録し、構築済みプロンプトは永続化しない.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from self_improving_code_review.domain.common.enums import ReviewerType
from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.messages import Messages

__all__ = ["DEFAULT_PROMPT_NAME", "PromptTemplate", "load_prompt_template"]

DEFAULT_PROMPT_NAME = "review_general_v1"
_REQUIRED_KEYS: frozenset[str] = frozenset(
    {"prompt_version", "system_instruction", "user_template"}
)
_PROFILE_KEYS = frozenset(item.value for item in ReviewerType)


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """バージョン管理されたレビュー用プロンプト."""

    name: str
    version: int
    system_instruction: str
    user_template: str
    content_hash: str
    reviewer_profiles: tuple[tuple[ReviewerType, str], ...] = ()

    @property
    def version_label(self) -> str:
        """``review_general_v1@5`` など、実行に記録する識別子を返す."""
        return f"{self.name}@{self.version}"

    def profile_for(self, reviewer_type: ReviewerType) -> str | None:
        """レビュー種別に対応する固定プロファイルを返す."""
        return next(
            (
                text
                for profile_type, text in self.reviewer_profiles
                if profile_type is reviewer_type
            ),
            None,
        )


def load_prompt_template(prompts_root: Path, name: str = DEFAULT_PROMPT_NAME) -> PromptTemplate:
    """``prompts_root`` からプロンプトテンプレート ``name`` を読み込む.

    Raises:
        InvalidInputError: ファイルが存在しない、読み取れない、対応表ではない、
            または必須キーがない場合.
    """
    prompt_path = prompts_root / f"{name}.yaml"
    try:
        raw_text = prompt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        message = f"{Messages.PROMPT_TEMPLATE_READ_FAILED}: {prompt_path}: {exc}"
        raise InvalidInputError(message) from exc

    try:
        document = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        message = f"{Messages.PROMPT_TEMPLATE_YAML_INVALID}: {prompt_path}"
        raise InvalidInputError(message) from exc

    if not isinstance(document, dict):
        message = f"{Messages.PROMPT_TEMPLATE_NOT_MAPPING}: {prompt_path}"
        raise InvalidInputError(message)
    missing = _REQUIRED_KEYS - frozenset(document)
    if missing:
        message = f"{Messages.PROMPT_TEMPLATE_KEYS_MISSING}: {prompt_path}: {sorted(missing)}"
        raise InvalidInputError(message)

    version = document["prompt_version"]
    if not isinstance(version, int) or version < 1:
        message = f"{Messages.PROMPT_TEMPLATE_VERSION_INVALID}: {prompt_path}"
        raise InvalidInputError(message)

    reviewer_profiles = _reviewer_profiles(prompt_path, document.get("reviewer_profiles"))

    return PromptTemplate(
        name=name,
        version=version,
        system_instruction=str(document["system_instruction"]),
        user_template=str(document["user_template"]),
        content_hash=hash_text(raw_text),
        reviewer_profiles=reviewer_profiles,
    )


def _reviewer_profiles(
    prompt_path: Path,
    value: object,
) -> tuple[tuple[ReviewerType, str], ...]:
    """任意のレビュー種別別固定プロファイルを検証して返す."""
    if value is None:
        return ()
    if not isinstance(value, dict) or frozenset(value) != _PROFILE_KEYS:
        message = f"{Messages.PROMPT_TEMPLATE_PROFILES_INCOMPLETE}: {prompt_path}"
        raise InvalidInputError(message)
    profiles: list[tuple[ReviewerType, str]] = []
    for reviewer_type in ReviewerType:
        profile = value.get(reviewer_type.value)
        if not isinstance(profile, str) or not profile.strip():
            message = f"{Messages.PROMPT_TEMPLATE_PROFILE_EMPTY}: {prompt_path}"
            raise InvalidInputError(message)
        profiles.append((reviewer_type, profile))
    return tuple(profiles)
