"""BNG MCP Resource and ResourceTemplate definitions."""

import json
import logging

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Context

from mcp_controller.bng.common import _raise_resource_error, _serialize_targets, _target_summary
from mcp_controller.config import Settings
from mcp_controller.core.controllers import ControllerId
from mcp_controller.core.mock import mock_intercept
from mcp_controller.core.kubernetes_client import (
    KubernetesClient,
    KubernetesClientError,
)
from mcp_controller.core.registry import (
    ControllerCapability,
    ControllerManifest,
    DataSource,
)
from mcp_controller.core.tags import CapabilityTag, ControllerTag

logger = logging.getLogger(__name__)

BNG_MANIFEST = ControllerManifest(
    controller_id=ControllerId.BNG,
    version="1.0.1",
    display_name="Nokia BNG Observability MCP Controller",
    network_function="bng",
    description=(
        "Intelligent MCP controller for Nokia SROS BNG state and availability. "
        "Provides metrics, logs, health assessment, and engineering SME context "
        "for Broadband Network Gateway operations."
    ),
    capabilities=[
        ControllerCapability(
            name="bng://manifest",
            kind="resource",
            description="Controller manifest with capabilities and dependencies",
            tags=[CapabilityTag.MANIFEST, CapabilityTag.DISCOVERY],
        ),
        ControllerCapability(
            name="bng://namespaces",
            kind="resource",
            description=(
                "All Kubernetes namespaces visible to the controller — "
                "useful for discovering where BNG target CRs may live"
            ),
            tags=[CapabilityTag.DISCOVERY],
        ),
        ControllerCapability(
            name="bng://targets",
            kind="resource",
            description=(
                "All monitored BNG targets (devices and hosts) — "
                "name, address, hostname, kind, namespace"
            ),
            tags=[CapabilityTag.TARGETS, CapabilityTag.DISCOVERY],
        ),
        ControllerCapability(
            name="bng_targets_by_label",
            kind="tool",
            description=(
                "Monitored BNG targets filtered by Kubernetes label selector, "
                "e.g. `role=bng` or `env=prod,role=bng`"
            ),
            tags=[CapabilityTag.TARGETS, CapabilityTag.DISCOVERY],
        ),
        ControllerCapability(
            name="bng://targets/devices",
            kind="resource",
            description="All NetworkDeviceTarget CRs with full spec and status",
            tags=[CapabilityTag.TARGETS, CapabilityTag.DISCOVERY],
        ),
        ControllerCapability(
            name="bng://targets/devices/{namespace}",
            kind="resource_template",
            description="NetworkDeviceTarget CRs filtered by Kubernetes namespace",
            tags=[CapabilityTag.TARGETS, CapabilityTag.DISCOVERY],
        ),
        ControllerCapability(
            name="bng://targets/hosts",
            kind="resource",
            description="All NetworkHostTarget CRs with full spec and status",
            tags=[CapabilityTag.TARGETS, CapabilityTag.DISCOVERY],
        ),
        ControllerCapability(
            name="bng://targets/hosts/{namespace}",
            kind="resource_template",
            description="NetworkHostTarget CRs filtered by Kubernetes namespace",
            tags=[CapabilityTag.TARGETS, CapabilityTag.DISCOVERY],
        ),
        ControllerCapability(
            name="bng_health_summary",
            kind="tool",
            description=(
                "Real-time overall BNG health status — aggregated across all "
                "monitored devices from CPU usage, memory utilisation, and "
                "device availability, with an overall worst-case status."
            ),
            tags=[CapabilityTag.OBSERVABILITY, CapabilityTag.STATUS],
        ),
        ControllerCapability(
            name="read_bng_manifest",
            kind="tool",
            description=(
                "Read the full BNG controller manifest including identity, "
                "capabilities, dependencies, and data sources"
            ),
            tags=[CapabilityTag.MANIFEST, CapabilityTag.DISCOVERY],
        ),
        ControllerCapability(
            name="discover_bng_capabilities",
            kind="tool",
            description=(
                "Discover all BNG capabilities grouped by kind "
                "(resource, resource_template, tool, prompt)"
            ),
            tags=[CapabilityTag.DISCOVERY],
        ),
        ControllerCapability(
            name="read_bng_resource",
            kind="tool",
            description=(
                "Read any registered BNG resource by its `bng://` URI — "
                "supports static resources and parameterized templates"
            ),
            tags=[CapabilityTag.DISCOVERY],
        ),
        ControllerCapability(
            name="bng_device_unavailability_map",
            kind="tool",
            description=(
                "Check whether a BNG device was unreachable during a time window "
                "using absent_over_time() on CPU telemetry. "
                "Supports an optional start_time for historical queries."
            ),
            tags=[CapabilityTag.OBSERVABILITY, CapabilityTag.METRICS],
        ),
        ControllerCapability(
            name="bng_device_cpu_usage",
            kind="tool",
            description=(
                "Query CPU usage time series for a BNG device over a time window "
                "with summary statistics (avg, min, max) and quantiles (p80, p95, p98). "
                "Supports an optional start_time for historical queries."
            ),
            tags=[CapabilityTag.OBSERVABILITY, CapabilityTag.METRICS],
        ),
        ControllerCapability(
            name="bng_device_memory_usage",
            kind="tool",
            description=(
                "Query memory utilization (%) time series for a BNG device "
                "over a time window with summary statistics (avg, min, max) "
                "and quantiles (p80, p95, p98). "
                "Supports an optional start_time for historical queries."
            ),
            tags=[CapabilityTag.OBSERVABILITY, CapabilityTag.METRICS, CapabilityTag.RESOURCES],
        ),
        ControllerCapability(
            name="bng_device_hosts_stats",
            kind="tool",
            description=(
                "Return peak and current IPv4/IPv6 subscriber host counts "
                "for a BNG device using instant queries on "
                "`state_subscriber_mgmt_statistics_total_hosts_peak_value` "
                "and `state_subscriber_mgmt_statistics_total_hosts_current_value`."
            ),
            tags=[CapabilityTag.METRICS, CapabilityTag.SUBSCRIBER_MGMT, CapabilityTag.RESOURCES],
        ),
        ControllerCapability(
            name="ppp_sessions_total_established",
            kind="tool",
            description=(
                "Query established PPP sessions for a BNG device using "
                "`state_subscriber_mgmt_statistics_sessions_current_value` "
                'filtered by `sessions_counter="ppp-sessions-total-established"`. '
                "Returns current count and min/max over the window."
            ),
            tags=[CapabilityTag.METRICS, CapabilityTag.SUBSCRIBER_MGMT, CapabilityTag.RESOURCES],
        ),
        ControllerCapability(
            name="sap_instances_allocated",
            kind="tool",
            description=(
                "Query SAP instances allocated per (card, mda) for a BNG device "
                "using `state_card_mda_resource_usage_sap_instances_allocated`. "
                "Returns currently allocated and min/max over the window per "
                "matching slot. `card` and `mda` filters are optional."
            ),
            tags=[CapabilityTag.OBSERVABILITY, CapabilityTag.METRICS, CapabilityTag.RESOURCES],
        ),
        ControllerCapability(
            name="ingress_policers_allocated",
            kind="tool",
            description=(
                "Query ingress policer allocation per (card, fp) for a BNG device "
                "using `state_card_fp_resource_usage_ingress_policers_allocated` and "
                "`state_card_fp_resource_usage_ingress_policers_total`. Returns "
                "currently allocated/total counts, current/min/max utilisation (%) "
                "per FP. `card` and `fp` filters are optional."
            ),
            tags=[CapabilityTag.OBSERVABILITY, CapabilityTag.METRICS, CapabilityTag.RESOURCES],
        ),
        ControllerCapability(
            name="egress_policers_allocated",
            kind="tool",
            description=(
                "Query egress policer allocation per (card, fp) for a BNG device "
                "using `state_card_fp_resource_usage_egress_policers_allocated` and "
                "`state_card_fp_resource_usage_egress_policers_total`. Returns "
                "currently allocated/total counts, current/min/max utilisation (%) "
                "per FP. `card` and `fp` filters are optional."
            ),
            tags=[CapabilityTag.OBSERVABILITY, CapabilityTag.METRICS, CapabilityTag.RESOURCES],
        ),
        ControllerCapability(
            name="ingress_queues_allocated",
            kind="tool",
            description=(
                "Query ingress queue allocation per (card, fp) for a BNG device "
                "using `state_card_fp_resource_usage_ingress_queues_allocated` and "
                "`state_card_fp_resource_usage_ingress_queues_total`. Returns "
                "currently allocated/total counts, current/min/max utilisation (%) "
                "per FP. `card` and `fp` filters are optional."
            ),
            tags=[CapabilityTag.OBSERVABILITY, CapabilityTag.METRICS, CapabilityTag.RESOURCES],
        ),
        ControllerCapability(
            name="egress_queues_allocated",
            kind="tool",
            description=(
                "Query egress queue allocation per (card, fp) for a BNG device "
                "using `state_card_fp_resource_usage_egress_queues_allocated` and "
                "`state_card_fp_resource_usage_egress_queues_total`. Returns "
                "currently allocated/total counts, current/min/max utilisation (%) "
                "per FP. `card` and `fp` filters are optional."
            ),
            tags=[CapabilityTag.OBSERVABILITY, CapabilityTag.METRICS, CapabilityTag.RESOURCES],
        ),
        ControllerCapability(
            name="subscriber_next_hop_entries_allocated",
            kind="tool",
            description=(
                "Query subscriber next-hop entries allocation for a BNG device "
                "using `state_system_resource_usage_subscriber_next_hop_entries_allocated` "
                "and `state_system_resource_usage_subscriber_next_hop_entries_total`. "
                "Returns currently allocated/total counts, current/min/max utilisation (%) "
                "device-wide (no card/fp dimension)."
            ),
            tags=[CapabilityTag.OBSERVABILITY, CapabilityTag.METRICS, CapabilityTag.RESOURCES],
        ),
        ControllerCapability(
            name="bng-overview",
            kind="prompt",
            description=(
                "Session bootstrap prompt — returns the BNG controller manifest "
                "and grouped capability summary so the LLM knows what is available"
            ),
            tags=[CapabilityTag.MANIFEST, CapabilityTag.DISCOVERY],
        ),
    ],
    # Other MCP Controllers of EAC ecosystem
    dependencies=[],
    data_sources=[DataSource.PROMETHEUS, DataSource.LOKI, DataSource.KUBERNETES],
    tags=[
        ControllerTag.BROADBAND,
        ControllerTag.SUBSCRIBER_MGMT,
        ControllerTag.SROS,
        ControllerTag.NOKIA,
        ControllerTag.BNG,
    ],
)


