"""Tests for BNG metric tools (CPU usage, memory usage)."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from mcp_controller.bng.tools import register_bng_tools
from mcp_controller.config import Settings
from mcp_controller.core.prometheus_client import PrometheusClient, PrometheusClientError
from mcp_controller.core.types import ErrorResponse, MetricResult, MetricSample, MetricSeries

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
PROM_FIXTURES = FIXTURES_DIR / "prometheus"

TEST_NAMESPACE = "test-ns"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _bypass_device_verification():
    """Bypass K8s device verification — metric tool tests focus on Prometheus behaviour."""
    with patch("mcp_controller.bng.common.verify_device_target", new_callable=AsyncMock):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_prom_fixture(path: Path) -> MetricResult:
    """Parse a Prometheus fixture JSON file into a `MetricResult`."""
    body = json.loads(path.read_text())
    data = body["data"]
    series_list = []
    for item in data["result"]:
        labels = dict(item.get("metric", {}))
        samples = [
            MetricSample(timestamp=float(v[0]), value=float(v[1]))
            for v in item.get("values", [])
        ]
        series_list.append(MetricSeries(labels=labels, samples=samples))
    return MetricResult(
        query="fixture",
        result_type=data["resultType"],
        series=series_list,
    )


def _make_mock_prom(return_value: MetricResult | None) -> AsyncMock:
    """Build a mock `PrometheusClient` returning a fixed query_range result."""
    mock = AsyncMock(spec=PrometheusClient)
    mock.query_range = AsyncMock(return_value=return_value)
    return mock


def _register_and_get_tool(tool_name: str, mock_prom: AsyncMock):
    """Register BNG tools with a mocked Prometheus client and return the named tool.

    Args:
        tool_name: Name of the tool to retrieve.
        mock_prom: Mock `PrometheusClient` instance.

    Returns:
        The tool function object.
    """
    from mcp.server.fastmcp import FastMCP

    settings = Settings(k8s_namespace=TEST_NAMESPACE)
    mcp = FastMCP(name="test")
    with (
        patch("mcp_controller.bng.tools.KubernetesClient", return_value=AsyncMock()),
        patch("mcp_controller.bng.tools.PrometheusClient", return_value=mock_prom),
    ):
        register_bng_tools(mcp, settings)
    return mcp._tool_manager._tools[tool_name]


# ---------------------------------------------------------------------------
# CPU usage tool tests
# ---------------------------------------------------------------------------


class TestBngDeviceCpuUsage:
    """Tests for the `bng_device_cpu_usage` tool."""

    async def test_returns_cpu_usage_with_summary_stats(
        self, mock_ctx: AsyncMock
    ) -> None:
        fixture = _parse_prom_fixture(PROM_FIXTURES / "cpu_usage_range.json")
        mock_prom = _make_mock_prom(fixture)
        tool = _register_and_get_tool("bng_device_cpu_usage", mock_prom)

        result = await tool.fn(device="bng-01", interval="5m", step="1m", ctx=mock_ctx)

        data = result.model_dump(mode="json")
        assert data["device"] == f"{TEST_NAMESPACE}/bng-01"
        assert data["interval"] == "5m"
        assert data["step"] == "1m"
        assert data["sample_count"] == 5
        assert data["avg_cpu_usage"] == pytest.approx(15.0)
        assert data["min_cpu_usage"] == pytest.approx(10.0)
        assert data["max_cpu_usage"] == pytest.approx(20.0)
        assert data["result"] is not None

    async def test_returns_null_stats_on_empty_result(
        self, mock_ctx: AsyncMock
    ) -> None:
        fixture = _parse_prom_fixture(PROM_FIXTURES / "empty_range.json")
        mock_prom = _make_mock_prom(fixture)
        tool = _register_and_get_tool("bng_device_cpu_usage", mock_prom)

        result = await tool.fn(device="bng-01", interval="5m", step="1m", ctx=mock_ctx)

        data = result.model_dump(mode="json")
        assert data["sample_count"] == 0
        assert data["avg_cpu_usage"] is None
        assert data["min_cpu_usage"] is None
        assert data["max_cpu_usage"] is None

    async def test_raises_error_on_invalid_interval(
        self, mock_ctx: AsyncMock
    ) -> None:
        mock_prom = _make_mock_prom(None)
        tool = _register_and_get_tool("bng_device_cpu_usage", mock_prom)

        with pytest.raises(ErrorResponse) as exc_info:
            await tool.fn(device="bng-01", interval="5xyz", step="1m", ctx=mock_ctx)

        assert exc_info.value.error == "invalid_interval"
        mock_prom.query_range.assert_not_awaited()

    async def test_raises_error_on_invalid_step(
        self, mock_ctx: AsyncMock
    ) -> None:
        mock_prom = _make_mock_prom(None)
        tool = _register_and_get_tool("bng_device_cpu_usage", mock_prom)

        with pytest.raises(ErrorResponse) as exc_info:
            await tool.fn(device="bng-01", interval="5m", step="bad", ctx=mock_ctx)

        assert exc_info.value.error == "invalid_step"
        mock_prom.query_range.assert_not_awaited()

    async def test_raises_error_on_prometheus_failure(
        self, mock_ctx: AsyncMock
    ) -> None:
        mock_prom = _make_mock_prom(None)
        mock_prom.query_range = AsyncMock(
            side_effect=PrometheusClientError("connection refused")
        )
        tool = _register_and_get_tool("bng_device_cpu_usage", mock_prom)

        with pytest.raises(ErrorResponse) as exc_info:
            await tool.fn(device="bng-01", interval="5m", step="1m", ctx=mock_ctx)

        assert exc_info.value.error == "prometheus_error"
        assert "connection refused" in exc_info.value.detail

    async def test_normalizes_human_friendly_step(
        self, mock_ctx: AsyncMock
    ) -> None:
        fixture = _parse_prom_fixture(PROM_FIXTURES / "cpu_usage_range.json")
        mock_prom = _make_mock_prom(fixture)
        tool = _register_and_get_tool("bng_device_cpu_usage", mock_prom)

        await tool.fn(device="bng-01", interval="5min", step="30sec", ctx=mock_ctx)

        call_kwargs = mock_prom.query_range.call_args.kwargs
        assert call_kwargs["step"] == "30s"


# ---------------------------------------------------------------------------
# Memory usage tool tests
# ---------------------------------------------------------------------------


class TestBngDeviceMemoryUsage:
    """Tests for the `bng_device_memory_usage` tool."""

    async def test_returns_memory_usage_with_summary_stats(
        self, mock_ctx: AsyncMock
    ) -> None:
        fixture = _parse_prom_fixture(PROM_FIXTURES / "memory_usage_range.json")
        mock_prom = _make_mock_prom(fixture)
        tool = _register_and_get_tool("bng_device_memory_usage", mock_prom)

        result = await tool.fn(device="bng-01", interval="5m", step="1m", ctx=mock_ctx)

        data = result.model_dump(mode="json")
        assert data["device"] == f"{TEST_NAMESPACE}/bng-01"
        assert data["interval"] == "5m"
        assert data["step"] == "1m"
        assert data["sample_count"] == 5
        assert data["avg_memory_usage_pct"] == pytest.approx(46.52)
        assert data["min_memory_usage_pct"] == pytest.approx(43.1)
        assert data["max_memory_usage_pct"] == pytest.approx(50.0)
        assert data["result"] is not None

    async def test_returns_null_stats_on_empty_result(
        self, mock_ctx: AsyncMock
    ) -> None:
        fixture = _parse_prom_fixture(PROM_FIXTURES / "empty_range.json")
        mock_prom = _make_mock_prom(fixture)
        tool = _register_and_get_tool("bng_device_memory_usage", mock_prom)

        result = await tool.fn(device="bng-01", interval="5m", step="1m", ctx=mock_ctx)

        data = result.model_dump(mode="json")
        assert data["sample_count"] == 0
        assert data["avg_memory_usage_pct"] is None
        assert data["min_memory_usage_pct"] is None
        assert data["max_memory_usage_pct"] is None

    async def test_raises_error_on_invalid_interval(
        self, mock_ctx: AsyncMock
    ) -> None:
        mock_prom = _make_mock_prom(None)
        tool = _register_and_get_tool("bng_device_memory_usage", mock_prom)

        with pytest.raises(ErrorResponse) as exc_info:
            await tool.fn(device="bng-01", interval="5xyz", step="1m", ctx=mock_ctx)

        assert exc_info.value.error == "invalid_interval"
        mock_prom.query_range.assert_not_awaited()

    async def test_raises_error_on_invalid_step(
        self, mock_ctx: AsyncMock
    ) -> None:
        mock_prom = _make_mock_prom(None)
        tool = _register_and_get_tool("bng_device_memory_usage", mock_prom)

        with pytest.raises(ErrorResponse) as exc_info:
            await tool.fn(device="bng-01", interval="5m", step="bad", ctx=mock_ctx)

        assert exc_info.value.error == "invalid_step"
        mock_prom.query_range.assert_not_awaited()

    async def test_raises_error_on_prometheus_failure(
        self, mock_ctx: AsyncMock
    ) -> None:
        mock_prom = _make_mock_prom(None)
        mock_prom.query_range = AsyncMock(
            side_effect=PrometheusClientError("timeout")
        )
        tool = _register_and_get_tool("bng_device_memory_usage", mock_prom)

        with pytest.raises(ErrorResponse) as exc_info:
            await tool.fn(device="bng-01", interval="5m", step="1m", ctx=mock_ctx)

        assert exc_info.value.error == "prometheus_error"
        assert "timeout" in exc_info.value.detail

    async def test_normalizes_human_friendly_step(
        self, mock_ctx: AsyncMock
    ) -> None:
        fixture = _parse_prom_fixture(PROM_FIXTURES / "memory_usage_range.json")
        mock_prom = _make_mock_prom(fixture)
        tool = _register_and_get_tool("bng_device_memory_usage", mock_prom)

        await tool.fn(device="bng-01", interval="2hours", step="5min", ctx=mock_ctx)

        call_kwargs = mock_prom.query_range.call_args.kwargs
        assert call_kwargs["step"] == "5m"

    async def test_promql_contains_both_metrics(
        self, mock_ctx: AsyncMock
    ) -> None:
        fixture = _parse_prom_fixture(PROM_FIXTURES / "memory_usage_range.json")
        mock_prom = _make_mock_prom(fixture)
        tool = _register_and_get_tool("bng_device_memory_usage", mock_prom)

        await tool.fn(device="bng-01", interval="5m", step="1m", ctx=mock_ctx)

        promql = mock_prom.query_range.call_args.args[0]
        assert "state_system_memory_pools_summary_total_in_use" in promql
        assert "state_system_memory_pools_summary_available_memory" in promql
        assert f'source="{TEST_NAMESPACE}/bng-01"' in promql
