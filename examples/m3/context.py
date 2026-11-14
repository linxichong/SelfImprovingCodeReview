"""M3 ローカル確認で追加取得する安全なコンテキスト."""


def has_user(value: object | None) -> bool:
    """利用者オブジェクトが存在するかを返す."""
    return value is not None
