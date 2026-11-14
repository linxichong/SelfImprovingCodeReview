"""独立受入の30問題、公称12安全対照と判明した漏れを外部通信なしで再現する。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import csv
import io
import json
import operator
import os
import pickle
import sqlite3
import ssl
import tarfile
import zipfile
from contextlib import closing
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from pathlib import Path
from types import ModuleType

import pytest

_CATALOG_PATH = Path("datasets/evaluation/independent_v8/catalog.json")
_V9_CATALOG_PATH = Path("datasets/evaluation/test_v9/catalog.json")


def _module(path: str, *, catalog_path: Path = _CATALOG_PATH) -> ModuleType:
    """管理下の候補コードを標準ライブラリだけで実行できる形へ読み込む。"""
    cases = json.loads(catalog_path.read_text(encoding="utf-8"))["cases"]
    source = next(case["source"] for case in cases if case["path"] == path)
    module = ModuleType("acceptance_case")
    exec(compile(source, path, "exec"), module.__dict__)
    return module


class CallablePayload:
    """復元時に副作用のない任意 callable が実行されることを示す。"""

    def __reduce__(self) -> tuple[object, tuple[int, int]]:
        """外部プロセスやファイルを使わず42を返す呼出しを埋め込む。"""
        return operator.add, (40, 2)


@pytest.mark.parametrize("symbol", ["decode_export", "decode_restore"])
def test_untrusted_pickle_can_invoke_a_callable(symbol: str) -> None:
    """未信頼データの復元が任意 callable を実行することを確認する。"""
    module = _module("src/jobs/decoders.py")
    assert getattr(module, symbol)(pickle.dumps(CallablePayload())) == 42


@pytest.mark.parametrize("symbol", ["list_invoices", "mark_paid"])
def test_sql_input_can_cross_owner_boundaries(symbol: str) -> None:
    """HTTP 入力相当の SQL 構文で別所有者の読み書きに到達する。"""
    module = _module("src/reports/selection.py")
    with closing(sqlite3.connect(":memory:")) as connection, connection:
        connection.execute("CREATE TABLE invoices(owner TEXT, paid INTEGER)")
        connection.executemany("INSERT INTO invoices VALUES (?, 0)", [("alice",), ("bob",)])
        result = getattr(module, symbol)(connection, "' OR 1=1 --")
        assert result == ([("alice",), ("bob",)] if symbol == "list_invoices" else 2)


@pytest.mark.parametrize("symbol", ["format_label", "format_filter"])
def test_shell_input_can_add_a_harmless_second_command(symbol: str) -> None:
    """既知の printf だけを使い、入力が追加コマンドとして実行されることを示す。"""
    module = _module("src/exports/processes.py")
    assert getattr(module, symbol)("; printf INJECTED") == "INJECTED"


@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_archive_members_escape_only_into_the_test_directory(tmp_path: Path, kind: str) -> None:
    """展開先外への書込みを pytest 専用一時ディレクトリ内だけで再現する。"""
    module = _module("src/uploads/archives.py")
    destination = tmp_path / "destination"
    destination.mkdir()
    buffer = io.BytesIO()
    if kind == "zip":
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../escaped.txt", b"marker")
        buffer.seek(0)
        with zipfile.ZipFile(buffer) as archive:
            module.expand_zip(archive, destination)
    else:
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo("../escaped.txt")
            info.size = 6
            archive.addfile(info, io.BytesIO(b"marker"))
        buffer.seek(0)
        with tarfile.open(fileobj=buffer) as archive:
            module.expand_tar(archive, destination)
    assert (tmp_path / "escaped.txt").read_bytes() == b"marker"


@pytest.mark.parametrize("symbol", ["download", "replace"])
def test_attachment_paths_escape_the_assigned_root(tmp_path: Path, symbol: str) -> None:
    """独立した読取・書込入口の親移動を試験領域だけで確認する。"""
    module = _module("src/attachments/access.py")
    root = tmp_path / "attachments"
    root.mkdir()
    outside = tmp_path / "other.txt"
    outside.write_bytes(b"original")
    if symbol == "download":
        assert module.download(root, "../other.txt") == b"original"
    else:
        module.replace(root, "../other.txt", b"replacement")
        assert outside.read_bytes() == b"replacement"


@pytest.mark.parametrize("symbol", ["read_record", "delete_record"])
def test_record_access_ignores_actor_ownership(symbol: str) -> None:
    """別所有者の記録をログイン利用者が読取・削除できることを確認する。"""
    module = _module("src/records/permissions.py")
    records = {"r1": ("alice", b"alice-data")}
    if symbol == "read_record":
        assert module.read_record(records, "bob", "r1") == b"alice-data"
    else:
        module.delete_record(records, "bob", "r1")
        assert records == {}


@pytest.mark.parametrize("symbol", ["minor_units", "discounted"])
def test_currency_results_violate_the_declared_units(symbol: str) -> None:
    """二つの独立した単位計算の誤りを厳密な Decimal 値で示す。"""
    module = _module("src/settlements/units.py")
    if symbol == "minor_units":
        assert module.minor_units(Decimal("12.34")) == 12  # 契約値は1234。
    else:
        assert module.discounted(Decimal("100"), 10) == Decimal("9000")  # 契約値は90。


@pytest.mark.parametrize("amount", ["1e26", "-1e26", "1e30", "-1e30"])
@pytest.mark.parametrize("catalog_path", [_CATALOG_PATH, _V9_CATALOG_PATH])
def test_money_case_raises_at_precision_boundary(amount: str, catalog_path: Path) -> None:
    """公開済み対照の桁数上限と裁定で見つかった大数例外を正負で再現する。"""
    module = _module("src/money/conversion.py", catalog_path=catalog_path)
    context = Context(prec=28, rounding=ROUND_HALF_EVEN, traps=[InvalidOperation])
    with localcontext(context), pytest.raises(InvalidOperation):
        module.cents(Decimal(amount))


@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("catalog_path", [_CATALOG_PATH, _V9_CATALOG_PATH])
def test_money_case_accepts_the_cent_below_precision_boundary(
    sign: int, catalog_path: Path
) -> None:
    """例外境界の一セント手前は28桁の精度で正確に変換できることを確認する。"""
    module = _module("src/money/conversion.py", catalog_path=catalog_path)
    amount = Decimal("99999999999999999999999999.99")
    if sign < 0:
        amount = amount.copy_negate()
    context = Context(prec=28, rounding=ROUND_HALF_EVEN, traps=[InvalidOperation])
    with localcontext(context):
        assert module.cents(amount) == sign * (10**28 - 1)


@pytest.mark.parametrize(("amount", "expected"), [("1e30", 10**32), ("-1e30", -(10**32))])
@pytest.mark.parametrize("catalog_path", [_CATALOG_PATH, _V9_CATALOG_PATH])
def test_money_case_accepts_large_values_with_sufficient_precision(
    amount: str, expected: int, catalog_path: Path
) -> None:
    """同じ大数が十分な精度では変換でき、失敗が精度依存であることを確認する。"""
    module = _module("src/money/conversion.py", catalog_path=catalog_path)
    context = Context(prec=33, rounding=ROUND_HALF_EVEN, traps=[InvalidOperation])
    with localcontext(context):
        assert module.cents(Decimal(amount)) == expected


@pytest.mark.parametrize("symbol", ["batches", "first_page"])
def test_window_boundaries_omit_required_elements(symbol: str) -> None:
    """末尾バッチ欠落と先頭要素欠落を別々に再現する。"""
    module = _module("src/search/windows.py")
    if symbol == "batches":
        assert module.batches([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4]]
    else:
        assert module.first_page([1, 2, 3], 2) == [2, 3]


@pytest.mark.parametrize("symbol", ["read_row", "write_row"])
def test_csv_quoting_is_corrupted(symbol: str) -> None:
    """引用フィールドの読取と書込の独立した破損を確認する。"""
    module = _module("src/exchange/csv_rows.py")
    if symbol == "read_row":
        assert module.read_row('"a,b",c') == ['"a', 'b"', "c"]
    else:
        output = module.write_row(["a,b", "c"])
        assert next(csv.reader([output])) == ["a", "b", "c"]


@pytest.mark.parametrize("symbol", ["request_ids", "summarize"])
def test_collection_lifetimes_violate_the_request_contract(symbol: str) -> None:
    """可変既定値共有と反復子再消費の別々の問題を再現する。"""
    module = _module("src/workflows/collections.py")
    if symbol == "request_ids":
        module.request_ids("first")
        assert module.request_ids("second") == ["first", "second"]
    else:
        assert module.summarize(iter([1, 2])) == (3, [])


def test_ttl_accepts_expired_data() -> None:
    """秒単位の TTL が1000倍に拡張されることを確認する。"""
    assert _module("src/cache/expiry.py").is_fresh(0, 5, 100)


def test_replacement_commits_deletion_before_insert_failure() -> None:
    """挿入失敗後にも削除が確定していることを実 SQLite で示す。"""
    module = _module("src/sync/replacement.py")
    with closing(sqlite3.connect(":memory:")) as connection, connection:
        connection.execute("CREATE TABLE entries(value TEXT NOT NULL UNIQUE)")
        connection.execute("INSERT INTO entries VALUES ('original')")
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            module.replace_rows(connection, [("replacement",), ("replacement",)])
        connection.rollback()
        assert connection.execute("SELECT * FROM entries").fetchall() == []


def test_serialization_failure_truncates_existing_settings(tmp_path: Path) -> None:
    """JSON 変換失敗時に旧設定が失われることを確認する。"""
    path = tmp_path / "settings.json"
    path.write_text("original", encoding="utf-8")
    with pytest.raises(TypeError):
        _module("src/preferences/persistence.py").save(path, object())
    assert path.read_text(encoding="utf-8") == ""


def test_tls_configuration_disables_both_checks() -> None:
    """ネットワークなしで証明書検証とホスト名検証の無効化を確認する。"""
    context = _module("src/connections/tls.py").client_context()
    assert context.verify_mode == ssl.CERT_NONE
    assert not context.check_hostname


def test_upload_limit_trusts_a_forged_length() -> None:
    """偽の短いヘッダーに対し実本文の上限が機能しないことを確認する。"""
    module = _module("src/ingestion/limits.py")
    assert module.read_upload(io.BytesIO(b"a" * 100), {"Content-Length": "1"}, 4) == b"a" * 100


def test_invalid_base64_is_silently_accepted() -> None:
    """拒否すべき無効文字が空データへ変換されることを確認する。"""
    assert _module("src/encoding/strict_input.py").decode_field(b"!!!!") == b""


def test_background_failure_is_reported_as_success() -> None:
    """バックグラウンド送信の失敗が成功に変わることを確認する。"""

    def fail() -> None:
        """機密情報を含まない既知の送信失敗を発生させる。"""
        raise RuntimeError

    assert _module("src/notifications/dispatch.py").dispatch(fail) is True


async def _cancel() -> object:
    """外部処理なしでキャンセルを発生させる。"""
    raise asyncio.CancelledError


def test_task_cancellation_is_swallowed() -> None:
    """呼出元へ伝播すべきキャンセルが None へ変換されることを確認する。"""
    assert asyncio.run(_module("src/scheduling/cancellation.py").execute(_cancel())) is None


def test_false_string_is_enabled() -> None:
    """文字列 false の論理値が契約に反して True になることを確認する。"""
    assert _module("src/flags/decoding.py").enabled("false") is True


def test_dictionary_cleanup_raises_during_iteration() -> None:
    """期限切れが存在すると辞書走査に失敗することを確認する。"""
    with pytest.raises(RuntimeError):
        _module("src/leases/cleanup.py").expire({"old": 1, "new": 10}, 5)


@pytest.mark.parametrize("is_symlink", [False, True])
def test_safe_archive_never_overwrites_existing_entries(tmp_path: Path, is_symlink: bool) -> None:
    """既存の通常ファイルや領域外へのリンクを上書きしないことを確認する。"""
    module = _module("src/archive_store/member.py")
    destination = tmp_path / "destination"
    destination.mkdir()
    target = tmp_path / "outside.txt" if is_symlink else destination / "item.txt"
    target.write_bytes(b"original")
    if is_symlink:
        (destination / "item.txt").symlink_to(target)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("item.txt", b"replacement")
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive, pytest.raises(FileExistsError):
        module.unpack(archive, destination)
    assert target.read_bytes() == b"original"


@pytest.mark.parametrize("name", ["../escape", "..\\escape", "/absolute", "", ".", ".."])
def test_safe_archive_rejects_non_basename_members(tmp_path: Path, name: str) -> None:
    """親移動、区切り、空名などを拒否し、ファイルを書かないことを確認する。"""
    module = _module("src/archive_store/member.py")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, b"marker")
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive, pytest.raises(ValueError, match="invalid member"):
        module.unpack(archive, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_safe_archive_writes_a_new_regular_file(tmp_path: Path) -> None:
    """正常な新規単一ファイルを作成できることを確認する。"""
    module = _module("src/archive_store/member.py")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("normal.txt", b"marker")
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive:
        module.unpack(archive, tmp_path)
    assert (tmp_path / "normal.txt").read_bytes() == b"marker"


@pytest.mark.skipif(os.name != "posix", reason="版9の ZIP 対照は POSIX 専用の契約")
def test_v9_archive_keeps_colon_names_inside_the_posix_destination(tmp_path: Path) -> None:
    """Windows 風の名前も POSIX では展開先内の単一名となることを確認する。"""
    module = _module("src/archive_store/member.py", catalog_path=_V9_CATALOG_PATH)
    destination = tmp_path / "destination"
    destination.mkdir()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("C:foo", b"marker")
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive:
        module.unpack(archive, destination)
    assert (destination / "C:foo").read_bytes() == b"marker"
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("number", range(12))
def test_safe_controls_handle_normal_and_adversarial_values(tmp_path: Path, number: int) -> None:
    """安全対照の正常値と対象境界を外部通信なしで検証する。"""
    paths = [
        case["path"]
        for case in json.loads(_CATALOG_PATH.read_text())["cases"]
        if not case["issues"]
    ]
    module = _module(paths[number])
    if number == 0:
        assert module.decode(b'{"ok":true}') == {"ok": True}
    elif number == 1:
        with closing(sqlite3.connect(":memory:")) as connection, connection:
            connection.execute("CREATE TABLE invoices(owner TEXT)")
            connection.execute("INSERT INTO invoices VALUES ('alice')")
            assert module.owners(connection, "' OR 1=1 --") == []
    elif number == 2:
        assert module.label("; printf INJECTED") == "; printf INJECTED"
    elif number == 3:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../escaped.txt", b"marker")
        buffer.seek(0)
        with zipfile.ZipFile(buffer) as archive, pytest.raises(ValueError, match="invalid member"):
            module.unpack(archive, tmp_path)
        assert list(tmp_path.iterdir()) == []
    elif number == 4:
        records = {"r1": ("alice", b"body")}
        assert module.read(records, "alice", "r1") == b"body"
        with pytest.raises(PermissionError):
            module.read(records, "bob", "r1")
    elif number == 5:
        assert module.cents(Decimal("12.345")) == 1235
    elif number == 6:
        assert module.read('"a,b",c') == ["a,b", "c"]
        assert module.read("") == []
    elif number == 7:
        assert module.split([1, 2, 3], 2) == [[1, 2], [3]]
        with pytest.raises(ValueError, match="positive size"):
            module.split([], 0)
    elif number == 8:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(module.execute(_cancel()))
    elif number == 9:
        context = module.client_context()
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname
    elif number == 10:
        assert module.decode(base64.b64encode(b"data")) == b"data"
        with pytest.raises(binascii.Error):
            module.decode(b"!!!!")
    else:
        assert module.fresh(0, 5, 4)
        assert not module.fresh(0, 5, 100)
        assert not module.fresh(10, 5, 4)
