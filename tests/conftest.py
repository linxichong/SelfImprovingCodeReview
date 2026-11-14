"""設計ケース ID を pytest の選択条件と XML 試験証跡へ付与する."""

from __future__ import annotations

import re

import pytest

from self_improving_code_review.messages import Messages

pytest_plugins = ["pytester"]

_CASE_ID = re.compile(r"(?:UT|IT|CT|E2E)-M[1-5]-\d{3}[a-z]?")


def pytest_itemcollected(item: pytest.Item) -> None:
    """各パラメーター実行へ重複のない ID を設定し、XML の properties に残す."""
    case_ids: set[str] = set()
    for marker in item.iter_markers("case"):
        if marker.kwargs or not marker.args:
            raise pytest.UsageError(Messages.TEST_CASE_ARGUMENTS)
        for case_id in marker.args:
            if not isinstance(case_id, str) or _CASE_ID.fullmatch(case_id) is None:
                raise pytest.UsageError(Messages.TEST_CASE_INVALID_ID)
            case_ids.add(case_id)
    if case_ids:
        ordered = sorted(case_ids)
        item.extra_keyword_matches.update(ordered)
        item.user_properties.append(("case_ids", ",".join(ordered)))
