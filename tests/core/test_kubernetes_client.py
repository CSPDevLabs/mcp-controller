"""Tests for KubernetesClient schema validation error handling."""

from unittest.mock import AsyncMock, patch

import pytest
from kubernetes_asyncio.client.rest import ApiException
from pydantic import ValidationError

from mcp_controller.core.kubernetes_client import (
    KubernetesClient,
    KubernetesClientError,
    KubernetesNamespaceNotFoundError,
    KubernetesPermissionError,
    KubernetesSchemaError,
)


# A spec.address that is not a string — triggers Pydantic ValidationError.
MALFORMED_DEVICE_ITEM = {"spec": {"address": [], "hostname": "x"}}
MALFORMED_HOST_ITEM = {"spec": {"address": [], "hostname": "x"}}


@pytest.fixture
def k8s():
    return KubernetesClient(namespace="test")


class TestSchemaErrorOnList:
    """list_* methods must wrap ValidationError in KubernetesSchemaError."""

    async def test_list_network_device_targets(self, k8s: KubernetesClient) -> None:
        with patch.object(
            k8s, "list_custom_objects", new_callable=AsyncMock, return_value=[MALFORMED_DEVICE_ITEM]
        ):
            with pytest.raises(KubernetesSchemaError) as exc_info:
                await k8s.list_network_device_targets()

            assert isinstance(exc_info.value.__cause__, ValidationError)
            assert isinstance(exc_info.value, KubernetesClientError)

    async def test_list_network_host_targets(self, k8s: KubernetesClient) -> None:
        with patch.object(
            k8s, "list_custom_objects", new_callable=AsyncMock, return_value=[MALFORMED_HOST_ITEM]
        ):
            with pytest.raises(KubernetesSchemaError) as exc_info:
                await k8s.list_network_host_targets()

            assert isinstance(exc_info.value.__cause__, ValidationError)
            assert isinstance(exc_info.value, KubernetesClientError)


class TestSchemaErrorOnGet:
    """get_* methods must wrap ValidationError in KubernetesSchemaError."""

    async def test_get_network_device_target(self, k8s: KubernetesClient) -> None:
        with patch.object(
            k8s,
            "get_custom_object",
            new_callable=AsyncMock,
            return_value=MALFORMED_DEVICE_ITEM,
        ):
            with pytest.raises(KubernetesSchemaError) as exc_info:
                await k8s.get_network_device_target("bng1")

            assert isinstance(exc_info.value.__cause__, ValidationError)
            assert isinstance(exc_info.value, KubernetesClientError)

    async def test_get_network_host_target(self, k8s: KubernetesClient) -> None:
        with patch.object(
            k8s,
            "get_custom_object",
            new_callable=AsyncMock,
            return_value=MALFORMED_HOST_ITEM,
        ):
            with pytest.raises(KubernetesSchemaError) as exc_info:
                await k8s.get_network_host_target("host1")

            assert isinstance(exc_info.value.__cause__, ValidationError)
            assert isinstance(exc_info.value, KubernetesClientError)


class TestCheckNamespaceExists:
    """Tests for `check_namespace_exists`."""

    async def test_raises_on_404(self, k8s: KubernetesClient) -> None:
        """Should raise `KubernetesNamespaceNotFoundError` for missing namespace."""
        mock_core = AsyncMock()
        mock_core.read_namespace = AsyncMock(
            side_effect=ApiException(status=404, reason="Not Found")
        )
        k8s._core_v1 = mock_core
        k8s._custom_objects = AsyncMock()  # skip _ensure_connected

        with pytest.raises(KubernetesNamespaceNotFoundError):
            await k8s.check_namespace_exists("ghost")

    async def test_raises_permission_error_on_403(self, k8s: KubernetesClient) -> None:
        """Should raise `KubernetesPermissionError` for RBAC denial."""
        mock_core = AsyncMock()
        mock_core.read_namespace = AsyncMock(
            side_effect=ApiException(status=403, reason="Forbidden")
        )
        k8s._core_v1 = mock_core
        k8s._custom_objects = AsyncMock()

        with pytest.raises(KubernetesPermissionError):
            await k8s.check_namespace_exists("secret-ns")

    async def test_passes_for_existing_namespace(self, k8s: KubernetesClient) -> None:
        """Should not raise when the namespace exists."""
        mock_core = AsyncMock()
        mock_core.read_namespace = AsyncMock(return_value={"metadata": {"name": "nok"}})
        k8s._core_v1 = mock_core
        k8s._custom_objects = AsyncMock()

        await k8s.check_namespace_exists("nok")  # should not raise
