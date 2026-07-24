"""Unit tests for the resolve_query_window helper in common.py."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from mcp_controller.bng.common import resolve_query_window
from mcp_controller.bng.types import QueryWindow
from mcp_controller.core.types import ErrorResponse

DEVICE = "test-ns/bng1"


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


async def test_no_start_time_window_ends_near_now():
    before = datetime.now(tz=timezone.utc)
    window = resolve_query_window("1h", "1m", DEVICE)
    after = datetime.now(tz=timezone.utc)

    assert isinstance(window, QueryWindow)
    assert window.prom_interval == "1h"
    assert window.prom_step == "1m"
    assert before <= window.end_dt <= after
    assert abs((window.end_dt - window.start_dt) - timedelta(hours=1)) < timedelta(seconds=1)


async def test_explicit_start_time_sets_correct_range():
    start_iso = "2026-01-01T00:00:00+00:00"
    window = resolve_query_window("30m", "15s", DEVICE, start_time=start_iso)

    expected_start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    expected_end = expected_start + timedelta(minutes=30)

    assert window.start_dt == expected_start
    assert window.end_dt == expected_end
    assert window.prom_interval == "30m"
    assert window.prom_step == "15s"


async def test_human_friendly_duration_normalised():
    window = resolve_query_window("2hours", "5min", DEVICE)
    assert window.prom_interval == "2h"
    assert window.prom_step == "5m"
    assert abs((window.end_dt - window.start_dt) - timedelta(hours=2)) < timedelta(seconds=1)


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


async def test_invalid_interval_raises_error_response():
    with pytest.raises(ErrorResponse) as exc_info:
        resolve_query_window("badinterval", "1m", DEVICE)
    err = exc_info.value
    assert err.error == "invalid_interval"
    assert "badinterval" in err.detail


async def test_invalid_step_raises_error_response():
    with pytest.raises(ErrorResponse) as exc_info:
        resolve_query_window("1h", "badstep", DEVICE)
    err = exc_info.value
    assert err.error == "invalid_step"
    assert "badstep" in err.detail


async def test_invalid_start_time_raises_error_response():
    with pytest.raises(ErrorResponse) as exc_info:
        resolve_query_window("1h", "1m", DEVICE, start_time="not-a-date")
    err = exc_info.value
    assert err.error == "invalid_start_time"
    assert "not-a-date" in err.detail


async def test_future_start_time_raises_error_response():
    future = "2099-01-01T00:00:00+00:00"
    with pytest.raises(ErrorResponse) as exc_info:
        resolve_query_window("1h", "1m", DEVICE, start_time=future)
    err = exc_info.value
    assert err.error == "invalid_start_time"


async def test_naive_start_time_raises_error_response():
    with pytest.raises(ErrorResponse) as exc_info:
        resolve_query_window("1h", "1m", DEVICE, start_time="2026-01-01T00:00:00")
    err = exc_info.value
    assert err.error == "invalid_start_time"
