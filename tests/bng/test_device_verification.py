"""Tests for device resolution and verification against K8s targets."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from mcp.server.fastmcp import FastMCP

from mcp_controller.bng.common import resolve_device_source, verify_device_target
from mcp_controller.bng.resources import register_bng_resources
from mcp_controller.bng.tools import register_bng_tools
from mcp_controller.config import Settings
from mcp_controller.core.k8s_types import NetworkDeviceTarget, NetworkHostTarget
from mcp_controller.core.kubernetes_client import (
    KubernetesClientError,
    KubernetesNotFoundError,
)
from mcp_controller.core.types import ErrorResponse

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
PROM_FIXTURES = FIXTURES_DIR / "prometheus"
K8S_FIXTURES = FIXTURES_DIR / "kubernetes"

# Fixture devices live in namespace "nok".
FIXTURE_NAMESPACE = "nok"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_device_targets() -> list[NetworkDeviceTarget]:
    raw = json.loads((K8S_FIXTURES / "network_device_targets.json").read_text())
    return [NetworkDeviceTarget.model_validate(item) for item in raw]


def _load_host_targets() -> list[NetworkHostTarget]:
    raw = json.loads((K8S_FIXTURES / "network_host_targets.json").read_text())
    return [NetworkHostTarget.model_validate(item) for item in raw]


def _make_mock_k8s() -> AsyncMock:
    """Build a mock `KubernetesClient` that returns fixture data."""
    mock = AsyncMock()
    mock.list_network_device_targets = AsyncMock(return_value=_load_device_targets())
    mock.list_network_host_targets = AsyncMock(return_value=_load_host_targets())
    return mock


def _build_mcp_with_resources(settings: Settings, mock_k8s: AsyncMock) -> FastMCP:
    """Create a FastMCP server with BNG resources registered."""
    mcp = FastMCP(name="test")
    with patch("mcp_controller.bng.resources.KubernetesClient", return_value=mock_k8s):
        register_bng_resources(mcp, settings)
    return mcp


# ---------------------------------------------------------------------------
# resolve_device_source tests
# ---------------------------------------------------------------------------


class TestResolveDeviceSource:
    """Tests for the `resolve_device_source` sync helper."""

    def test_prepends_default_namespace_when_missing(self) -> None:
        result = resolve_device_source("bng1", "nok-bng")
        assert result == "nok-bng/bng1"

    def test_returns_unchanged_when_namespace_present(self) -> None:
        result = resolve_device_source("nok-bng/bng1", "other-ns")
        assert result == "nok-bng/bng1"

    def test_uses_first_slash_only(self) -> None:
        result = resolve_device_source("ns/name/extra", "default")
        assert result == "ns/name/extra"


# ---------------------------------------------------------------------------
# verify_device_target tests
# ---------------------------------------------------------------------------


class TestVerifyDeviceTarget:
    """Tests for async device verification via the ``bng://targets/devices`` resource."""

    async def test_passes_when_device_exists(self) -> None:
        settings = Settings(k8s_namespace=FIXTURE_NAMESPACE)
        mock_k8s = _make_mock_k8s()
        mcp = _build_mcp_with_resources(settings, mock_k8s)

        # Should not raise — bng1 exists in namespace "nok".
        await verify_device_target("nok/bng1", mcp)

    async def test_raises_not_found_for_unknown_device(self) -> None:
        settings = Settings(k8s_namespace=FIXTURE_NAMESPACE)
        mock_k8s = _make_mock_k8s()
        mcp = _build_mcp_with_resources(settings, mock_k8s)

        with pytest.raises(KubernetesNotFoundError, match="no-such-device"):
            await verify_device_target("nok/no-such-device", mcp)

    async def test_raises_not_found_on_namespace_mismatch(self) -> None:
        """Device bng1 exists in "nok", but caller asks for "wrong-ns"."""
        settings = Settings(k8s_namespace=FIXTURE_NAMESPACE)
        mock_k8s = _make_mock_k8s()
        mcp = _build_mcp_with_resources(settings, mock_k8s)

        with pytest.raises(KubernetesNotFoundError, match="bng1"):
            await verify_device_target("wrong-ns/bng1", mcp)

    async def test_raises_client_error_on_resource_error(self) -> None:
        settings = Settings(k8s_namespace=FIXTURE_NAMESPACE)
        mock_k8s = _make_mock_k8s()
        mock_k8s.list_network_device_targets = AsyncMock(
            side_effect=KubernetesClientError("API server unavailable")
        )
        mcp = _build_mcp_with_resources(settings, mock_k8s)

        with pytest.raises(KubernetesClientError, match="API server unavailable"):
            await verify_device_target("nok/bng1", mcp)


# ---------------------------------------------------------------------------
# End-to-end: tool returns device_not_found when device is missing
# ---------------------------------------------------------------------------


