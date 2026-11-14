from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from self_improving_code_review.domain.common.hashing import hash_text
from self_improving_code_review.infrastructure.git.repository_identity import (
    RepositoryIdentity,
    RepositoryIdentityResolver,
)


@pytest.mark.case("UT-M5-001")
@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        (
            "https://github.com/owner/repository.git",
            RepositoryIdentity("github", "owner/repository"),
        ),
        (
            "git@github.com:owner/repository.git",
            RepositoryIdentity("github", "owner/repository"),
        ),
        (
            "ssh://git@git.example.com/team/repository.git",
            RepositoryIdentity("git", "git.example.com/team/repository"),
        ),
    ],
)
def test_repository_identity_resolves_supported_remote_forms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remote: str,
    expected: RepositoryIdentity,
) -> None:
    resolver = RepositoryIdentityResolver()
    monkeypatch.setattr(resolver, "_origin_url", lambda _repository: remote)

    assert resolver.resolve(tmp_path) == expected


@pytest.mark.case("UT-M5-001")
def test_repository_identity_hashes_local_path_without_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = RepositoryIdentityResolver()
    monkeypatch.setattr(resolver, "_origin_url", lambda _repository: None)

    identity = resolver.resolve(tmp_path)

    assert identity == RepositoryIdentity(
        "local",
        f"local:{hash_text(tmp_path.resolve().as_posix())}",
    )


@pytest.mark.case("UT-M5-001")
def test_repository_identity_does_not_expose_malformed_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = RepositoryIdentityResolver()
    monkeypatch.setattr(resolver, "_origin_url", lambda _repository: "malformed secret remote")

    identity = resolver.resolve(tmp_path)

    assert identity.provider == "git"
    assert identity.external_id == f"unknown/{hash_text('malformed secret remote')}"
    assert "secret" not in identity.external_id


@pytest.mark.case("UT-M5-001")
@pytest.mark.parametrize("returncode", [0, 1])
def test_origin_url_handles_present_and_missing_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
) -> None:
    completed = subprocess.CompletedProcess(
        args=["git"],
        returncode=returncode,
        stdout="https://github.com/owner/repository.git\n",
        stderr="",
    )
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: completed)

    value = RepositoryIdentityResolver()._origin_url(tmp_path)

    assert value == ("https://github.com/owner/repository.git" if returncode == 0 else None)


@pytest.mark.case("UT-M5-001")
def test_origin_url_treats_execution_failure_as_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(subprocess, "run", fail)

    assert RepositoryIdentityResolver()._origin_url(tmp_path) is None
