"""安全境界の独立開発例を実 LLM・ネットワーク・追加 SDK なしで検証する。"""

from __future__ import annotations

import ast
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from types import ModuleType

import pytest

from self_improving_code_review.domain.change.diff_parser import UnifiedDiffParser
from self_improving_code_review.domain.common.enums import ValidationStatus
from self_improving_code_review.domain.finding.validators import EvidenceValidator
from self_improving_code_review.security.models import ScanProfile, SourceKind, SourcePayload
from self_improving_code_review.security.secret_scanner import DetectSecretsScanner

_ROOT = Path("datasets/development/security_boundaries")
_FAMILIES = ("jwt", "flask", "xml", "xss")
_VARIANTS = ("vulnerable", "safe", "context")


class SignatureRejectedError(ValueError):
    """模擬検証器による署名拒否を表す。"""


def _source(case_id: str) -> str:
    """ローカルの差分から追加された Python 本文だけを取り出す。"""
    patch = (_ROOT / "patches" / f"{case_id}.patch").read_text(encoding="utf-8")
    files = UnifiedDiffParser().parse(patch).files
    assert len(files) == 1
    return "\n".join(
        line.text for hunk in files[0].hunks for line in hunk.lines if line.new_line is not None
    )


def _load_example(
    case_id: str, monkeypatch: pytest.MonkeyPatch, dependencies: dict[str, ModuleType]
) -> ModuleType:
    """SDK を観測用模擬に差し替え、管理下の開発例を実行する。"""
    for name, dependency in dependencies.items():
        monkeypatch.setitem(sys.modules, name, dependency)
    module = ModuleType(case_id)
    exec(compile(_source(case_id), f"{case_id}.py", "exec"), module.__dict__)
    return module


@pytest.mark.smoke
def test_security_matrix_covers_three_outcomes_without_formal_approval() -> None:
    """四分野の三分類、二つの独立位置と不足情報を固定する。"""
    manifest = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest) == {"schema_version", "split", "approval_status", "cases"}
    assert manifest["schema_version"] == 1
    assert manifest["split"] == "development"
    assert manifest["approval_status"] == "AI_AUTHORED_DEVELOPMENT_ONLY"
    cases = manifest["cases"]
    assert len(cases) == 12
    assert {case["case_id"] for case in cases} == {
        f"{family}-{variant}" for family in _FAMILIES for variant in _VARIANTS
    }
    assert {case["patch"] for case in cases} == {
        f"patches/{path.name}" for path in (_ROOT / "patches").glob("*.patch")
    }
    for case in cases:
        assert set(case) == {
            "case_id",
            "family",
            "variant",
            "patch",
            "expected_outcome",
            "expected_findings",
            "context_questions",
        }
        source = _source(case["case_id"])
        functions = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)]
        assert len(functions) == 2
        symbols = {node.name for node in functions}
        assert len(symbols) == 2
        if case["variant"] == "vulnerable":
            assert case["expected_outcome"] == "findings"
            assert {finding["symbol"] for finding in case["expected_findings"]} == symbols
            assert len(case["expected_findings"]) == 2
            assert not case["context_questions"]
        elif case["variant"] == "safe":
            assert case["expected_outcome"] == "no-target-findings"
            assert not case["expected_findings"]
            assert not case["context_questions"]
        else:
            assert case["expected_outcome"] == "needs-context"
            assert not case["expected_findings"]
            assert {question["symbol"] for question in case["context_questions"]} == symbols
            assert all(question["question"] for question in case["context_questions"])


