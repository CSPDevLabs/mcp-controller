"""Tests for the ppp_sessions_total_established BNG tool."""

from unittest.mock import AsyncMock, patch

import pytest

from mcp_controller.bng.tools import register_bng_tools
from mcp_controller.config import Settings
from mcp_controller.core.prometheus_client import PrometheusClient, PrometheusClientError
from mcp_controller.core.types import ErrorResponse, MetricResult, MetricSample, MetricSeries

TEST_NAMESPACE = "test-ns"
DEVICE_SOURCE = f"{TEST_NAMESPACE}/bng-01"
TOOL_NAME = "ppp_sessions_total_established"


@pytest.fixture(autouse=True)
def _bypass_device_verification():
    with patch("mcp_controller.bng.common.verify_device_target", new_callable=AsyncMock):
        yield


def _series(labels: dict[str, str], values: list[float]) -> MetricSeries:
    return MetricSeries(
        labels=labels,
        samples=[MetricSample(timestamp=float(i), value=v) for i, v in enumerate(values)],
    )


def _matrix(series: list[MetricSeries]) -> MetricResult:
    return MetricResult(query="fixture", result_type="matrix", series=series)


def _vector(series: list[MetricSeries]) -> MetricResult:
    return MetricResult(query="fixture", result_type="vector", series=series)


def _ppp_labels() -> dict[str, str]:
    return {
        "source": DEVICE_SOURCE,
        "sessions_counter": "ppp-sessions-total-established",
    }


def _register_and_get_tool(mock_prom: AsyncMock):
    from mcp.server.fastmcp import FastMCP

    settings = Settings(k8s_namespace=TEST_NAMESPACE)
    mcp = FastMCP(name="test")
    with (
        patch("mcp_controller.bng.tools.KubernetesClient", return_value=AsyncMock()),
        patch("mcp_controller.bng.tools.PrometheusClient", return_value=mock_prom),
    ):
        register_bng_tools(mcp, settings)
    return mcp._tool_manager._tools[TOOL_NAME]


def _make_prom(range_result: MetricResult, instant_result: MetricResult) -> AsyncMock:
    mock = AsyncMock(spec=PrometheusClient)
    mock.query_range = AsyncMock(return_value=range_result)
    mock.query = AsyncMock(return_value=instant_result)
    return mock


class TestPppSessionsTotalEstablished:
    async def test_returns_current_min_max(self, mock_ctx: AsyncMock) -> None:
        range_result = _matrix([_series(_ppp_labels(), [100.0, 120.0, 90.0, 150.0, 130.0])])
        instant_result = _vector([_series(_ppp_labels(), [135.0])])
        mock_prom = _make_prom(range_result, instant_result)
        tool = _register_and_get_tool(mock_prom)

        result = await tool.fn(
            device="bng-01", interval="5m", step="1m", ctx=mock_ctx
        )

        assert result.device == DEVICE_SOURCE
        assert result.interval == "5m"
        assert result.step == "1m"
        assert result.currently_established == 135
        assert result.min_established == 90
        assert result.max_established == 150
        assert result.result is not None
        assert len(result.result.series) == 1

    async def test_empty_range_yields_null_min_max(self, mock_ctx: AsyncMock) -> None:
        instant_result = _vector([_series(_ppp_labels(), [42.0])])
        mock_prom = _make_prom(_matrix([]), instant_result)
        tool = _register_and_get_tool(mock_prom)

        result = await tool.fn(
            device="bng-01", interval="5m", step="1m", ctx=mock_ctx
        )

        assert result.currently_established == 42
        assert result.min_established is None
        assert result.max_established is None

    async def test_empty_instant_yields_null_current(self, mock_ctx: AsyncMock) -> None:
        range_result = _matrix([_series(_ppp_labels(), [10.0, 20.0])])
        mock_prom = _make_prom(range_result, _vector([]))
        tool = _register_and_get_tool(mock_prom)

        result = await tool.fn(
            device="bng-01", interval="5m", step="1m", ctx=mock_ctx
        )

        assert result.currently_established is None
        assert result.min_established == 10
        assert result.max_established == 20

    async def test_promql_contains_both_label_selectors(self, mock_ctx: AsyncMock) -> None:
        mock_prom = _make_prom(_matrix([]), _vector([]))
        tool = _register_and_get_tool(mock_prom)

        await tool.fn(device="bng-01", interval="5m", step="1m", ctx=mock_ctx)

        promql = mock_prom.query_range.call_args.args[0]
        assert "state_subscriber_mgmt_statistics_sessions_current_value" in promql
        assert f'source="{DEVICE_SOURCE}"' in promql
        assert 'sessions_counter="ppp-sessions-total-established"' in promql

    async def test_invalid_interval_raises(self, mock_ctx: AsyncMock) -> None:
        mock_prom = _make_prom(_matrix([]), _vector([]))
        tool = _register_and_get_tool(mock_prom)

        with pytest.raises(ErrorResponse) as exc:
            await tool.fn(
                device="bng-01", interval="5xyz", step="1m", ctx=mock_ctx
            )
        assert exc.value.error == "invalid_interval"
        mock_prom.query_range.assert_not_awaited()

    async def test_prometheus_failure_raises(self, mock_ctx: AsyncMock) -> None:
        mock_prom = AsyncMock(spec=PrometheusClient)
        mock_prom.query_range = AsyncMock(side_effect=PrometheusClientError("boom"))
        mock_prom.query = AsyncMock(return_value=_vector([]))
        tool = _register_and_get_tool(mock_prom)

        with pytest.raises(ErrorResponse) as exc:
            await tool.fn(
                device="bng-01", interval="5m", step="1m", ctx=mock_ctx
            )
        assert exc.value.error == "prometheus_error"
