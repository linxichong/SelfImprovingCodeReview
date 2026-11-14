"""test-v9 の ZIP 親ディレクトリ漏標を訂正した未承認 test-v10 を生成する."""

from pathlib import Path

from prepare_independent_v8 import main

if __name__ == "__main__":
    raise SystemExit(main(version=10, catalog_path=Path(__file__).parent / "test_v10/catalog.json"))
