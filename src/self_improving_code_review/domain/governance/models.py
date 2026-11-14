"""M5 のプロジェクト分離と統制に使用する純粋モデル."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.domain.common.values import ProjectId
from self_improving_code_review.messages import Messages

__all__ = [
    "GovernanceResourceType",
    "ProjectRecord",
    "ProjectStatus",
    "RepositoryBinding",
    "validate_governance_reason",
]

_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")


class ProjectStatus(Enum):
    """プロジェクトが新規操作を受け付ける状態を表す."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class GovernanceResourceType(Enum):
    """既存データへ明示的に割り当てられる資源種別."""

    GROUND_TRUTH_SET = "ground-truth-set"
    SCM_CHANGE = "scm-change"


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    """登録済みプロジェクトと方針版を表す."""

    project_id: ProjectId
    name: str
    policy_hash: str
    status: ProjectStatus
    lock_version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        """名称、ハッシュ、ロック版および UTC 時刻を検証する."""
        normalized_name = self.name.strip()
        if not 1 <= len(normalized_name) <= 100 or _CONTROL_PATTERN.search(normalized_name):
            raise InvalidInputError(Messages.PROJECT_NAME_INVALID)
        if _HASH_PATTERN.fullmatch(self.policy_hash) is None:
            raise InvalidInputError(Messages.PROJECT_POLICY_HASH_INVALID)
        if self.lock_version < 1:
            raise InvalidInputError(Messages.PROJECT_LOCK_INVALID)
        if any(
            value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value)
            for value in (self.created_at, self.updated_at)
        ):
            raise InvalidInputError(Messages.PROJECT_TIME_NOT_UTC)
        object.__setattr__(self, "name", normalized_name)


@dataclass(frozen=True, slots=True)
class RepositoryBinding:
    """一つの外部リポジトリとプロジェクトの対応を表す."""

    project_id: ProjectId
    provider: str
    repository_external_id: str
    created_at: datetime

    def __post_init__(self) -> None:
        """提供元、外部識別子および UTC 時刻を検証する."""
        provider = self.provider.strip().lower()
        repository = self.repository_external_id.strip()
        if _PROVIDER_PATTERN.fullmatch(provider) is None:
            raise InvalidInputError(Messages.REPOSITORY_PROVIDER_INVALID)
        if not 1 <= len(repository) <= 255 or any(character.isspace() for character in repository):
            raise InvalidInputError(Messages.REPOSITORY_EXTERNAL_ID_INVALID)
        if self.created_at.tzinfo is None or self.created_at.utcoffset() != UTC.utcoffset(
            self.created_at
        ):
            raise InvalidInputError(Messages.REPOSITORY_BINDING_TIME_NOT_UTC)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "repository_external_id", repository)


def validate_governance_reason(reason: str) -> str:
    """監査理由を正規化し、空値、制御文字および過大入力を拒否する."""
    normalized = reason.strip()
    if not 1 <= len(normalized) <= 500 or _CONTROL_PATTERN.search(normalized):
        raise InvalidInputError(Messages.GOVERNANCE_REASON_INVALID)
    return normalized
