"""Tests for BNG target resources and resource templates."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.fastmcp.exceptions import ResourceError

from mcp_controller.bng.common import _raise_resource_error, _serialize_targets, _target_summary
from mcp_controller.bng.types import TargetSummary
from mcp_controller.bng.resources import register_bng_resources
from mcp_controller.config import Settings
from mcp_controller.core.k8s_types import NetworkDeviceTarget, NetworkHostTarget
from mcp_controller.core.kubernetes_client import (
    KubernetesBadRequestError,
    KubernetesClientError,
    KubernetesNamespaceNotFoundError,
    KubernetesNotFoundError,
    KubernetesPermissionError,
    KubernetesSchemaError,
)

# Use a fixed namespace so tests don't depend on the default.
TEST_NAMESPACE = "test-ns"


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestTargetSummary:
    """Unit tests for `_target_summary`."""

    def test_device_summary(self, device_targets: list[NetworkDeviceTarget]) -> None:
        summary = _target_summary(device_targets[0])
        assert isinstance(summary, TargetSummary)
        assert summary == TargetSummary(
            name="bng1",
            address="10.0.0.1",
            hostname="bng1.lab.nok.dev",
            kind="NetworkDeviceTarget",
            namespace="nok",
        )

    def test_host_summary(self, host_targets: list[NetworkHostTarget]) -> None:
        summary = _target_summary(host_targets[0])
        assert isinstance(summary, TargetSummary)
        assert summary == TargetSummary(
            name="jumphost1",
            address="10.1.0.1",
            hostname="jumphost1.lab.nok.dev",
            kind="NetworkHostTarget",
            namespace="nok",
        )


class TestSerializeTargets:
    """Unit tests for `_serialize_targets`."""

    def test_round_trips_device_targets(
        self, device_targets: list[NetworkDeviceTarget]
    ) -> None:
        result = json.loads(_serialize_targets(device_targets))
        assert len(result) == 2
        assert result[0]["metadata"]["name"] == "bng1"
        # Aliases must be used in JSON output.
        assert "apiVersion" in result[0]
        assert "commonLabels" in result[0]["spec"]

    def test_round_trips_host_targets(
        self, host_targets: list[NetworkHostTarget]
    ) -> None:
        result = json.loads(_serialize_targets(host_targets))
        assert len(result) == 1
        assert result[0]["metadata"]["name"] == "jumphost1"


# ---------------------------------------------------------------------------
# Resource handler tests
# ---------------------------------------------------------------------------


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


class TestBngTargetsResource:
    """Tests for the `bng://targets` static resource."""

    async def test_returns_all_summaries(
        self,
        device_targets: list[NetworkDeviceTarget],
        host_targets: list[NetworkHostTarget],
    ) -> None:
        mock_k8s = _make_mock_k8s(device_targets, host_targets)
        settings = Settings(k8s_namespace=TEST_NAMESPACE)

        with patch(
            "mcp_controller.bng.resources.KubernetesClient", return_value=mock_k8s
        ):
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP(name="test")
            register_bng_resources(mcp, settings)

            resource = mcp._resource_manager._resources["bng://targets"]
            result = await resource.read()

        data = json.loads(result)
        assert len(data) == 3  # 2 devices + 1 host
        names = {entry["name"] for entry in data}
        assert names == {"bng1", "bng2", "jumphost1"}


class TestBngTargetDevicesResource:
    """Tests for the `bng://targets/devices` static resource."""

    async def test_returns_full_device_specs(
        self,
        device_targets: list[NetworkDeviceTarget],
        host_targets: list[NetworkHostTarget],
    ) -> None:
        mock_k8s = _make_mock_k8s(device_targets, host_targets)
        settings = Settings(k8s_namespace=TEST_NAMESPACE)

        with patch(
            "mcp_controller.bng.resources.KubernetesClient", return_value=mock_k8s
        ):
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP(name="test")
            register_bng_resources(mcp, settings)

            resource = mcp._resource_manager._resources["bng://targets/devices"]
            result = await resource.read()

        data = json.loads(result)
        assert len(data) == 2
        assert data[0]["spec"]["address"] == "10.0.0.1"
        mock_k8s.list_network_device_targets.assert_awaited_once()


class TestBngTargetHostsResource:
    """Tests for the `bng://targets/hosts` static resource."""

    async def test_returns_full_host_specs(
        self,
        device_targets: list[NetworkDeviceTarget],
        host_targets: list[NetworkHostTarget],
    ) -> None:
        mock_k8s = _make_mock_k8s(device_targets, host_targets)
        settings = Settings(k8s_namespace=TEST_NAMESPACE)

        with patch(
            "mcp_controller.bng.resources.KubernetesClient", return_value=mock_k8s
        ):
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP(name="test")
            register_bng_resources(mcp, settings)

            resource = mcp._resource_manager._resources["bng://targets/hosts"]
            result = await resource.read()

        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["spec"]["hostname"] == "jumphost1.lab.nok.dev"


# ---------------------------------------------------------------------------
# Resource template handler tests
# ---------------------------------------------------------------------------


class TestBngTargetDevicesByNamespaceTemplate:
    """Tests for the `bng://targets/devices/{namespace}` template."""

    async def test_passes_namespace_to_client(
        self,
        mock_ctx: AsyncMock,
        device_targets: list[NetworkDeviceTarget],
        host_targets: list[NetworkHostTarget],
    ) -> None:
        mock_k8s = _make_mock_k8s(device_targets, host_targets)
        settings = Settings(k8s_namespace=TEST_NAMESPACE)

        with patch(
            "mcp_controller.bng.resources.KubernetesClient", return_value=mock_k8s
        ):
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP(name="test")
            register_bng_resources(mcp, settings)

            tpl = mcp._resource_manager._templates[
                "bng://targets/devices/{namespace}"
            ]
            result = await tpl.fn("production", mock_ctx)

        mock_k8s.list_network_device_targets.assert_awaited_once_with(
            namespace="production"
        )
        data = json.loads(result)
        assert len(data) == 2


class TestBngTargetHostsByNamespaceTemplate:
    """Tests for the `bng://targets/hosts/{namespace}` template."""

    async def test_passes_namespace_to_client(
        self,
        mock_ctx: AsyncMock,
        device_targets: list[NetworkDeviceTarget],
        host_targets: list[NetworkHostTarget],
    ) -> None:
        mock_k8s = _make_mock_k8s(device_targets, host_targets)
        settings = Settings(k8s_namespace=TEST_NAMESPACE)

        with patch(
            "mcp_controller.bng.resources.KubernetesClient", return_value=mock_k8s
        ):
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP(name="test")
            register_bng_resources(mcp, settings)

            tpl = mcp._resource_manager._templates[
                "bng://targets/hosts/{namespace}"
            ]
            result = await tpl.fn("staging", mock_ctx)

        mock_k8s.list_network_host_targets.assert_awaited_once_with(
            namespace="staging"
        )
        data = json.loads(result)
        assert len(data) == 1


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestRaiseResourceError:
    """Unit tests for `_raise_resource_error`."""

    @pytest.mark.parametrize(
        "exc_class,expected_category",
        [
            (KubernetesBadRequestError, "bad_request"),
            (KubernetesNamespaceNotFoundError, "namespace_not_found"),
            (KubernetesNotFoundError, "not_found"),
            (KubernetesPermissionError, "permission_denied"),
            (KubernetesSchemaError, "schema_validation"),
            (KubernetesClientError, "api_error"),
        ],
    )
    async def test_error_categories(
        self,
        mock_ctx: AsyncMock,
        exc_class: type[KubernetesClientError],
        expected_category: str,
    ) -> None:
        exc = exc_class("something went wrong")
        with pytest.raises(ResourceError) as exc_info:
            await _raise_resource_error("bng://targets", exc, mock_ctx)

        data = json.loads(str(exc_info.value))
        assert data["error"] == expected_category
        assert data["uri"] == "bng://targets"
        assert "something went wrong" in data["detail"]
        mock_ctx.error.assert_awaited_once()

    async def test_ctx_error_receives_uri_and_detail(
        self,
        mock_ctx: AsyncMock,
    ) -> None:
        exc = KubernetesPermissionError("RBAC denied")
        with pytest.raises(ResourceError):
            await _raise_resource_error("bng://targets/devices", exc, mock_ctx)

        msg = mock_ctx.error.call_args[0][0]
        assert "bng://targets/devices" in msg
        assert "RBAC denied" in msg


class TestResourceK8sErrorHandling:
    """Integration tests: K8s errors in resource handlers raise `ResourceError`."""

    async def test_targets_raises_resource_error_on_k8s_failure(
        self,
    ) -> None:
        mock_k8s = AsyncMock()
        mock_k8s.list_network_device_targets = AsyncMock(
            side_effect=KubernetesPermissionError("RBAC denied for targets")
        )
        settings = Settings(k8s_namespace=TEST_NAMESPACE)

        with patch(
            "mcp_controller.bng.resources.KubernetesClient", return_value=mock_k8s
        ):
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP(name="test")
            register_bng_resources(mcp, settings)

            resource = mcp._resource_manager._resources["bng://targets"]
            with pytest.raises(ResourceError) as exc_info:
                await resource.fn()

        data = json.loads(str(exc_info.value))
        assert data["error"] == "permission_denied"

    async def test_devices_by_ns_raises_resource_error_on_namespace_not_found(
        self,
        mock_ctx: AsyncMock,
    ) -> None:
        mock_k8s = AsyncMock()
        mock_k8s.list_network_device_targets = AsyncMock(
            side_effect=KubernetesNamespaceNotFoundError(
                "Namespace 'ghost' does not exist"
            )
        )
        settings = Settings(k8s_namespace=TEST_NAMESPACE)

        with patch(
            "mcp_controller.bng.resources.KubernetesClient", return_value=mock_k8s
        ):
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP(name="test")
            register_bng_resources(mcp, settings)

            tpl = mcp._resource_manager._templates[
                "bng://targets/devices/{namespace}"
            ]
            with pytest.raises(ResourceError) as exc_info:
                await tpl.fn("ghost", mock_ctx)

        data = json.loads(str(exc_info.value))
        assert data["error"] == "namespace_not_found"
        assert "ghost" in data["detail"]