# Registration
# -------------------------------------------------------------------------------------------------


def register_bng_resources(mcp: FastMCP, settings: Settings) -> None:
    """Register BNG resources and resource templates on the MCP server.

    Args:
        mcp:      The FastMCP server instance.
        settings: Application settings (provides `k8s_namespace`).

    Returns:
        None
    """
    k8s = KubernetesClient(namespace=settings.k8s_namespace)

    # Static resources
    # -------------------------------------------------------------------------------------------------

    @mcp.resource("bng://manifest")
    @mock_intercept(settings)
    async def bng_manifest() -> str:
        """BNG controller manifest: capabilities, version, and dependencies.
        Args:
            None

        Returns:
            Pretty-printed JSON string.
        """
        return BNG_MANIFEST.model_dump_json(indent=2)

    @mcp.resource("bng://namespaces")
    @mock_intercept(settings)
    async def bng_namespaces() -> str:
        """All Kubernetes namespaces visible to the controller.

        Returns:
            Pretty-printed JSON array of `Namespace` models.
        """
        logger.info("Listing all Kubernetes namespaces")
        try:
            namespaces = await k8s.list_namespaces()
        except KubernetesClientError as exc:
            await _raise_resource_error("bng://namespaces", exc)
        return json.dumps(
            [ns.model_dump(mode="json", by_alias=True, exclude_none=True) for ns in namespaces],
            indent=2,
        )

    @mcp.resource("bng://targets")
    @mock_intercept(settings)
    async def bng_targets() -> str:
        """All monitored BNG targets — name, address, hostname, kind, namespace."""
        logger.info("Listing all BNG targets (devices + hosts)")
        try:
            devices = await k8s.list_network_device_targets()
            hosts = await k8s.list_network_host_targets()
        except KubernetesClientError as exc:
            await _raise_resource_error("bng://targets", exc)
        summaries = [_target_summary(t) for t in devices] + [_target_summary(t) for t in hosts]
        return json.dumps([s.model_dump(mode="json") for s in summaries], indent=2)

    @mcp.resource("bng://targets/devices")
    @mock_intercept(settings)
    async def bng_target_devices() -> str:
        """All `NetworkDeviceTarget` CRs with full spec and status."""
        logger.info("Listing all NetworkDeviceTarget CRs")
        try:
            devices = await k8s.list_network_device_targets()
        except KubernetesClientError as exc:
            await _raise_resource_error("bng://targets/devices", exc)
        return _serialize_targets(devices)

    @mcp.resource("bng://targets/hosts")
    @mock_intercept(settings)
    async def bng_target_hosts() -> str:
        """All `NetworkHostTarget` CRs with full spec and status."""
        logger.info("Listing all NetworkHostTarget CRs")
        try:
            hosts = await k8s.list_network_host_targets()
        except KubernetesClientError as exc:
            await _raise_resource_error("bng://targets/hosts", exc)
        return _serialize_targets(hosts)

    # Resource templates
    # -------------------------------------------------------------------------------------------------

    @mcp.resource("bng://targets/devices/{namespace}")
    @mock_intercept(settings)
    async def bng_target_devices_by_ns(namespace: str, ctx: Context) -> str:
        """`NetworkDeviceTarget` CRs in a specific namespace."""
        logger.info("Listing NetworkDeviceTarget CRs in namespace: %s", namespace)
        await ctx.info(f"Listing NetworkDeviceTarget CRs in namespace: {namespace}")
        try:
            devices = await k8s.list_network_device_targets(namespace=namespace)
        except KubernetesClientError as exc:
            await _raise_resource_error(f"bng://targets/devices/{namespace}", exc, ctx)
        return _serialize_targets(devices)

    @mcp.resource("bng://targets/hosts/{namespace}")
    @mock_intercept(settings)
    async def bng_target_hosts_by_ns(namespace: str, ctx: Context) -> str:
        """`NetworkHostTarget` CRs in a specific namespace."""
        logger.info("Listing NetworkHostTarget CRs in namespace: %s", namespace)
        await ctx.info(f"Listing NetworkHostTarget CRs in namespace: {namespace}")
        try:
            hosts = await k8s.list_network_host_targets(namespace=namespace)
        except KubernetesClientError as exc:
            await _raise_resource_error(f"bng://targets/hosts/{namespace}", exc, ctx)
        return _serialize_targets(hosts)