class TestToolDeviceVerification:
    """Verify that metric tools return a structured error for unknown devices."""

    async def test_cpu_tool_returns_error_for_unknown_device(self, mock_ctx: AsyncMock) -> None:
        settings = Settings(k8s_namespace=FIXTURE_NAMESPACE)
        mock_k8s = _make_mock_k8s()
        mock_prom = AsyncMock()

        mcp = FastMCP(name="test")
        with (
            patch("mcp_controller.bng.resources.KubernetesClient", return_value=mock_k8s),
            patch("mcp_controller.bng.tools.KubernetesClient", return_value=mock_k8s),
            patch("mcp_controller.bng.tools.PrometheusClient", return_value=mock_prom),
        ):
            register_bng_resources(mcp, settings)
            register_bng_tools(mcp, settings)

        tool = mcp._tool_manager._tools["bng_device_cpu_usage"]
        with pytest.raises(ErrorResponse) as exc_info:
            await tool.fn(device="no-such-device", interval="5m", step="1m", ctx=mock_ctx)

        data = exc_info.value.model_dump(mode="json")
        assert data["error"] == "device_not_found"
        assert "no-such-device" in data["detail"]
        # Prometheus must not be called when device verification fails.
        mock_prom.query_range.assert_not_awaited()

    async def test_memory_tool_returns_error_for_unknown_device(self, mock_ctx: AsyncMock) -> None:
        settings = Settings(k8s_namespace=FIXTURE_NAMESPACE)
        mock_k8s = _make_mock_k8s()
        mock_prom = AsyncMock()

        mcp = FastMCP(name="test")
        with (
            patch("mcp_controller.bng.resources.KubernetesClient", return_value=mock_k8s),
            patch("mcp_controller.bng.tools.KubernetesClient", return_value=mock_k8s),
            patch("mcp_controller.bng.tools.PrometheusClient", return_value=mock_prom),
        ):
            register_bng_resources(mcp, settings)
            register_bng_tools(mcp, settings)

        tool = mcp._tool_manager._tools["bng_device_memory_usage"]
        with pytest.raises(ErrorResponse) as exc_info:
            await tool.fn(device="no-such-device", interval="5m", step="1m", ctx=mock_ctx)

        data = exc_info.value.model_dump(mode="json")
        assert data["error"] == "device_not_found"
        mock_prom.query_range.assert_not_awaited()

    async def test_unavailability_tool_returns_error_for_unknown_device(
        self, mock_ctx: AsyncMock
    ) -> None:
        settings = Settings(k8s_namespace=FIXTURE_NAMESPACE)
        mock_k8s = _make_mock_k8s()
        mock_prom = AsyncMock()

        mcp = FastMCP(name="test")
        with (
            patch("mcp_controller.bng.resources.KubernetesClient", return_value=mock_k8s),
            patch("mcp_controller.bng.tools.KubernetesClient", return_value=mock_k8s),
            patch("mcp_controller.bng.tools.PrometheusClient", return_value=mock_prom),
        ):
            register_bng_resources(mcp, settings)
            register_bng_tools(mcp, settings)

        tool = mcp._tool_manager._tools["bng_device_unavailability_map"]
        with pytest.raises(ErrorResponse) as exc_info:
            await tool.fn(device="no-such-device", interval="5m", step="1m", ctx=mock_ctx)

        data = exc_info.value.model_dump(mode="json")
        assert data["error"] == "device_not_found"
        mock_prom.query_range.assert_not_awaited()

    async def test_cpu_tool_passes_for_existing_device(self, mock_ctx: AsyncMock) -> None:
        fixture_data = json.loads((PROM_FIXTURES / "cpu_usage_range.json").read_text())
        from mcp_controller.core.types import MetricResult, MetricSample, MetricSeries

        data = fixture_data["data"]
        series = [
            MetricSeries(
                labels=dict(item.get("metric", {})),
                samples=[
                    MetricSample(timestamp=float(v[0]), value=float(v[1]))
                    for v in item.get("values", [])
                ],
            )
            for item in data["result"]
        ]
        prom_result = MetricResult(query="fixture", result_type=data["resultType"], series=series)

        settings = Settings(k8s_namespace=FIXTURE_NAMESPACE)
        mock_k8s = _make_mock_k8s()
        mock_prom = AsyncMock()
        mock_prom.query_range = AsyncMock(return_value=prom_result)

        mcp = FastMCP(name="test")
        with (
            patch("mcp_controller.bng.resources.KubernetesClient", return_value=mock_k8s),
            patch("mcp_controller.bng.tools.KubernetesClient", return_value=mock_k8s),
            patch("mcp_controller.bng.tools.PrometheusClient", return_value=mock_prom),
        ):
            register_bng_resources(mcp, settings)
            register_bng_tools(mcp, settings)

        tool = mcp._tool_manager._tools["bng_device_cpu_usage"]
        # "bng1" exists in namespace "nok" (fixture data)
        result = await tool.fn(device="bng1", interval="5m", step="1m", ctx=mock_ctx)

        data = result.model_dump(mode="json")
        assert "error" not in data
        assert data["device"] == f"{FIXTURE_NAMESPACE}/bng1"
        assert mock_prom.query_range.call_count == 4
