"""SCM 外部呼出しの安全な計測モデル."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScmApiMetrics:
    """SCM API 呼出しで観測した安全な計測値."""

    call_count: int = 0
    retry_count: int = 0
    retry_wait_ms: int = 0
