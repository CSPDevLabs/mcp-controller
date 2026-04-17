"""Integration test: MCP server serves pre-recorded mock data in replay mode.

This test pre-populates mock data files for two handlers (`bng_manifest` and
`bng_targets_by_label`), creates the server with `Settings(mock=True)`, and
verifies that the handlers return the recorded data without touching any real
backend.
"""

import json

import pytest

from mcp_controller.config import Settings
from mcp_controller.core.mock import MockDataNotFoundError, mock_intercept


# ---------------------------------------------------------------------------
# Fixture: redirect _data_dir to a temp path for isolation
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_data_dir(tmp_path, monkeypatch):
    """Point the mock module's _data_dir() at a fresh temp directory."""
    monkeypatch.setattr("mcp_controller.core.mock._data_dir", lambda override=None: tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _write_mock(data_dir, func_name: str, args: dict, mock_data):
    entry = {"args": args, "response_type": type(mock_data).__name__, "mock_data": mock_data}
    path = data_dir / f"{func_name}.json"
    existing = json.loads(path.read_text()) if path.exists() else []
    existing.append(entry)
    path.write_text(json.dumps(existing, indent=2))


# ---------------------------------------------------------------------------
# Integration: resource handler in replay mode returns mock data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resource_handler_serves_mock_data(mock_data_dir):
    """A zero-arg resource handler returns mock data without backend calls."""
    recorded_payload = json.dumps({"mock": True, "targets": []}, indent=2)
    _write_mock(mock_data_dir, "bng_targets", {}, recorded_payload)

    settings = Settings(mock=True)
    real_called = False

    async def bng_targets() -> str:
        nonlocal real_called
        real_called = True
        return "real_data"

    wrapped = mock_intercept(settings)(bng_targets)
    result = await wrapped()

    assert result == recorded_payload
    assert not real_called


# ---------------------------------------------------------------------------
# Integration: tool handler with args in replay mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_handler_with_args_serves_mock_data(mock_data_dir):
    """A tool handler returns the pre-recorded entry matching its args."""
    recorded = {"device": "bng1", "avg_cpu_usage": 14.2}
    _write_mock(
        mock_data_dir,
        "bng_device_cpu_usage",
        {"device": "nok/bng1", "interval": "1h", "step": "60s", "start_time": ""},
        recorded,
    )

    settings = Settings(mock=True)

    async def bng_device_cpu_usage(device: str, interval: str, step: str, start_time: str = ""):
        return {"device": device, "avg_cpu_usage": 0.0}

    wrapped = mock_intercept(settings)(bng_device_cpu_usage)
    result = await wrapped(
        device="nok/bng1", interval="1h", step="60s"
    )

    assert result == recorded


# ---------------------------------------------------------------------------
# Integration: replay raises when no matching args
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_raises_for_unrecorded_args(mock_data_dir):
    """Replay mode raises MockDataNotFoundError for args not in the file."""
    _write_mock(mock_data_dir, "bng_device_cpu_usage", {"device": "bng1"}, "data")

    settings = Settings(mock=True)

    async def bng_device_cpu_usage(device: str):
        return "real"

    wrapped = mock_intercept(settings)(bng_device_cpu_usage)

    with pytest.raises(MockDataNotFoundError) as exc_info:
        await wrapped(device="bng_unknown")

    assert "bng_device_cpu_usage" in str(exc_info.value)
    assert "bng_unknown" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Integration: record mode saves data and result is returned unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_mode_saves_and_returns_result(mock_data_dir):
    """Record mode saves the result to disk and returns it to the caller."""
    settings = Settings(mock_data_record=True)
    call_count = 0

    async def bng_namespaces() -> str:
        nonlocal call_count
        call_count += 1
        return '["nok-bng", "nok-segw"]'

    wrapped = mock_intercept(settings)(bng_namespaces)
    result = await wrapped()

    assert result == '["nok-bng", "nok-segw"]'
    assert call_count == 1

    saved = json.loads((mock_data_dir / "bng_namespaces.json").read_text())
    assert len(saved) == 1
    assert saved[0]["mock_data"] == '["nok-bng", "nok-segw"]'
    assert saved[0]["args"] == {}


# ---------------------------------------------------------------------------
# Integration: record mode upserts on repeated calls with same args
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_mode_upserts_on_repeated_call(mock_data_dir):
    """Calling a recorded handler twice with the same args produces one entry."""
    settings = Settings(mock_data_record=True)
    counter = 0

    async def my_tool(x: int) -> str:
        nonlocal counter
        counter += 1
        return f"result_{counter}"

    wrapped = mock_intercept(settings)(my_tool)
    await wrapped(x=1)
    await wrapped(x=1)

    saved = json.loads((mock_data_dir / "my_tool.json").read_text())
    assert len(saved) == 1
    assert saved[0]["mock_data"] == "result_2"  # second call overwrote the first