@pytest.mark.smoke
@pytest.mark.parametrize("family", _FAMILIES)
@pytest.mark.parametrize("variant", _VARIANTS)
def test_security_patch_positions_and_secret_boundary(family: str, variant: str) -> None:
    """差分行数、位置、関数所属と送信前秘密検査を決定的に検証する。"""
    case_id = f"{family}-{variant}"
    manifest = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))
    case = next(case for case in manifest["cases"] if case["case_id"] == case_id)
    patch = (_ROOT / case["patch"]).read_text(encoding="utf-8")
    changed_file = UnifiedDiffParser().parse(patch).files[0]
    assert (
        changed_file.new_path.value
        == {
            "jwt": "src/billing/permissions.py",
            "flask": "src/service/entrypoints.py",
            "xml": "src/imports/documents.py",
            "xss": "src/pages/fragments.py",
        }[family]
    )
    source = _source(case_id)
    functions = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)]
    for hunk in changed_file.hunks:
        assert hunk.old_count == 0
        assert sum(line.new_line is not None for line in hunk.lines) == hunk.new_count
    for expected in case["expected_findings"]:
        assert expected["file"] == changed_file.new_path.value
        line = expected["start_line"]
        assert line in changed_file.added_line_numbers
        assert source.splitlines()[line - 1] == expected["evidence_quote"]
        function = next(node for node in functions if node.name == expected["symbol"])
        assert function.end_lineno is not None
        assert function.lineno <= line <= function.end_lineno
    scan = DetectSecretsScanner().scan(
        SourcePayload(kind=SourceKind.DIFF, label=case_id, text=patch), profile=ScanProfile.STRICT
    )
    assert scan.is_passed


@pytest.mark.parametrize("family", _FAMILIES)
def test_repeated_evidence_requires_distinguishable_contiguous_source(family: str) -> None:
    """反復コードの短い引用を拒否し、関数を含む連続原文なら位置を確定する。"""
    case_id = f"{family}-vulnerable"
    manifest = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))
    case = next(case for case in manifest["cases"] if case["case_id"] == case_id)
    patch = (_ROOT / case["patch"]).read_text(encoding="utf-8")
    changed_file = UnifiedDiffParser().parse(patch).files[0]
    source = _source(case_id)
    functions = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)]
    for target, function in zip(case["expected_findings"], functions, strict=True):
        short = EvidenceValidator().validate(target["evidence_quote"], changed_file)
        assert short.status is (
            ValidationStatus.VALID if family == "flask" else ValidationStatus.INVALID
        )
        if family != "flask":
            assert short.reason_code == "EVIDENCE_AMBIGUOUS"
        assert function.end_lineno is not None
        quote = "\n".join(source.splitlines()[function.lineno - 1 : function.end_lineno])
        scoped = EvidenceValidator().validate(quote, changed_file)
        assert scoped.status is ValidationStatus.VALID
        assert scoped.line_range is not None
        assert scoped.line_range.start_line <= target["start_line"] <= scoped.line_range.end_line


@pytest.mark.parametrize("variant", ["vulnerable", "safe"])
@pytest.mark.parametrize(
    ("symbol", "role"), [("can_download_invoice", "billing"), ("can_close_ledger", "finance")]
)
def test_jwt_authorization_uses_declared_verification_contract(
    monkeypatch: pytest.MonkeyPatch, variant: str, symbol: str, role: str
) -> None:
    """署名拒否を模擬し、権限判定への到達と検証設定の違いを観測する。"""
    jwt = ModuleType("jwt")
    calls: list[dict[str, object]] = []

    def decode(token: str, verification_key: str, **options: object) -> dict[str, str]:
        """署名検証を要求した場合の検証失敗を模擬する。"""
        assert verification_key == "trusted-public-key-fixture"
        calls.append(options)
        if token == "tampered" and options["options"] == {"verify_signature": True}:
            raise SignatureRejectedError
        return {"role": role}

    monkeypatch.setattr(jwt, "decode", decode, raising=False)
    module = _load_example(f"jwt-{variant}", monkeypatch, {"jwt": jwt})
    authorize = getattr(module, symbol)
    assert authorize("valid", "trusted-public-key-fixture") is True
    if variant == "safe":
        with pytest.raises(SignatureRejectedError):
            authorize("tampered", "trusted-public-key-fixture")
    else:
        assert authorize("tampered", "trusted-public-key-fixture") is True
    assert len(calls) == 2
    for call in calls:
        assert call == {
            "algorithms": ["RS256"],
            "audience": "billing-api",
            "issuer": "https://identity.example.invalid",
            "options": {"verify_signature": variant == "safe"},
        }


