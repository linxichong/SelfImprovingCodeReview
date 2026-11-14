"""test-v8 の漏標とプラットフォーム契約を訂正した未承認 test-v9 を生成する."""

from pathlib import Path

from prepare_independent_v8 import main

if __name__ == "__main__":
    raise SystemExit(main(version=9, catalog_path=Path(__file__).parent / "test_v9/catalog.json"))
