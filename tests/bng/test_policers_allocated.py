"""Tests for the ingress/egress policers-allocated BNG tools."""

from unittest.mock import AsyncMock, patch

import pytest

from mcp_controller.bng.tools import register_bng_tools
from mcp_controller.config import Settings
from mcp_controller.core.prometheus_client import PrometheusClient, PrometheusClientError
from mcp_controller.core.types import ErrorResponse, MetricResult, MetricSample, MetricSeries

TEST_NAMESPACE = "test-ns"
DEVICE_SOURCE = f"{TEST_NAMESPACE}/bng-01"


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


def _slot_labels(card: str, fp: str) -> dict[str, str]:
    return {
        "source": DEVICE_SOURCE,
        "card_slot_number": card,
        "fp_fp_number": fp,
    }


def _register_and_get_tool(tool_name: str, mock_prom: AsyncMock):
    from mcp.server.fastmcp import FastMCP

    settings = Settings(k8s_namespace=TEST_NAMESPACE)
    mcp = FastMCP(name="test")
    with (
        patch("mcp_controller.bng.tools.KubernetesClient", return_value=AsyncMock()),
        patch("mcp_controller.bng.tools.PrometheusClient", return_value=mock_prom),
    ):
        register_bng_tools(mcp, settings)
    return mcp._tool_manager._tools[tool_name]


def _make_prom(
    range_result: MetricResult,
    allocated_result: MetricResult,
    total_result: MetricResult,
) -> AsyncMock:
    mock = AsyncMock(spec=PrometheusClient)
    mock.query_range = AsyncMock(return_value=range_result)
    # Two instant calls: allocated then total (in that order).
    mock.query = AsyncMock(side_effect=[allocated_result, total_result])
    return mock