class RecordingApp:
    """サーバーを起動せず、起動時の安全設定だけを記録する。"""

    def __init__(self) -> None:
        """呼出記録を初期化する。"""
        self.calls: list[dict[str, object]] = []

    def run(self, **options: object) -> None:
        """バインドや通信を行わず引数を保持する。"""
        self.calls.append(options)


@pytest.mark.parametrize("variant", ["vulnerable", "safe"])
def test_flask_examples_do_not_start_a_server(
    monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    """二つの起動口でデバッグと公開範囲の設定を確認する。"""
    flask = ModuleType("flask")
    monkeypatch.setattr(flask, "Flask", RecordingApp, raising=False)
    module = _load_example(f"flask-{variant}", monkeypatch, {"flask": flask})
    app = RecordingApp()
    module.serve_accounts(app)
    module.serve_exports(app)
    assert app.calls == [
        {
            "host": "127.0.0.1" if variant == "safe" else "::",
            "port": port,
            "debug": variant == "vulnerable",
            "use_reloader": False,
        }
        for port in (8091, 8092)
    ]


@pytest.mark.parametrize("variant", ["vulnerable", "safe"])
def test_xml_examples_pass_security_settings_to_the_parser(
    monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    """実体解決を実行せず、二つの入力経路のパーサー所有と設定を確認する。"""
    etree = ModuleType("lxml.etree")
    lxml = ModuleType("lxml")
    calls: list[dict[str, bool]] = []
    parser = object()

    def make_parser(**options: bool) -> object:
        """解析器を作らず指定された安全設定を記録する。"""
        calls.append(options)
        return parser

    def parse(document: bytes, selected_parser: object) -> bytes:
        """解析器が入力経路へ渡されたことだけを観測する。"""
        assert selected_parser is parser
        return document

    monkeypatch.setattr(etree, "XMLParser", make_parser, raising=False)
    monkeypatch.setattr(etree, "fromstring", parse, raising=False)
    monkeypatch.setattr(lxml, "etree", etree, raising=False)
    module = _load_example(f"xml-{variant}", monkeypatch, {"lxml": lxml})
    for symbol in ("parse_upload", "parse_attachment"):
        assert getattr(module, symbol)(b"<record/>") == b"<record/>"
    assert (
        calls
        == [
            {
                "resolve_entities": variant == "vulnerable",
                "load_dtd": variant == "vulnerable",
                "no_network": variant == "safe",
            }
        ]
        * 2
    )


class HtmlTags(HTMLParser):
    """HTML として有効な開始タグだけを観測する。"""

    def __init__(self) -> None:
        """解析器と観測記録を初期化する。"""
        super().__init__()
        self.tags: list[str] = []

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        """有効な要素名を記録する。"""
        self.tags.append(tag)


@pytest.mark.parametrize("variant", ["vulnerable", "safe"])
@pytest.mark.parametrize("symbol", ["render_notice", "render_caption"])
@pytest.mark.parametrize("value", ['<img src=x onerror="alert(1)">', "text & < > '"])
def test_xss_examples_preserve_text_or_expose_unescaped_markup(
    monkeypatch: pytest.MonkeyPatch, variant: str, symbol: str, value: str
) -> None:
    """標準 HTML 解析で、独立した二関数の入力がタグ化するかを確認する。"""
    markupsafe = ModuleType("markupsafe")
    monkeypatch.setattr(markupsafe, "Markup", str, raising=False)
    module = _load_example(f"xss-{variant}", monkeypatch, {"markupsafe": markupsafe})
    rendered = getattr(module, symbol)(value)
    parser = HtmlTags()
    parser.feed(rendered)
    assert parser.tags == (
        ["section", "img"] if variant == "vulnerable" and "<img" in value else ["section"]
    )
    if variant == "safe":
        assert value not in rendered
        assert "&lt;" in rendered
