"""Tests for BNG MCP tools."""

from unittest.mock import AsyncMock, patch

import pytest

from mcp_controller.bng.tools import register_bng_tools
from mcp_controller.config import Settings
from mcp_controller.core.k8s_types import NetworkDeviceTarget, NetworkHostTarget, Prometheus
from mcp_controller.core.kubernetes_client import (
    KubernetesBadRequestError,
    KubernetesClientError,
    KubernetesFluxManagedError,
)
from mcp_controller.core.types import ErrorResponse

TEST_NAMESPACE = "test-ns"

_CANONICAL_MANIFEST = """\
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: system-resource-alerts
  namespace: nok-bng
  labels:
    role: alert-rules
spec:
  groups:
  - name: system-usage
    rules:
    - alert: HighCpuUsage
      expr: state_system_cpu_summary_usage_cpu_usage > 12
      for: 1m
      labels:
        severity: warning
      annotations:
        summary: "High CPU usage on {{ $labels.source }}"
        description: "CPU usage is at {{ $value }}% (threshold 12%)"
"""


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


# ---------------------------------------------------------------------------
# create_prometheus_alert_rule tool tests
# ---------------------------------------------------------------------------


def _make_mock_k8s_for_alert_tool(
    *,
    prometheuses: list[Prometheus] | None = None,
    existing_rule: dict | None = None,
) -> AsyncMock:
    """Build a mock `KubernetesClient` for `create_prometheus_alert_rule` tests."""
    mock = AsyncMock()
    mock.dry_run_create_prometheus_rule = AsyncMock(return_value=None)
    mock.apply_prometheus_rule = AsyncMock(return_value={})
    mock.list_prometheuses = AsyncMock(return_value=prometheuses or [])
    return mock


def _register_alert_tool(mock_k8s: AsyncMock, settings: Settings):
    """Register BNG tools with `KubernetesClient` patched, return the tool object."""
    from mcp.server.fastmcp import FastMCP

    patcher = patch("mcp_controller.bng.tools.KubernetesClient", return_value=mock_k8s)
    patcher.start()
    try:
        mcp = FastMCP(name="test")
        register_bng_tools(mcp, settings)
        return mcp._tool_manager._tools["create_prometheus_alert_rule"]
    finally:
        patcher.stop()


