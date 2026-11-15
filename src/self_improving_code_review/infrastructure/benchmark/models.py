"""固定評価入力とツール応答の契約."""

from pathlib import PurePosixPath
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from self_improving_code_review.messages import Messages

Sha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
InstanceId = Annotated[str, StringConstraints(pattern=r"^[\w.-]+__[\w.-]+@[a-f0-9]{7}$")]
Model = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
Count = Annotated[int, Field(ge=0)]
Line = Annotated[int, Field(gt=0)]
Tool = Literal["sicr", "ocr", "codex"]
Status = Literal[
    "complete",
    "failed",
    "blocked",
    "partial-success",
    "timeout",
    "missing",
    "invalid",
    "judge-failed",
]
TOOLS: tuple[Tool, ...] = ("sicr", "ocr", "codex")


class Manifest(BaseModel):
    """評価対象、固定モデルと外部ツールの識別子を保持する."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1]
    upstream_commit: Sha
    dataset_sha256: Digest
    instances: Annotated[tuple[InstanceId, ...], Field(min_length=1)]
    repeats: Annotated[int, Field(ge=1, le=20)]
    model: Model
    judge_model: Model
    gateway: str
    ocr_sha256: Digest
    codex_version: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def validate_pins(self) -> Self:
        """設定の重複と送信先の曖昧な指定を拒否する."""
        url = urlsplit(self.gateway)
        if (
            len(set(self.instances)) != len(self.instances)
            or any(x.lower() in {"latest", "unresolved"} for x in (self.model, self.judge_model))
            or url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path != "/v1"
        ):
            raise ValueError(Messages.BENCH_INVALID)
        return self


class Reference(BaseModel):
    """上流が提供する参考コメントを保持する."""

    model_config = ConfigDict(strict=True)
    path: str
    note: Annotated[str, Field(min_length=1)]
    from_line: Line | None = None
    to_line: Line | None = None
    side: Literal["left", "right"] | None = None


class Sample(BaseModel):
    """固定された GitHub PR と参考コメントを読み取る."""

    model_config = ConfigDict(strict=True)
    pr_url: Annotated[
        str, StringConstraints(pattern=r"^https://github\.com/[\w.-]+/[\w.-]+/pull/[0-9]+$")
    ] = Field(validation_alias="githubPrUrl")
    source_commit: Sha
    target_commit: Sha
    comments: list[Reference]

    @property
    def repo(self) -> str:
        """上流の PR URL からリポジトリ識別子を得る."""
        return "/".join(self.pr_url.split("/")[3:5])

    @property
    def instance_id(self) -> str:
        """公式規則でサンプル識別子を組み立てる."""
        return f"{self.repo.replace('/', '__')}@{self.target_commit[:7]}"


class Finding(BaseModel):
    """公式評価器へ渡す新コード側の指摘."""

    model_config = ConfigDict(extra="forbid", strict=True)
    path: Annotated[str, Field(min_length=1)]
    note: Annotated[str, Field(min_length=1)]
    from_line: Line
    to_line: Line

    @model_validator(mode="after")
    def validate_location(self) -> Self:
        """相対パスと閉区間を検証する."""
        path = PurePosixPath(self.path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != self.path
            or self.path == "."
            or "\\" in self.path
            or self.from_line > self.to_line
        ):
            raise ValueError(Messages.BENCH_INVALID)
        return self

    def judge_row(self) -> dict[str, object]:
        """指摘を上流評価器の入力形式へ変換する."""
        return {**self.model_dump(), "side": "right"}


class Usage(BaseModel):
    """取得できたレビュー使用量を保持する."""

    model_config = ConfigDict(strict=True)
    input_tokens: Count | None = None
    output_tokens: Count | None = None
    latency_ms: Count | None = None
    cost_usd: str | None = None


class Trial(BaseModel):
    """試行の状態と評価可能なコメント."""

    model_config = ConfigDict(strict=True)
    status: Status
    usage: Usage = Field(default_factory=Usage)
    findings: tuple[Finding, ...] = ()


class Score(BaseModel):
    """公式評価器で一致した参考コメントと指摘の件数."""

    model_config = ConfigDict(strict=True)
    hit_n: Count
    matched_generated_n: Count
    matched_reference_indices: tuple[Line, ...]
