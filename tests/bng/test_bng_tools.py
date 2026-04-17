"""Tests for BNG MCP tools."""

from unittest.mock import AsyncMock, patch

import pytest

from mcp_controller.bng.tools import register_bng_tools
from mcp_controller.config import Settings
from mcp_controller.core.k8s_types import NetworkDeviceTarget, NetworkHostTarget
from mcp_controller.core.kubernetes_client import (
    KubernetesBadRequestError,
    KubernetesClientError,
)
from mcp_controller.core.types import ErrorResponse

TEST_NAMESPACE = "test-ns"


def _make_mock_k8s(
    device_targets: list[NetworkDeviceTarget],
    host_targets: list[NetworkHostTarget],
) -> AsyncMock:
    """Build a mock `KubernetesClient` that returns fixture data.

    Args:
        device_targets: Validated device target fixtures.
        host_targets:   Validated host target fixtures.

    Returns:
        `AsyncMock` mimicking `KubernetesClient`.
    """
    mock = AsyncMock()
    mock.list_network_device_targets = AsyncMock(return_value=device_targets)
    mock.list_network_host_targets = AsyncMock(return_value=host_targets)
    return mock


# ---------------------------------------------------------------------------
# Tool handler tests
# ---------------------------------------------------------------------------


class TestBngTargetsByLabelTool:
    """Tests for the `bng_targets_by_label` tool."""

    async def test_passes_label_selector_to_client(
        self,
        mock_ctx: AsyncMock,
        device_targets: list[NetworkDeviceTarget],
        host_targets: list[NetworkHostTarget],
    ) -> None:
        mock_k8s = _make_mock_k8s(device_targets, host_targets)
        settings = Settings(k8s_namespace=TEST_NAMESPACE)

        with patch(
            "mcp_controller.bng.tools.KubernetesClient", return_value=mock_k8s
        ):
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP(name="test")
            register_bng_tools(mcp, settings)

            tool = mcp._tool_manager._tools["bng_targets_by_label"]
            result = await tool.fn(label_selector="role=bng", ctx=mock_ctx)

        mock_k8s.list_network_device_targets.assert_awaited_once_with(
            label_selector="role=bng"
        )
        mock_k8s.list_network_host_targets.assert_awaited_once_with(
            label_selector="role=bng"
        )
        assert isinstance(result, list)
        assert len(result) == 3

    async def test_raises_error_on_bad_request(
        self,
        mock_ctx: AsyncMock,
    ) -> None:
        mock_k8s = AsyncMock()
        mock_k8s.list_network_device_targets = AsyncMock(
            side_effect=KubernetesBadRequestError("invalid label selector: Bad Request")
        )
        settings = Settings(k8s_namespace=TEST_NAMESPACE)

        with patch(
            "mcp_controller.bng.tools.KubernetesClient", return_value=mock_k8s
        ):
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP(name="test")
            register_bng_tools(mcp, settings)

            tool = mcp._tool_manager._tools["bng_targets_by_label"]
            with pytest.raises(ErrorResponse) as exc_info:
                await tool.fn(label_selector="=bad", ctx=mock_ctx)

        assert exc_info.value.error == "bad_request"
        mock_ctx.error.assert_awaited_once()

    async def test_raises_error_on_k8s_failure(
        self,
        mock_ctx: AsyncMock,
    ) -> None:
        mock_k8s = AsyncMock()
        mock_k8s.list_network_device_targets = AsyncMock(
            side_effect=KubernetesClientError("connection refused")
        )
        settings = Settings(k8s_namespace=TEST_NAMESPACE)

        with patch(
            "mcp_controller.bng.tools.KubernetesClient", return_value=mock_k8s
        ):
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP(name="test")
            register_bng_tools(mcp, settings)

            tool = mcp._tool_manager._tools["bng_targets_by_label"]
            with pytest.raises(ErrorResponse) as exc_info:
                await tool.fn(label_selector="role=bng", ctx=mock_ctx)

        assert exc_info.value.error == "api_error"