class TestCreatePrometheusAlertRuleTool:
    """Tests for the `create_prometheus_alert_rule` tool."""

    async def test_write_disabled_by_default(self, mock_ctx: AsyncMock) -> None:
        settings = Settings(k8s_namespace=TEST_NAMESPACE, alerts_write_enabled=False)
        mock_k8s = _make_mock_k8s_for_alert_tool()
        tool = _register_alert_tool(mock_k8s, settings)

        with pytest.raises(ErrorResponse) as exc_info:
            await tool.fn(manifest=_CANONICAL_MANIFEST, ctx=mock_ctx)

        assert exc_info.value.error == "write_disabled"
        mock_k8s.dry_run_create_prometheus_rule.assert_not_awaited()

    async def test_invalid_yaml_manifest(self, mock_ctx: AsyncMock) -> None:
        settings = Settings(k8s_namespace=TEST_NAMESPACE, alerts_write_enabled=True)
        mock_k8s = _make_mock_k8s_for_alert_tool()
        tool = _register_alert_tool(mock_k8s, settings)

        with pytest.raises(ErrorResponse) as exc_info:
            await tool.fn(manifest="not: valid: yaml: [", ctx=mock_ctx)

        assert exc_info.value.error == "invalid_manifest"

    async def test_rejects_wrong_kind(self, mock_ctx: AsyncMock) -> None:
        settings = Settings(k8s_namespace=TEST_NAMESPACE, alerts_write_enabled=True)
        mock_k8s = _make_mock_k8s_for_alert_tool()
        tool = _register_alert_tool(mock_k8s, settings)

        manifest = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n"
        with pytest.raises(ErrorResponse) as exc_info:
            await tool.fn(manifest=manifest, ctx=mock_ctx)

        assert exc_info.value.error == "invalid_manifest"
        assert "PrometheusRule" in exc_info.value.detail

    async def test_rejects_missing_api_version(self, mock_ctx: AsyncMock) -> None:
        settings = Settings(k8s_namespace=TEST_NAMESPACE, alerts_write_enabled=True)
        mock_k8s = _make_mock_k8s_for_alert_tool()
        tool = _register_alert_tool(mock_k8s, settings)

        manifest = "kind: PrometheusRule\nmetadata:\n  name: x\nspec:\n  groups: []\n"
        with pytest.raises(ErrorResponse) as exc_info:
            await tool.fn(manifest=manifest, ctx=mock_ctx)

        assert exc_info.value.error == "invalid_manifest"

    async def test_rejects_recording_rule(self, mock_ctx: AsyncMock) -> None:
        settings = Settings(k8s_namespace=TEST_NAMESPACE, alerts_write_enabled=True)
        mock_k8s = _make_mock_k8s_for_alert_tool()
        tool = _register_alert_tool(mock_k8s, settings)

        manifest = """\
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: recording-rules
  namespace: nok-bng
spec:
  groups:
  - name: g
    rules:
    - record: job:up:avg
      expr: avg(up)
"""
        with pytest.raises(ErrorResponse) as exc_info:
            await tool.fn(manifest=manifest, ctx=mock_ctx)

        assert exc_info.value.error == "recording_rule_rejected"
        mock_k8s.dry_run_create_prometheus_rule.assert_not_awaited()

    async def test_happy_path_create(
        self, mock_ctx: AsyncMock, prometheuses: list[Prometheus]
    ) -> None:
        settings = Settings(k8s_namespace=TEST_NAMESPACE, alerts_write_enabled=True)
        mock_k8s = _make_mock_k8s_for_alert_tool(prometheuses=prometheuses)
        tool = _register_alert_tool(mock_k8s, settings)

        result = await tool.fn(manifest=_CANONICAL_MANIFEST, ctx=mock_ctx)

        mock_k8s.dry_run_create_prometheus_rule.assert_awaited_once()
        mock_k8s.apply_prometheus_rule.assert_awaited_once()
        assert result.name == "system-resource-alerts"
        assert result.namespace == "nok-bng"
        assert result.labels == {"role": "alert-rules"}
        assert result.groups == ["system-usage"]
        assert result.rules_applied == 1
        assert result.matches_rule_selector is True
        assert result.warnings == []

    async def test_defaults_missing_namespace_to_settings(
        self, mock_ctx: AsyncMock
    ) -> None:
        settings = Settings(k8s_namespace="default-ns", alerts_write_enabled=True)
        mock_k8s = _make_mock_k8s_for_alert_tool()
        tool = _register_alert_tool(mock_k8s, settings)

        manifest = """\
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: no-namespace-rule
  labels:
    role: alert-rules
spec:
  groups:
  - name: g
    rules:
    - alert: A
      expr: up == 0
      for: 1m
"""
        result = await tool.fn(manifest=manifest, ctx=mock_ctx)

        assert result.namespace == "default-ns"

    async def test_flux_managed_conflict(self, mock_ctx: AsyncMock) -> None:
        settings = Settings(k8s_namespace=TEST_NAMESPACE, alerts_write_enabled=True)
        mock_k8s = _make_mock_k8s_for_alert_tool()
        mock_k8s.apply_prometheus_rule = AsyncMock(
            side_effect=KubernetesFluxManagedError(
                "PrometheusRule 'nok-bng/system-resource-alerts' is managed by Flux"
            )
        )
        tool = _register_alert_tool(mock_k8s, settings)

        with pytest.raises(ErrorResponse) as exc_info:
            await tool.fn(manifest=_CANONICAL_MANIFEST, ctx=mock_ctx)

        assert exc_info.value.error == "flux_managed_conflict"
        mock_ctx.error.assert_awaited()

    async def test_dry_run_failure_raises_bad_request(self, mock_ctx: AsyncMock) -> None:
        settings = Settings(k8s_namespace=TEST_NAMESPACE, alerts_write_enabled=True)
        mock_k8s = _make_mock_k8s_for_alert_tool()
        mock_k8s.dry_run_create_prometheus_rule = AsyncMock(
            side_effect=KubernetesBadRequestError("unknown field 'spec.groups[0].bogus'")
        )
        tool = _register_alert_tool(mock_k8s, settings)

        with pytest.raises(ErrorResponse) as exc_info:
            await tool.fn(manifest=_CANONICAL_MANIFEST, ctx=mock_ctx)

        assert exc_info.value.error == "bad_request"
        mock_k8s.apply_prometheus_rule.assert_not_awaited()

    async def test_warns_when_no_prometheus_selects_the_rule(
        self, mock_ctx: AsyncMock
    ) -> None:
        settings = Settings(k8s_namespace=TEST_NAMESPACE, alerts_write_enabled=True)
        # No Prometheus instances at all -> label mismatch warning.
        mock_k8s = _make_mock_k8s_for_alert_tool(prometheuses=[])
        tool = _register_alert_tool(mock_k8s, settings)

        result = await tool.fn(manifest=_CANONICAL_MANIFEST, ctx=mock_ctx)

        assert result.matches_rule_selector is False
        assert len(result.warnings) == 1
        assert TEST_NAMESPACE in result.warnings[0]
        assert "will not be evaluated" in result.warnings[0]
        mock_k8s.list_prometheuses.assert_awaited_once_with(namespace=TEST_NAMESPACE)

    async def test_warns_when_labels_do_not_match_selector(
        self, mock_ctx: AsyncMock
    ) -> None:
        settings = Settings(k8s_namespace=TEST_NAMESPACE, alerts_write_enabled=True)
        raw_prom = {
            "metadata": {"name": "p", "namespace": "nok-bng"},
            "spec": {"ruleSelector": {"matchLabels": {"role": "some-other-role"}}},
        }
        mismatched_prom = Prometheus.model_validate(raw_prom)
        mock_k8s = _make_mock_k8s_for_alert_tool(prometheuses=[mismatched_prom])
        tool = _register_alert_tool(mock_k8s, settings)

        result = await tool.fn(manifest=_CANONICAL_MANIFEST, ctx=mock_ctx)

        assert result.matches_rule_selector is False
        assert len(result.warnings) == 1
