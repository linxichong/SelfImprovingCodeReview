"""正常・読取失敗・復号失敗で応答を解放する HTTP 対照例."""

from urllib.request import Request, urlopen


def fetch_catalog() -> bytes:
    """固定カタログを取得し、全ての終了経路で応答を閉じる."""
    with urlopen("https://catalog.example.invalid/version", timeout=5) as response:
        return response.read()


def fetch_metadata() -> str:
    """メタデータの読取または UTF-8 復号が失敗しても応答を閉じる."""
    with urlopen("https://metadata.example.invalid/document", timeout=5) as response:
        return response.read().decode("utf-8")


def fetch_version() -> bytes:
    """Request オブジェクト経由でも応答の所有権を明確に管理する."""
    request = Request("https://version.example.invalid/current", method="GET")
    with urlopen(request, timeout=5) as response:
        return response.read()


def fetch_status() -> bytes:
    """固定状態情報の応答を例外経路も含めて閉じる."""
    with urlopen("https://status.example.invalid/health", timeout=5) as response:
        return response.read()
