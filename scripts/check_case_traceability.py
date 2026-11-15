"""設計ケースと関数単位の pytest マーカーを照合し、対応表を生成する.

対応は関連証跡へのリンクであり、設計の全条件を満たす受入判定ではない。
既知欠落の免除は設けず、全設計 ID の関連付けと未知 ID の不存在を検証する。
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections.abc import Iterator
from pathlib import Path

from self_improving_code_review.messages import Messages

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs/测试用例对应表.md"
CASE_ID_PATTERN = re.compile(r"(?:UT|IT|CT|E2E)-M[1-5]-\d{3}[a-z]?")
DESIGN_ROW_PATTERN = re.compile(
    r"^\|\s*`?((?:UT|IT|CT|E2E)-M[1-5]-\d{3}[a-z]?)`?\s*\|", re.MULTILINE
)


def design_case_ids(root: Path = ROOT) -> dict[str, tuple[Path, int, str]]:
    """設計表の ID、定義行、対象を収集し、重複を拒否する."""
    found: dict[str, tuple[Path, int, str]] = {}
    for document in sorted((root / "docs").glob("详细设计书_M*.md")):
        for line_number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
            match = DESIGN_ROW_PATTERN.match(line)
            if match is None:
                continue
            case_id = match.group(1)
            if case_id in found:
                message = f"{Messages.TEST_CASE_DUPLICATE_DESIGN}: {case_id}"
                raise SystemExit(message)
            title = line.split("|")[2].strip()
            found[case_id] = (document, line_number, title)
    if not found:
        raise SystemExit(Messages.TEST_CASE_NO_DESIGN)
    return found


def _test_functions(
    body: list[ast.stmt], prefix: str = ""
) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """テストランナーが収集する関数および Test クラスのメソッドを列挙する."""
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name.startswith("test_"):
                yield prefix + node.name, node
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            yield from _test_functions(node.body, prefix + node.name + "::")


def _is_case_marker(expression: ast.expr) -> bool:
    """pytest.mark.case という完全な属性列だけを認識する."""
    return (
        isinstance(expression, ast.Attribute)
        and expression.attr == "case"
        and isinstance(expression.value, ast.Attribute)
        and expression.value.attr == "mark"
        and isinstance(expression.value.value, ast.Name)
        and expression.value.value.id == "pytest"
    )


def _case_arguments(decorator: ast.expr) -> list[str]:
    """マーカーの非空の固定文字列引数を検証して返す."""
    if _is_case_marker(decorator):
        raise SystemExit(Messages.TEST_CASE_EMPTY_MARKER)
    if not isinstance(decorator, ast.Call) or not _is_case_marker(decorator.func):
        return []
    if decorator.keywords or not decorator.args:
        raise SystemExit(Messages.TEST_CASE_ARGUMENTS)
    result: list[str] = []
    for argument in decorator.args:
        if (
            not isinstance(argument, ast.Constant)
            or not isinstance(argument.value, str)
            or CASE_ID_PATTERN.fullmatch(argument.value) is None
        ):
            raise SystemExit(Messages.TEST_CASE_INVALID_ID)
        result.append(argument.value)
    if len(result) != len(set(result)):
        raise SystemExit(Messages.TEST_CASE_DUPLICATE_ID)
    return result


def marked_case_ids(root: Path = ROOT) -> dict[str, list[tuple[str, int]]]:
    """関数マーカーから ID と pytest ノード ID、定義行の対応を返す."""
    marked: dict[str, list[tuple[str, int]]] = {}
    for path in sorted((root / "tests").rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name, node in _test_functions(tree.body):
            node_ids: set[str] = set()
            for decorator in node.decorator_list:
                node_ids.update(_case_arguments(decorator))
            for case_id in sorted(node_ids):
                node_id = f"{path.relative_to(root).as_posix()}::{name}"
                marked.setdefault(case_id, []).append((node_id, node.lineno))
    return marked


def render_report(
    designed: dict[str, tuple[Path, int, str]],
    marked: dict[str, list[tuple[str, int]]],
) -> str:
    """設計行とテスト関数へのリンクを持つ決定的な Markdown 表を作る."""
    lines = [
        "# 设计测试用例与测试函数对应表",
        "",
        "使用 scripts/check_case_traceability.py --write 生成本表。请勿手动编辑。",
        "对应关系以各函数的 @pytest.mark.case(...) 为准。文件开头的说明不参与匹配。",
        "参数化测试的各次执行共用同一 ID。一个用例的多个条件可由多个函数验证。",
        "关联关系只提供验证线索。验收时仍须逐项核对条件和运行结果。",
        "测试层级的差异及未确认条件见[对应关系补充说明](测试用例对应补充说明.md)。",
        "",
    ]
    for stage in range(1, 6):
        cases = sorted(case_id for case_id in designed if f"-M{stage}-" in case_id)
        lines.extend(
            [f"## M{stage} ({len(cases)} 项)", "", "| 设计用例 | 相关测试函数 |", "|---|---|"]
        )
        for case_id in cases:
            document, line_number, title = designed[case_id]
            definition = f"[{case_id}]({document.name}#L{line_number}): {title}"
            links: list[str] = []
            for node_id, test_line in marked[case_id]:
                path = node_id.split("::", 1)[0]
                links.append(f"[{node_id}](../{path}#L{test_line})")
            lines.append(f"| {definition} | " + "<br>".join(links) + " |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    """全 ID の対応と保存済み対応表を検証し、明示指定時だけ表を更新する."""
    parser = argparse.ArgumentParser(description="检查设计用例与测试函数的对应关系。")
    parser.add_argument("--write", action="store_true", help="重新生成对应表。")
    arguments = parser.parse_args()
    designed = design_case_ids()
    marked = marked_case_ids()
    unknown = sorted(set(marked) - set(designed))
    missing = sorted(set(designed) - set(marked))
    sys.stdout.write(
        f"设计用例: {len(designed)}, 已关联: {len(set(designed) & set(marked))}" + chr(10)
    )
    for label, items in (("未定义 ID", unknown), ("缺少关联测试的 ID", missing)):
        if items:
            joined = ", ".join(items)
            sys.stdout.write(f"{label}: {joined}" + chr(10))
    if unknown or missing:
        return 1
    report = render_report(designed, marked)
    if arguments.write:
        REPORT.write_text(report, encoding="utf-8")
    elif not REPORT.exists() or REPORT.read_text(encoding="utf-8") != report:
        sys.stdout.write("对应表缺失或已过期。请使用 --write 重新生成。" + chr(10))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
