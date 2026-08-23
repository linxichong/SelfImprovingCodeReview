import json
import unittest

from app.review.cli import main


class CliTests(unittest.TestCase):
    def test_cli_mock_diff_file(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            diff = temp / "change.diff"
            rules = temp / "rules.md"
            output = temp / "review.json"
            diff.write_text(
                "diff --git a/app/service.py b/app/service.py\n"
                "--- a/app/service.py\n"
                "+++ b/app/service.py\n"
                "@@ -1 +1,2 @@\n"
                "+TODO: finish this\n",
                encoding="utf-8",
            )
            rules.write_text("Prefer actionable comments.", encoding="utf-8")

            code = main(["--diff-file", str(diff), "--rules", str(rules), "--output", str(output), "--mock"])

            self.assertEqual(code, 0)
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(data["metadata"]["mode"], "mock")
            self.assertEqual(data["reviews"][0]["file"], "app/service.py")
            self.assertEqual(data["reviews"][0]["category"], "maintainability")


if __name__ == "__main__":
    unittest.main()