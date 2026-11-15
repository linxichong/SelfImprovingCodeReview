"""固定チェックアウトと外部レビューツールを制御する."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from pydantic import TypeAdapter

from self_improving_code_review.domain.common.errors import InvalidInputError
from self_improving_code_review.infrastructure.benchmark.conversion import convert
from self_improving_code_review.infrastructure.benchmark.models import (
    Manifest,
    Sample,
    Score,
    Tool,
    Trial,
)
from self_improving_code_review.messages import Messages

_SAMPLES = TypeAdapter(list[Sample])
_BRIDGE = Path(__file__).with_name("bridge.py")


def command(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    body: str | None = None,
    timeout: int = 240,
) -> subprocess.CompletedProcess[str]:
    """シェルを使わず固定引数を実行し、外部出力を端末へ出さない."""
    return subprocess.run(  # nosec B603
        args,
        cwd=cwd,
        env=env,
        input=body,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def git(repo: Path, *args: str) -> str:
    """Git の出力を受け取り、失敗は固定文言で返す."""
    result = command(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=repo)
    if result.returncode:
        raise InvalidInputError(Messages.BENCH_GIT_FAILED)
    return result.stdout.strip()


def load_samples(upstream: Path, manifest: Manifest) -> tuple[Sample, ...]:
    """コミット、データハッシュ、選択 ID を照合する."""
    if git(upstream, "rev-parse", "HEAD") != manifest.upstream_commit or git(
        upstream, "status", "--porcelain", "--untracked-files=all"
    ):
        raise InvalidInputError(Messages.BENCH_UPSTREAM)
    content = (upstream / "dataset/v1/positive_samples.json").read_bytes()
    if hashlib.sha256(content).hexdigest() != manifest.dataset_sha256:
        raise InvalidInputError(Messages.BENCH_UPSTREAM)
    items = _SAMPLES.validate_json(content, strict=True)
    index = {item.instance_id: item for item in items}
    if len(index) != len(items) or not all(case in index for case in manifest.instances):
        raise InvalidInputError(Messages.BENCH_INVALID)
    return tuple(index[case] for case in manifest.instances)


@dataclass(frozen=True, slots=True)
class Workspace:
    """一つの PR に対して固定された差分と二つの作業コピーを保持する."""

    base: str
    head: str
    diff_sha256: str
    sicr: Path
    external: Path

    @classmethod
    def prepare(cls, folder: Path, sample: Sample, project: Path) -> Workspace:
        """公開 Git 入力を取得し、レビュー用資産を自ツール専用に配置する."""
        sicr, external = folder / "sicr", folder / "external"
        sicr.mkdir()
        git(sicr, "init", "--quiet")
        github = f"https://github.com/{sample.repo}.git"
        git(sicr, "remote", "add", "origin", github)
        git(
            sicr,
            "fetch",
            "--quiet",
            "--depth=100",
            "--filter=blob:none",
            "origin",
            sample.source_commit,
            sample.target_commit,
        )
        git(sicr, "checkout", "--quiet", "--detach", sample.target_commit)
        base = git(sicr, "merge-base", sample.source_commit, sample.target_commit)
        git(sicr, "worktree", "add", "--quiet", "--detach", str(external), sample.target_commit)
        diff = git(
            external, "diff", "--no-color", "--no-ext-diff", "--no-textconv", f"{base}...HEAD"
        )
        prompts = sicr / "prompts"
        template = prompts / "review_general_v1.yaml"
        audit_dir = sicr / ".review"
        if (
            prompts.is_symlink()
            or template.exists()
            or template.is_symlink()
            or (audit_dir.exists() or audit_dir.is_symlink())
        ):
            raise InvalidInputError(Messages.BENCH_WORKSPACE)
        prompts.mkdir(exist_ok=True)
        shutil.copyfile(project / "prompts/review_general_v1.yaml", template)
        shutil.copytree(project / "config/rules", audit_dir / "benchmark-rules")
        return cls(
            base, sample.target_commit, hashlib.sha256(diff.encode()).hexdigest(), sicr, external
        )


@dataclass(frozen=True, slots=True)
class BenchTools:
    """実行ファイル、固定モデルおよび環境から渡される鍵を保持する."""

    manifest: Manifest
    project: Path
    upstream: Path
    ocr: Path
    codex: Path
    evaluator_python: Path

    def verify(self) -> None:
        """外部ツールの版と配布物を実行前に照合する."""
        _key()
        if self.codex.name != "codex" or digest(self.ocr) != self.manifest.ocr_sha256:
            raise InvalidInputError(Messages.BENCH_TOOLS)
        result = command([str(self.codex), "--version"], cwd=self.project)
        if result.returncode or result.stdout.strip() != f"codex-cli {self.manifest.codex_version}":
            raise InvalidInputError(Messages.BENCH_TOOLS)

        check_dependencies = (
            "import importlib.metadata as m, sys; "
            "pins = [line.strip().split('==') for line in sys.stdin if line.strip()]; "
            "sys.exit(any(m.version(name) != version for name, version in pins))"
        )
        requirements = (self.upstream / "evaluation/requirements.txt").read_text()
        versions = command(
            [str(self.evaluator_python), "-c", check_dependencies],
            cwd=self.project,
            env=_env(),
            body=requirements,
        )
        if versions.returncode:
            raise InvalidInputError(Messages.BENCH_DEPENDENCIES)

    def review(self, tool: Tool, workspace: Workspace, instance: str, repeat: int) -> Trial:
        """ツールを実行して明示的な失敗状態または採点できる結果を返す."""
        now = monotonic()
        with _ToolCall(self, tool, workspace, instance, repeat) as call:
            try:
                result = command(call.args, cwd=self.project, env=call.env, timeout=270)
            except subprocess.TimeoutExpired:
                return Trial(status="timeout")
            if call.output is not None and not call.output.is_file():
                return Trial(status="failed" if result.returncode else "missing")
            text = call.output.read_text() if call.output is not None else result.stdout
            if not text and result.returncode:
                return Trial(status="failed")
            try:
                return convert(tool, text, result.returncode, round((monotonic() - now) * 1000))
            except ValueError:
                return Trial(status="invalid")

    def score(self, sample: Sample, trial: Trial) -> Score:
        """上流と同じセマンティック判定を隔離したプロセスで呼ぶ."""
        body = json.dumps(
            {
                "references": [row.model_dump() for row in sample.comments],
                "findings": [item.judge_row() for item in trial.findings],
            }
        )
        result = command(
            [str(self.evaluator_python), str(_BRIDGE), str(self.upstream), "score"],
            cwd=self.project,
            body=body,
            env=_env(
                JUDGE_API_KEY=_key(),
                JUDGE_MODEL=self.manifest.judge_model,
                JUDGE_BASE_URL=self.manifest.gateway,
                JUDGE_USE_MOCK="false",
                JUDGE_CONCURRENCY="1",
            ),
        )
        if result.returncode:
            raise InvalidInputError(Messages.BENCH_SCORE)
        score = Score.model_validate_json(result.stdout)
        ids = score.matched_reference_indices
        if (
            score.hit_n != len(set(ids))
            or len(ids) != score.hit_n
            or any(index > len(sample.comments) for index in ids)
            or score.matched_generated_n > len(trial.findings)
        ):
            raise InvalidInputError(Messages.BENCH_SCORE)
        return score


def digest(path: Path) -> str:
    """ファイルの SHA-256 を返す."""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _key() -> str:
    """秘密を入力ファイルやレポートに含めず、実行環境だけから取得する."""
    value = os.getenv("REVIEW_LLM_API_KEY", "").strip()
    if not value:
        raise InvalidInputError(Messages.BENCH_KEY)
    return value


def _env(**updates: str) -> dict[str, str]:
    """ツールごとに許可した評価設定だけを追加する."""
    clean = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OCR_", "CODEX_", "JUDGE_", "REVIEW_", "OPENAI_", "ANTHROPIC_"))
    }
    return {**clean, **updates}


def review_settings(model: str, gateway: str, repeat: int) -> dict[str, object]:
    """SICR の実行条件と試行ごとに独立した保存先を定義する."""
    return {
        "schema_version": 1,
        "project_id": "aacr-benchmark",
        "database": {"path": f".review/benchmark-{repeat}.db"},
        "llm": {
            "provider": "ai_gateway",
            "base_url": gateway,
            "model": model,
            "timeout_seconds": 60,
            "max_retries": 2,
        },
        "review": {
            "rules_directory": ".review/benchmark-rules",
            "max_workers": 1,
            "context_tools_enabled": False,
            "reflection_enabled": False,
        },
        "memory": {"is_injection_enabled": False},
        "security": {"allow_external_source": True},
    }


class _ToolCall:
    """一つの外部実行と、一時 JSON 出力の寿命を管理する."""

    def __init__(
        self, tools: BenchTools, tool: Tool, work: Workspace, id_value: str, repeat: int
    ) -> None:
        """レビュー入力、実行ファイルと変換対象を決める."""
        self.output: Path | None = work.sicr.parent / "review-result.json"
        model, gateway = tools.manifest.model, tools.manifest.gateway
        if tool == "sicr":
            config = work.sicr.parent / "sicr.json"
            config.write_text(json.dumps(review_settings(model, gateway, repeat)), encoding="utf-8")
            self.args = [
                sys.executable,
                "-m",
                "self_improving_code_review.cli.main",
                "review",
                "--repository",
                str(work.sicr),
                "--config",
                str(config),
                "--mode",
                "rules",
                "--range",
                work.base,
                work.head,
                "--output",
                str(self.output),
            ]
            self.env = _env(REVIEW_LLM_API_KEY=_key())
        elif tool == "ocr":
            self.args = [
                str(tools.ocr),
                "review",
                "--repo",
                str(work.external),
                "--from",
                work.base,
                "--to",
                work.head,
                "--model",
                model,
                "--concurrency",
                "1",
                "--effort",
                "medium",
                "--format",
                "json",
                "--output",
                str(self.output),
                "--timeout",
                "3",
            ]
            self.env = _env(
                OCR_NO_UPDATE="1",
                OCR_ENABLE_TELEMETRY="0",
                OCR_LLM_PROTOCOL="openai",
                OCR_USE_ANTHROPIC="false",
                OCR_LLM_TOKEN=_key(),
                OCR_LLM_MODEL=model,
                OCR_LLM_URL=gateway + "/chat/completions",
            )
        else:
            self.output = None
            self.args = [
                str(tools.evaluator_python),
                str(_BRIDGE),
                str(tools.upstream),
                "codex",
                str(work.external),
                work.base,
                work.head,
                id_value,
            ]
            self.env = _env(
                CODEX_API_KEY=_key(),
                CODEX_MODEL=model,
                CODEX_GATEWAY_URL=gateway,
                PATH=str(tools.codex.parent) + os.pathsep + os.environ.get("PATH", ""),
            )

    def __enter__(self) -> _ToolCall:
        """実行条件を渡す."""
        return self

    def __exit__(self, *_: object) -> None:
        """ベンダーの一時出力を残さない."""
        if self.output is not None:
            self.output.unlink(missing_ok=True)
