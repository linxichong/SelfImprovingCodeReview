"""本番コードのクラスと呼び出し可能要素に関する説明規約を検証する."""

from __future__ import annotations

import ast
import re
from pathlib import Path


def test_production_classes_and_callables_have_docstrings() -> None:
    """新しいクラス、メソッド、関数に用途説明があることを確認する."""
    source_root = Path("src/self_improving_code_review")
    missing: list[str] = []
    missing_japanese: list[str] = []
    japanese_text = re.compile(r"[ぁ-んァ-ヶ一-龠]")

    for path in sorted(source_root.rglob("*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(module):
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            docstring = ast.get_docstring(node, clean=False)
            relative_path = path.relative_to(source_root)
            location = f"{relative_path}:{node.lineno}:{node.name}"
            if docstring is None:
                missing.append(location)
            elif japanese_text.search(docstring) is None:
                missing_japanese.append(location)

    assert missing == [], "用途説明のないクラスまたは呼び出し可能要素: " + ", ".join(missing)
    assert missing_japanese == [], (
        "日本語の用途説明がないクラスまたは呼び出し可能要素: " + ", ".join(missing_japanese)
    )
