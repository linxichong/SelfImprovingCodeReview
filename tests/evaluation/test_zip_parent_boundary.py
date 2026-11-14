"""test-v9 と訂正候補の ZIP 親ディレクトリ欠落を外部通信なしで再現する。"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import ModuleType

import pytest


@pytest.mark.parametrize("has_directory_entry", [False, True])
@pytest.mark.parametrize("version", [9, 10])
def test_nested_zip_requires_a_preexisting_parent_directory(
    tmp_path: Path, has_directory_entry: bool, version: int
) -> None:
    """有効な入れ子 ZIP の失敗と、親を事前作成した同一入力の成功を確認する。"""
    catalog = json.loads(
        Path(f"datasets/evaluation/test_v{version}/catalog.json").read_text(encoding="utf-8")
    )
    path = "src/uploads/archives.py"
    source = next(case["source"] for case in catalog["cases"] if case["path"] == path)
    module = ModuleType("v9_archive_case")
    exec(compile(source, path, "exec"), module.__dict__)
    destination = tmp_path / "destination"
    destination.mkdir()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if has_directory_entry:
            archive.writestr("dir/", b"")
        archive.writestr("dir/file.txt", b"marker")
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive:
        assert archive.testzip() is None
        with pytest.raises(FileNotFoundError) as failure:
            module.expand_zip(archive, destination)
        assert failure.value.filename == str(destination / "dir/file.txt")
        assert list(destination.iterdir()) == []
        (destination / "dir").mkdir()
        module.expand_zip(archive, destination)
    assert (destination / "dir/file.txt").read_bytes() == b"marker"
