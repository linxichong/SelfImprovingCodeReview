import unittest

from app.review.models import ReviewResult


class ReviewResultTests(unittest.TestCase):
    def test_review_result_round_trip(self):
        result = ReviewResult.from_dict(
            {
                "reviews": [
                    {
                        "id": "R001",
                        "file": "app/service.py",
                        "start_line": 2,
                        "end_line": 2,
                        "category": "bug",
                        "severity": "high",
                        "title": "Example",
                        "description": "Description",
                        "evidence": "Evidence",
                        "suggestion": "Suggestion",
                        "confidence": 0.8,
                        "used_memory_ids": [],
                    }
                ],
                "metadata": {"mode": "baseline"},
            }
        )

        self.assertEqual(result.to_dict()["reviews"][0]["id"], "R001")


if __name__ == "__main__":
    unittest.main()