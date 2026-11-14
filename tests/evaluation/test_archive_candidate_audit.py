"""版10候補の三つの展開関数を POSIX の通常系と既知境界で横断確認する。"""

from __future__ import annotations

import io
import json
import os
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="候補の POSIX 展開契約を検証する")


def _module(path: str) -> ModuleType:
    """管理された版10の凍結予定コードを外部通信なしで読み込む。"""
    catalog = json.loads(Path("datasets/evaluation/test_v10/catalog.json").read_text())
    source = next(case["source"] for case in catalog["cases"] if case["path"] == path)
    module = ModuleType("v10_archive_case")
    exec(compile(source, path, "exec"), module.__dict__)
    return module


def _archive(kind: str, name: str) -> zipfile.ZipFile | tarfile.TarFile:
    """試験専用の通常ファイル一件を持つメモリ内アーカイブを開く。"""
    assert kind in {"zip", "tar"}, "unsupported test archive kind"
    buffer = io.BytesIO()
    if kind == "zip":
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(name, b"marker")
        buffer.seek(0)
        return zipfile.ZipFile(buffer)
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(b"marker")
        archive.addfile(info, io.BytesIO(b"marker"))
    buffer.seek(0)
    return tarfile.open(fileobj=buffer)


@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_v10_flat_regular_file_expands(tmp_path: Path, kind: str) -> None:
    """問題を含む関数でも単純な通常ファイルの基本展開は成功することを確認する。"""
    module = _module("src/uploads/archives.py")
    with _archive(kind, "file.txt") as archive:
        getattr(module, f"expand_{kind}")(archive, tmp_path)
    assert (tmp_path / "file.txt").read_bytes() == b"marker"


@pytest.mark.parametrize("has_directory_entry", [False, True])
def test_v10_tar_creates_nested_parent(tmp_path: Path, has_directory_entry: bool) -> None:
    """TAR では親エントリの有無によらず正常な入れ子を展開できることを確認する。"""
    module = _module("src/uploads/archives.py")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        if has_directory_entry:
            directory = tarfile.TarInfo("dir/")
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o755
            archive.addfile(directory)
        member = tarfile.TarInfo("dir/file.txt")
        member.size = len(b"marker")
        archive.addfile(member, io.BytesIO(b"marker"))
    buffer.seek(0)
    with tarfile.open(fileobj=buffer) as archive:
        module.expand_tar(archive, tmp_path)
    assert (tmp_path / "dir/file.txt").read_bytes() == b"marker"


@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_v10_absolute_member_escapes_inside_test_area(tmp_path: Path, kind: str) -> None:
    """既知の包含境界違反を、試験用ディレクトリ内の絶対パスだけで再現する。"""
    module = _module("src/uploads/archives.py")
    destination = tmp_path / "destination"
    destination.mkdir()
    outside = tmp_path / "outside.txt"
    with _archive(kind, str(outside)) as archive:
        getattr(module, f"expand_{kind}")(archive, destination)
    assert outside.read_bytes() == b"marker"


@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_v10_preexisting_directory_link_escapes_inside_test_area(tmp_path: Path, kind: str) -> None:
    """既存ディレクトリリンク経由の既知包含違反を試験領域だけで確認する。"""
    module = _module("src/uploads/archives.py")
    destination = tmp_path / "destination"
    outside = tmp_path / "outside"
    destination.mkdir()
    outside.mkdir()
    (destination / "alias").symlink_to(outside, target_is_directory=True)
    with _archive(kind, "alias/file.txt") as archive:
        getattr(module, f"expand_{kind}")(archive, destination)
    assert (outside / "file.txt").read_bytes() == b"marker"


@pytest.mark.parametrize("name", ["normal.txt", "C:foo", "名前.txt"])
def test_v10_single_name_control_rejects_overwrite(tmp_path: Path, name: str) -> None:
    """POSIX の正常な単一名を許可し、同じアーカイブによる上書きを拒否する。"""
    module = _module("src/archive_store/member.py")
    with _archive("zip", name) as archive:
        module.unpack(archive, tmp_path)
        with pytest.raises(FileExistsError):
            module.unpack(archive, tmp_path)
    assert (tmp_path / name).read_bytes() == b"marker"


@pytest.mark.parametrize(
    "name", ["nested/file", "../escape", "/absolute", "..\\escape", "", ".", ".."]
)
def test_v10_single_name_control_rejects_paths(tmp_path: Path, name: str) -> None:
    """単一名専用対照が入れ子や親移動など契約外のパスを拒否する。"""
    module = _module("src/archive_store/member.py")
    with _archive("zip", name) as archive, pytest.raises(ValueError, match="invalid member"):
        module.unpack(archive, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_v10_single_name_control_rejects_existing_file_link(tmp_path: Path) -> None:
    """安全対照の既存リンクを排他作成で拒否し、リンク先を書き換えない。"""
    module = _module("src/archive_store/member.py")
    destination = tmp_path / "destination"
    destination.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"original")
    (destination / "file.txt").symlink_to(outside)
    with _archive("zip", "file.txt") as archive, pytest.raises(FileExistsError):
        module.unpack(archive, destination)
    assert outside.read_bytes() == b"original"
