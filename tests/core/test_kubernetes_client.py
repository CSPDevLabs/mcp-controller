"""Tests for KubernetesClient schema validation error handling."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kubernetes_asyncio.client.rest import ApiException
from pydantic import ValidationError

from mcp_controller.core.k8s_types import Prometheus, PrometheusRule
from mcp_controller.core.kubernetes_client import (
    PROMETHEUS_RULE,
    KubernetesBadRequestError,
    KubernetesClient,
    KubernetesClientError,
    KubernetesFluxManagedError,
    KubernetesNamespaceNotFoundError,
    KubernetesNotFoundError,
    KubernetesPermissionError,
    KubernetesSchemaError,
    is_flux_managed,
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


# ---------------------------------------------------------------------------
# is_flux_managed()
# ---------------------------------------------------------------------------


class TestIsFluxManaged:
    """Unit tests for the `is_flux_managed` helper."""

    def test_none_labels(self) -> None:
        assert is_flux_managed(None) is False

    def test_empty_labels(self) -> None:
        assert is_flux_managed({}) is False

    def test_no_flux_labels(self) -> None:
        assert is_flux_managed({"role": "alert-rules"}) is False

    def test_flux_name_label(self) -> None:
        labels = {"kustomize.toolkit.fluxcd.io/name": "prometheus-rules"}
        assert is_flux_managed(labels) is True

    def test_flux_namespace_label(self) -> None:
        labels = {
            "role": "alert-rules",
            "kustomize.toolkit.fluxcd.io/namespace": "flux-system",
        }
        assert is_flux_managed(labels) is True


# ---------------------------------------------------------------------------
# PrometheusRule / Prometheus list & get
# ---------------------------------------------------------------------------


class TestListAndGetPrometheusRules:
    """Tests for `list_prometheus_rules` / `get_prometheus_rule`."""

    async def test_list_prometheus_rules(
        self, k8s: KubernetesClient, raw_prometheus_rules: list[dict]
    ) -> None:
        with patch.object(
            k8s,
            "list_custom_objects",
            new_callable=AsyncMock,
            return_value=raw_prometheus_rules,
        ) as mock_list:
            result = await k8s.list_prometheus_rules(namespace="nok-bng")

        mock_list.assert_awaited_once_with(
            PROMETHEUS_RULE, namespace="nok-bng", label_selector=None
        )
        assert len(result) == 2
        assert all(isinstance(r, PrometheusRule) for r in result)
        assert result[0].metadata.name == "system-resource-alerts"

    async def test_list_prometheus_rules_defaults_to_all_namespaces(
        self, k8s: KubernetesClient
    ) -> None:
        with patch.object(
            k8s, "list_custom_objects", new_callable=AsyncMock, return_value=[]
        ) as mock_list:
            await k8s.list_prometheus_rules()

        mock_list.assert_awaited_once_with(PROMETHEUS_RULE, namespace="", label_selector=None)

    async def test_list_prometheus_rules_schema_error(self, k8s: KubernetesClient) -> None:
        with patch.object(
            k8s,
            "list_custom_objects",
            new_callable=AsyncMock,
            return_value=[{"spec": {"groups": "not-a-list"}}],
        ):
            with pytest.raises(KubernetesSchemaError) as exc_info:
                await k8s.list_prometheus_rules()
            assert isinstance(exc_info.value.__cause__, ValidationError)

    async def test_get_prometheus_rule(
        self, k8s: KubernetesClient, raw_prometheus_rules: list[dict]
    ) -> None:
        with patch.object(
            k8s,
            "get_custom_object",
            new_callable=AsyncMock,
            return_value=raw_prometheus_rules[0],
        ):
            result = await k8s.get_prometheus_rule("system-resource-alerts", namespace="nok-bng")

        assert isinstance(result, PrometheusRule)
        assert result.metadata.name == "system-resource-alerts"


class TestListPrometheuses:
    """Tests for `list_prometheuses`."""

    async def test_list_prometheuses(
        self, k8s: KubernetesClient, raw_prometheuses: list[dict]
    ) -> None:
        with patch.object(
            k8s, "list_custom_objects", new_callable=AsyncMock, return_value=raw_prometheuses
        ):
            result = await k8s.list_prometheuses()

        assert len(result) == 1
        assert isinstance(result[0], Prometheus)
        assert result[0].spec.rule_selector.match_labels == {"role": "alert-rules"}

    async def test_list_prometheuses_schema_error(self, k8s: KubernetesClient) -> None:
        with patch.object(
            k8s,
            "list_custom_objects",
            new_callable=AsyncMock,
            return_value=[{"spec": {"ruleSelector": "not-an-object"}}],
        ):
            with pytest.raises(KubernetesSchemaError):
                await k8s.list_prometheuses()


# ---------------------------------------------------------------------------
# dry_run_create_prometheus_rule / apply_prometheus_rule
# ---------------------------------------------------------------------------


class TestDryRunCreatePrometheusRule:
    """Tests for `dry_run_create_prometheus_rule`."""

    async def test_delegates_to_dry_run_create_custom_object(
        self, k8s: KubernetesClient
    ) -> None:
        manifest = {"metadata": {"name": "x"}}
        with patch.object(
            k8s, "dry_run_create_custom_object", new_callable=AsyncMock
        ) as mock_dry_run:
            await k8s.dry_run_create_prometheus_rule(manifest, namespace="nok-bng")

        mock_dry_run.assert_awaited_once_with(PROMETHEUS_RULE, manifest, namespace="nok-bng")

    async def test_raises_bad_request_on_invalid_manifest(self, k8s: KubernetesClient) -> None:
        api_exc = ApiException(status=400, reason="Bad Request")
        api_exc.body = (
            '{"kind":"Status","apiVersion":"v1","status":"Failure",'
            '"message":"PrometheusRule in version \\"v1\\" cannot be handled as a '
            'PrometheusRule: json: cannot unmarshal string into Go value of type '
            'v1.Time","code":400}'
        )
        mock_api = AsyncMock()
        mock_api.create_namespaced_custom_object = AsyncMock(side_effect=api_exc)
        k8s._custom_objects = mock_api

        with pytest.raises(KubernetesBadRequestError, match="cannot unmarshal string") as exc_info:
            await k8s.dry_run_create_prometheus_rule(
                {"metadata": {"name": "x"}}, namespace="nok-bng"
            )

        assert str(exc_info.value).startswith("PrometheusRule manifest is invalid:")

    async def test_raises_bad_request_falls_back_to_reason_without_body(
        self, k8s: KubernetesClient
    ) -> None:
        mock_api = AsyncMock()
        mock_api.create_namespaced_custom_object = AsyncMock(
            side_effect=ApiException(status=400, reason="Bad Request")
        )
        k8s._custom_objects = mock_api

        with pytest.raises(KubernetesBadRequestError, match="Bad Request"):
            await k8s.dry_run_create_prometheus_rule(
                {"metadata": {"name": "x"}}, namespace="nok-bng"
            )


class TestApplyPrometheusRule:
    """Tests for `apply_prometheus_rule` — create, replace, and Flux refusal."""

    def _manifest(self, name: str = "system-resource-alerts") -> dict:
        return {"metadata": {"name": name, "namespace": "nok-bng"}, "spec": {"groups": []}}

    async def test_creates_when_object_does_not_exist(self, k8s: KubernetesClient) -> None:
        manifest = self._manifest()
        with (
            patch.object(
                k8s,
                "get_custom_object",
                new_callable=AsyncMock,
                side_effect=KubernetesNotFoundError("not found"),
            ),
            patch.object(
                k8s, "delete_custom_object", new_callable=AsyncMock
            ) as mock_delete,
            patch.object(
                k8s, "create_custom_object", new_callable=AsyncMock, return_value={"ok": True}
            ) as mock_create,
        ):
            result = await k8s.apply_prometheus_rule(manifest, namespace="nok-bng")

        mock_delete.assert_not_awaited()
        mock_create.assert_awaited_once_with(PROMETHEUS_RULE, manifest, namespace="nok-bng")
        assert result == {"ok": True}

    async def test_replaces_existing_non_flux_object(self, k8s: KubernetesClient) -> None:
        manifest = self._manifest()
        existing = {"metadata": {"name": "system-resource-alerts", "labels": {"role": "x"}}}
        with (
            patch.object(
                k8s, "get_custom_object", new_callable=AsyncMock, return_value=existing
            ),
            patch.object(
                k8s, "delete_custom_object", new_callable=AsyncMock
            ) as mock_delete,
            patch.object(
                k8s, "create_custom_object", new_callable=AsyncMock, return_value={"ok": True}
            ) as mock_create,
        ):
            await k8s.apply_prometheus_rule(manifest, namespace="nok-bng")

        mock_delete.assert_awaited_once_with(
            PROMETHEUS_RULE, "system-resource-alerts", namespace="nok-bng"
        )
        mock_create.assert_awaited_once_with(PROMETHEUS_RULE, manifest, namespace="nok-bng")

    async def test_refuses_to_replace_flux_managed_object(self, k8s: KubernetesClient) -> None:
        manifest = self._manifest()
        existing = {
            "metadata": {
                "name": "system-resource-alerts",
                "labels": {"kustomize.toolkit.fluxcd.io/name": "prometheus-rules"},
            }
        }
        with (
            patch.object(
                k8s, "get_custom_object", new_callable=AsyncMock, return_value=existing
            ),
            patch.object(
                k8s, "delete_custom_object", new_callable=AsyncMock
            ) as mock_delete,
            patch.object(
                k8s, "create_custom_object", new_callable=AsyncMock
            ) as mock_create,
        ):
            with pytest.raises(KubernetesFluxManagedError):
                await k8s.apply_prometheus_rule(manifest, namespace="nok-bng")

        mock_delete.assert_not_awaited()
        mock_create.assert_not_awaited()


# ---------------------------------------------------------------------------
# get_prometheus_rule_crd_schema
# ---------------------------------------------------------------------------


class TestGetPrometheusRuleCrdSchema:
    """Tests for `get_prometheus_rule_crd_schema`."""

    def _prime_connected(self, k8s: KubernetesClient, raw_crd: dict) -> None:
        """Bypass `_ensure_connected` by pre-populating the lazy-init slots."""
        k8s._custom_objects = AsyncMock()  # marks the client as "connected"
        mock_apiextensions = AsyncMock()
        mock_apiextensions.read_custom_resource_definition = AsyncMock(return_value=object())
        k8s._apiextensions = mock_apiextensions
        mock_api_client = MagicMock()
        mock_api_client.sanitize_for_serialization = MagicMock(return_value=raw_crd)
        k8s._api_client = mock_api_client

    async def test_returns_spec_subtree_of_storage_version(
        self, k8s: KubernetesClient, prometheus_rule_crd_schema: dict
    ) -> None:
        raw_crd = {
            "spec": {
                "versions": [
                    {"name": "v1beta1", "storage": False, "schema": {"openAPIV3Schema": {}}},
                    {
                        "name": "v1",
                        "storage": True,
                        "schema": {
                            "openAPIV3Schema": {
                                "properties": {"spec": prometheus_rule_crd_schema}
                            }
                        },
                    },
                ]
            }
        }
        self._prime_connected(k8s, raw_crd)

        result = await k8s.get_prometheus_rule_crd_schema()

        assert result == prometheus_rule_crd_schema

    async def test_raises_not_found_when_crd_missing(self, k8s: KubernetesClient) -> None:
        k8s._custom_objects = AsyncMock()
        mock_apiextensions = AsyncMock()
        mock_apiextensions.read_custom_resource_definition = AsyncMock(
            side_effect=ApiException(status=404, reason="Not Found")
        )
        k8s._apiextensions = mock_apiextensions

        with pytest.raises(KubernetesNotFoundError):
            await k8s.get_prometheus_rule_crd_schema()