@pytest.mark.parametrize(
    "tool_name,direction",
    [
        ("ingress_policers_allocated", "ingress"),
        ("egress_policers_allocated", "egress"),
    ],
)
class TestPolicersAllocated:
    async def test_returns_breakdown_per_card_fp(
        self, tool_name: str, direction: str, mock_ctx: AsyncMock
    ) -> None:
        range_result = _matrix([
            _series(_slot_labels("1", "1"), [10.0, 12.0, 15.0]),
            _series(_slot_labels("1", "2"), [20.0, 22.0, 25.0]),
        ])
        allocated_result = _vector([
            _series(_slot_labels("1", "1"), [30.0]),
            _series(_slot_labels("1", "2"), [50.0]),
        ])
        total_result = _vector([
            _series(_slot_labels("1", "1"), [200.0]),
            _series(_slot_labels("1", "2"), [200.0]),
        ])
        mock_prom = _make_prom(range_result, allocated_result, total_result)
        tool = _register_and_get_tool(tool_name, mock_prom)

        result = await tool.fn(
            device="bng-01", interval="5m", step="1m", ctx=mock_ctx
        )

        assert result.device == DEVICE_SOURCE
        assert result.direction == direction
        assert len(result.by_slot) == 2

        slot_11 = next(s for s in result.by_slot if s.card == 1 and s.fp == 1)
        assert slot_11.currently_allocated == 30.0
        assert slot_11.currently_total == 200.0
        assert slot_11.currently_pct == pytest.approx(15.0)
        assert slot_11.min_pct == pytest.approx(10.0)
        assert slot_11.max_pct == pytest.approx(15.0)

        slot_12 = next(s for s in result.by_slot if s.card == 1 and s.fp == 2)
        assert slot_12.currently_pct == pytest.approx(25.0)
        assert slot_12.min_pct == pytest.approx(20.0)
        assert slot_12.max_pct == pytest.approx(25.0)

    async def test_card_filter_in_selector(
        self, tool_name: str, direction: str, mock_ctx: AsyncMock
    ) -> None:
        range_result = _matrix([_series(_slot_labels("1", "1"), [10.0])])
        allocated_result = _vector([_series(_slot_labels("1", "1"), [10.0])])
        total_result = _vector([_series(_slot_labels("1", "1"), [100.0])])
        mock_prom = _make_prom(range_result, allocated_result, total_result)
        tool = _register_and_get_tool(tool_name, mock_prom)

        await tool.fn(
            device="bng-01", interval="5m", step="1m", ctx=mock_ctx, card=1
        )

        promql = mock_prom.query_range.call_args.args[0]
        assert 'card_slot_number="1"' in promql
        assert "fp_fp_number=" not in promql

    async def test_fp_filter_in_selector(
        self, tool_name: str, direction: str, mock_ctx: AsyncMock
    ) -> None:
        range_result = _matrix([_series(_slot_labels("1", "2"), [10.0])])
        allocated_result = _vector([_series(_slot_labels("1", "2"), [10.0])])
        total_result = _vector([_series(_slot_labels("1", "2"), [100.0])])
        mock_prom = _make_prom(range_result, allocated_result, total_result)
        tool = _register_and_get_tool(tool_name, mock_prom)

        await tool.fn(
            device="bng-01", interval="5m", step="1m", ctx=mock_ctx, fp=2
        )

        promql = mock_prom.query_range.call_args.args[0]
        assert 'fp_fp_number="2"' in promql
        assert "card_slot_number=" not in promql

    async def test_both_filters_in_selector(
        self, tool_name: str, direction: str, mock_ctx: AsyncMock
    ) -> None:
        range_result = _matrix([_series(_slot_labels("3", "4"), [10.0])])
        allocated_result = _vector([_series(_slot_labels("3", "4"), [10.0])])
        total_result = _vector([_series(_slot_labels("3", "4"), [100.0])])
        mock_prom = _make_prom(range_result, allocated_result, total_result)
        tool = _register_and_get_tool(tool_name, mock_prom)

        result = await tool.fn(
            device="bng-01", interval="5m", step="1m", ctx=mock_ctx, card=3, fp=4
        )

        promql = mock_prom.query_range.call_args.args[0]
        assert 'card_slot_number="3"' in promql
        assert 'fp_fp_number="4"' in promql
        assert len(result.by_slot) == 1
        assert result.by_slot[0].card == 3
        assert result.by_slot[0].fp == 4

    async def test_empty_result_yields_empty_by_slot(
        self, tool_name: str, direction: str, mock_ctx: AsyncMock
    ) -> None:
        empty = _matrix([])
        empty_vec = _vector([])
        mock_prom = _make_prom(empty, empty_vec, empty_vec)
        tool = _register_and_get_tool(tool_name, mock_prom)

        result = await tool.fn(
            device="bng-01", interval="5m", step="1m", ctx=mock_ctx
        )

        assert result.by_slot == []

    async def test_zero_total_yields_null_pct(
        self, tool_name: str, direction: str, mock_ctx: AsyncMock
    ) -> None:
        range_result = _matrix([_series(_slot_labels("1", "1"), [0.0])])
        allocated_result = _vector([_series(_slot_labels("1", "1"), [0.0])])
        total_result = _vector([_series(_slot_labels("1", "1"), [0.0])])
        mock_prom = _make_prom(range_result, allocated_result, total_result)
        tool = _register_and_get_tool(tool_name, mock_prom)

        result = await tool.fn(
            device="bng-01", interval="5m", step="1m", ctx=mock_ctx
        )

        assert len(result.by_slot) == 1
        assert result.by_slot[0].currently_pct is None
        assert result.by_slot[0].currently_total == 0.0

    async def test_invalid_interval_raises(
        self, tool_name: str, direction: str, mock_ctx: AsyncMock
    ) -> None:
        mock_prom = _make_prom(_matrix([]), _vector([]), _vector([]))
        tool = _register_and_get_tool(tool_name, mock_prom)

        with pytest.raises(ErrorResponse) as exc:
            await tool.fn(
                device="bng-01", interval="5xyz", step="1m", ctx=mock_ctx
            )
        assert exc.value.error == "invalid_interval"
        mock_prom.query_range.assert_not_awaited()

    async def test_prometheus_failure_raises(
        self, tool_name: str, direction: str, mock_ctx: AsyncMock
    ) -> None:
        mock_prom = AsyncMock(spec=PrometheusClient)
        mock_prom.query_range = AsyncMock(side_effect=PrometheusClientError("boom"))
        mock_prom.query = AsyncMock(return_value=_vector([]))
        tool = _register_and_get_tool(tool_name, mock_prom)

        with pytest.raises(ErrorResponse) as exc:
            await tool.fn(
                device="bng-01", interval="5m", step="1m", ctx=mock_ctx
            )
        assert exc.value.error == "prometheus_error"

    async def test_promql_references_direction_metrics(
        self, tool_name: str, direction: str, mock_ctx: AsyncMock
    ) -> None:
        mock_prom = _make_prom(_matrix([]), _vector([]), _vector([]))
        tool = _register_and_get_tool(tool_name, mock_prom)

        await tool.fn(device="bng-01", interval="5m", step="1m", ctx=mock_ctx)

        range_promql = mock_prom.query_range.call_args.args[0]
        assert f"state_card_fp_resource_usage_{direction}_policers_allocated" in range_promql
        assert f"state_card_fp_resource_usage_{direction}_policers_total" in range_promql
        assert "on(source, card_slot_number, fp_fp_number)" in range_promql
