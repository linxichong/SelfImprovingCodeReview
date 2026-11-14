"""設計 ID の決定的照合と pytest XML への関連証跡記録を検証する."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check_case_traceability.py"


def test_repository_case_traceability_is_current() -> None:
    """通常の品質ゲートで未対応 ID、未知 ID、古い対応表を拒否する."""
    result = subprocess.run(
        [sys.executable, str(CHECKER)], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def sample_repository(tmp_path: Path) -> Path:
    """本番リポジトリを変更せず小さな設計とテストの照合環境を作る."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / CHECKER.name).write_text(CHECKER.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs/详细设计书_M1_测试.md").write_text(
        "| UT-M1-001 | 設定 | 境界 | 拒否 |\n", encoding="utf-8"
    )
    (tmp_path / "tests/test_sample.py").write_text(
        'import pytest\n@pytest.mark.case("UT-M1-001")\ndef test_sample(): pass\n',
        encoding="utf-8",
    )
    return tmp_path


def _run_checker(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """隔離したリポジトリでネットワークなしに検査コマンドを実行する."""
    return subprocess.run(
        [sys.executable, str(root / "scripts" / CHECKER.name), *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_checker_generates_stable_report_and_rejects_stale_report(sample_repository: Path) -> None:
    """明示生成、再生成の安定性、未生成および陳腐化の拒否を確認する."""
    assert _run_checker(sample_repository).returncode == 1
    assert _run_checker(sample_repository, "--write").returncode == 0
    report = sample_repository / "docs/测试用例对应表.md"
    original = report.read_text(encoding="utf-8")
    assert "test_sample.py::test_sample" in original
    assert _run_checker(sample_repository).returncode == 0
    assert _run_checker(sample_repository, "--write").returncode == 0
    assert report.read_text(encoding="utf-8") == original
    report.write_text("stale", encoding="utf-8")
    assert _run_checker(sample_repository).returncode == 1


@pytest.mark.parametrize(
    "source",
    [
        "def test_sample(): pass\n",
        'import pytest\n@pytest.mark.case("UT-M1-999")\ndef test_sample(): pass\n',
        "import pytest\n@pytest.mark.case()\ndef test_sample(): pass\n",
        "import pytest\n@pytest.mark.case\ndef test_sample(): pass\n",
        "import pytest\n@pytest.mark.case(42)\ndef test_sample(): pass\n",
        'import pytest\n@pytest.mark.case("invalid")\ndef test_sample(): pass\n',
        'import pytest\n@pytest.mark.case(id="UT-M1-001")\ndef test_sample(): pass\n',
        'import pytest\n@pytest.mark.case("UT-M1-001","UT-M1-001")\ndef test_sample(): pass\n',
        '@other.case("UT-M1-001")\ndef test_sample(): pass\n',
        'import pytest\n@pytest.mark.case("UT-M1-001")\ndef helper(): pass\n',
    ],
)
def test_checker_rejects_missing_unknown_and_invalid_markers(
    sample_repository: Path, source: str
) -> None:
    """コメント、類似デコレーター、不正引数で対応を偽装できないことを確認する."""
    (sample_repository / "tests/test_sample.py").write_text(source, encoding="utf-8")
    assert _run_checker(sample_repository, "--write").returncode != 0
    assert not (sample_repository / "docs/测试用例对应表.md").exists()


def test_checker_rejects_duplicate_and_empty_design(sample_repository: Path) -> None:
    """重複 ID と設計表の消失を成功扱いにしない."""
    document = sample_repository / "docs/详细设计书_M1_测试.md"
    document.write_text("| UT-M1-001 | A |\n| UT-M1-001 | B |\n", encoding="utf-8")
    assert _run_checker(sample_repository, "--write").returncode != 0
    document.write_text("no cases", encoding="utf-8")
    assert _run_checker(sample_repository, "--write").returncode != 0


def test_checker_collects_test_class_and_suffix_id(sample_repository: Path) -> None:
    """Test クラスのメソッド、非同期関数、枝番号を正しく関連付ける."""
    (sample_repository / "docs/详细设计书_M1_测试.md").write_text(
        "| IT-M1-003a | 入力元 |\n", encoding="utf-8"
    )
    (sample_repository / "tests/test_sample.py").write_text(
        "import pytest\nclass TestInputs:\n"
        '    @pytest.mark.case("IT-M1-003a")\n    async def test_source(self): pass\n',
        encoding="utf-8",
    )
    assert _run_checker(sample_repository, "--write").returncode == 0
    report = (sample_repository / "docs/测试用例对应表.md").read_text(encoding="utf-8")
    assert "test_sample.py::TestInputs::test_source" in report


def test_case_ids_select_parameter_cases_and_survive_junit_xml(pytester: pytest.Pytester) -> None:
    """番号選択と複数 ID の XML 保存を実際の pytest 実行で確認する."""
    pytester.makeconftest((ROOT / "tests/conftest.py").read_text(encoding="utf-8"))
    pytester.makeini("[pytest]\nmarkers = case(*ids): design case identifiers\n")
    pytester.makepyfile(
        'import pytest\n@pytest.mark.case("IT-M3-006","E2E-M3-002")\n'
        '@pytest.mark.parametrize("value", [1, 2])\n'
        "def test_resumes(value): assert value > 0\n"
        "def test_unrelated(): pass\n"
    )
    result = pytester.runpytest_subprocess("-k", "IT-M3-006", "--junitxml=result.xml")
    result.assert_outcomes(passed=2, deselected=1)
    document = ElementTree.parse(pytester.path / "result.xml")
    cases = document.findall(".//testcase")
    assert len(cases) == 2
    for case in cases:
        properties = case.findall("./properties/property")
        assert [(p.get("name"), p.get("value")) for p in properties] == [
            ("case_ids", "E2E-M3-002,IT-M3-006")
        ]
        assert "[" in case.attrib["name"]


@pytest.mark.parametrize(
    "marker",
    [
        "pytest.mark.case()",
        "pytest.mark.case(42)",
        'pytest.mark.case("invalid")',
        'pytest.mark.case(id="UT-M1-001")',
    ],
)
def test_xml_hook_rejects_invalid_case_markers(pytester: pytest.Pytester, marker: str) -> None:
    """実収集でも不正マーカーを拒否して偽の XML 証跡を作らない."""
    pytester.makeconftest((ROOT / "tests/conftest.py").read_text(encoding="utf-8"))
    pytester.makeini("[pytest]\nmarkers = case(*ids): design case identifiers\n")
    pytester.makepyfile(f"import pytest\n@{marker}\ndef test_sample(): pass\n")
    result = pytester.runpytest_subprocess()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
