from __future__ import annotations

from datetime import UTC

import pytest

from self_improving_code_review.infrastructure.system import SystemClock, Uuid4IdGenerator


@pytest.mark.case("UT-M1-003")
def test_system_clock_returns_timezone_aware_utc_time() -> None:
    now = SystemClock().now_utc()

    assert now.tzinfo is not None
    assert now.utcoffset() == UTC.utcoffset(None)


@pytest.mark.case("UT-M1-003")
def test_id_generator_produces_distinct_valid_run_ids() -> None:
    generator = Uuid4IdGenerator()

    first = generator.new_run_id()
    second = generator.new_run_id()

    assert str(first) != str(second)
    assert str(first)[14] == "4"
