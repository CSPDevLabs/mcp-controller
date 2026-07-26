"""BNG MCP Tool definitions."""

import logging
import sys
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ResourceError
from mcp.server.fastmcp.server import Context

from mcp_controller.bng.common import (
    _raise_k8s_error,
    _target_summary,
    compute_sample_stats,
    execute_instant_query,
    execute_range_query,
    prepare_device,
    resolve_query_window,
)
from mcp_controller.bng.resources import BNG_MANIFEST
from mcp_controller.bng.types import (
    BngHealthSummary,
    DeviceCpuUsageResult,
    DeviceHealth,
    DeviceHostsStatsResult,
    DeviceMemoryUsageResult,
    DeviceUnavailabilityResult,
    HealthStatus,
    PolicerDirection,
    PolicersAllocatedResult,
    PolicersAllocationBySlot,
    PppSessionsTotalEstablishedResult,
    QueuesAllocatedResult,
    QueuesAllocationBySlot,
    QueuesDirection,
    SapAllocationBySlot,
    SapInstancesAllocatedResult,
    SubscriberNextHopEntriesResult,
    TargetSummary,
)
from mcp_controller.core.types import ErrorResponse, MetricResult
from mcp_controller.core.registry import ControllerManifest
from mcp_controller.config import Settings
from mcp_controller.core.mock import mock_intercept
from mcp_controller.core.kubernetes_client import (
    KubernetesClient,
    KubernetesClientError,
)
from mcp_controller.core.prometheus_client import (
    PrometheusClient,
    PrometheusClientError,
)

logger = logging.getLogger(__name__)


# Health-summary thresholds
# -------------------------------------------------------------------------------------------------

# CPU usage (%) boundaries: >= red is red, >= yellow is yellow, else green.
_CPU_RED_PCT = 80.0
_CPU_YELLOW_PCT = 70.0

# Memory utilisation (%) boundaries: >= red is red, >= yellow is yellow, else green.
_MEM_RED_PCT = 85.0
_MEM_YELLOW_PCT = 70.0

# Severity ordering used to pick the worst status across signals/devices.
_STATUS_RANK: dict[HealthStatus, int] = {"green": 0, "unknown": 1, "yellow": 2, "red": 3}


def _worst_status(current: HealthStatus, candidate: HealthStatus) -> HealthStatus:
    """Return the more severe of two statuses.

    Ordering is `red` > `yellow` > `unknown` > `green`.

    Args:
        current: The status accumulated so far.
        candidate: The status to compare against.

    Returns:
        The more severe `HealthStatus`.
    """
    return candidate if _STATUS_RANK[candidate] > _STATUS_RANK[current] else current


def _classify(value: float | None, yellow: float, red: float) -> HealthStatus:
    """Classify a utilisation percentage against yellow/red thresholds.

    Args:
        value: The utilisation percentage, or `None` when no data exists.
        yellow: Inclusive lower bound for a `"yellow"` status.
        red: Inclusive lower bound for a `"red"` status.

    Returns:
        `"unknown"` when `value` is `None`, otherwise `"green"`,
        `"yellow"`, or `"red"`.
    """
    if value is None:
        return "unknown"
    if value >= red:
        return "red"
    if value >= yellow:
        return "yellow"
    return "green"


async def _instant_value(prom: PrometheusClient, promql: str) -> float | None:
    """Run an instant query and return its first sample value.

    Failures (unreachable Prometheus, query errors, empty results) are
    swallowed and reported as `None` so a single flaky device never
    breaks the whole health summary.

    Args:
        prom: The Prometheus client instance.
        promql: The PromQL instant-query expression.

    Returns:
        The first sample value, or `None` when unavailable.
    """
    try:
        result = await prom.query(promql)
    except PrometheusClientError as exc:
        logger.warning("Health-summary query failed: %s (%s)", promql, exc)
        return None
    if isinstance(result, MetricResult) and result.series and result.series[0].samples:
        return result.series[0].samples[0].value
    return None


async def _assess_device_health(prom: PrometheusClient, source: str) -> DeviceHealth:
    """Assess a single device's health from CPU and memory instant queries.

    Args:
        prom: The Prometheus client instance.
        source: Fully qualified `namespace/name` Prometheus `source` label.

    Returns:
        A populated `DeviceHealth` model.
    """
    cpu = await _instant_value(
        prom,
        f'state_system_cpu_summary_usage_cpu_usage{{cpu_sample_period="60",source="{source}"}}',
    )
    in_use = f'state_system_memory_pools_summary_total_in_use{{source="{source}"}}'
    available = f'state_system_memory_pools_summary_available_memory{{source="{source}"}}'
    memory = await _instant_value(
        prom,
        f"sum by(source) ({in_use})"
        f" / on(source) "
        f"(sum by(source) ({in_use}) + sum by(source) ({available}))"
        f" * 100",
    )

    # A device reporting neither CPU nor memory is treated as unreachable.
    if cpu is None and memory is None:
        return DeviceHealth(
            device=source,
            status="red",
            available=False,
            cpu_usage_pct=None,
            memory_usage_pct=None,
            reasons=["device is not reporting CPU or memory metrics"],
        )

    cpu_status = _classify(cpu, _CPU_YELLOW_PCT, _CPU_RED_PCT)
    mem_status = _classify(memory, _MEM_YELLOW_PCT, _MEM_RED_PCT)
    reasons: list[str] = []
    if cpu is not None and cpu_status in ("yellow", "red"):
        reasons.append(f"CPU usage {cpu:.1f}% is {cpu_status}")
    if memory is not None and mem_status in ("yellow", "red"):
        reasons.append(f"memory utilisation {memory:.1f}% is {mem_status}")

    return DeviceHealth(
        device=source,
        status=_worst_status(cpu_status, mem_status),
        available=True,
        cpu_usage_pct=cpu,
        memory_usage_pct=memory,
        reasons=reasons,
    )


def register_bng_tools(mcp: FastMCP, settings: Settings) -> None:
    """Register BNG tools on the MCP server.

    Args:
        mcp:      The FastMCP server instance.
        settings: Application settings (provides `k8s_namespace`).

    Returns:
        None
    """
    k8s = KubernetesClient(namespace=settings.k8s_namespace)
    prom = PrometheusClient(
        base_url=settings.prometheus_url,
        verify=not settings.tls_skip_verify,
    )

    @mcp.tool()
    @mock_intercept(settings)
    async def bng_targets_by_label(label_selector: str, ctx: Context) -> list[TargetSummary]:
        """Return BNG targets filtered by a Kubernetes label selector.

        The `label_selector` follows standard K8s syntax,
        e.g. `role=bng` or `env=prod,role=bng`.

        Args:
            label_selector: K8s label selector expression.
            ctx: MCP request context.

        Returns:
            List of `TargetSummary` objects (name, address, hostname, kind,
            namespace).

        Raises:
            ErrorResponse: on any Kubernetes API error.
        """
        logger.info("Listing BNG targets with label selector: %s", label_selector)
        await ctx.info(f"Listing BNG targets with label selector: {label_selector}")
        try:
            devices = await k8s.list_network_device_targets(label_selector=label_selector)
            hosts = await k8s.list_network_host_targets(label_selector=label_selector)
        except KubernetesClientError as exc:
            await _raise_k8s_error("bng://targets/by-label", exc, ctx)
        return [_target_summary(t) for t in devices] + [_target_summary(t) for t in hosts]

    @mcp.tool()
    @mock_intercept(settings)
    async def read_bng_manifest(ctx: Context) -> ControllerManifest:
        """Return the full BNG controller manifest.

        The manifest contains controller identity (id, version, display name),
        description, declared capabilities, dependencies on other controllers,
        backend data sources, and classification tags.

        Call this tool first to understand what the BNG controller provides.

        Args:
            ctx: MCP request context.

        Returns:
            The `ControllerManifest` for the BNG controller.
        """
        logger.info("Reading BNG manifest")
        await ctx.info("Reading BNG controller manifest")
        return BNG_MANIFEST

    @mcp.tool()
    @mock_intercept(settings)
    async def discover_bng_capabilities(
        ctx: Context,
    ) -> dict[str, list[dict[str, object]]]:
        """Discover all BNG controller capabilities grouped by kind.

        Returns available resources, resource templates, tools, and prompts
        with their names, descriptions, and tags. Use this to find what
        data and actions are available before calling `read_bng_resource`
        or other tools.

        Args:
            ctx: MCP request context.

        Returns:
            Mapping keyed by capability kind (`resource`,
            `resource_template`, `tool`, `prompt`), each value a list of
            entries with `name`, `description`, and `tags`.
        """
        logger.info("Discovering BNG capabilities")
        await ctx.info("Discovering BNG controller capabilities")
        grouped: dict[str, list[dict[str, object]]] = {}
        for cap in BNG_MANIFEST.capabilities:
            grouped.setdefault(cap.kind, []).append(
                {
                    "name": cap.name,
                    "description": cap.description,
                    "tags": [str(t) for t in cap.tags],
                }
            )
        return grouped

    @mcp.tool()
    @mock_intercept(settings)
    async def read_bng_resource(uri: str, ctx: Context) -> str:
        """Read any registered BNG resource by its URI.

        Supports both static resources (e.g. `bng://targets`) and
        parameterized resource templates (e.g. `bng://targets/devices/nok-bng`).
        Call `discover_bng_capabilities` first to find available URIs.

        This tool delegates to the server's resource registry, so newly
        registered resources become accessible automatically without
        code changes here.

        Args:
            uri: Resource URI, e.g. `"bng://targets"` or
                `"bng://targets/devices/nok-bng"`.
            ctx: MCP request context.

        Returns:
            Raw resource payload string.

        Raises:
            ValueError: If the URI scheme is not `bng://`.
            ErrorResponse: If the resource is not found or cannot be read.
        """
        if not uri.startswith("bng://"):
            raise ValueError(f"Invalid URI scheme: {uri!r}. Only `bng://` URIs are supported.")
        logger.info("Reading BNG resource: %s", uri)
        await ctx.info(f"Reading BNG resource: {uri}")
        try:
            results = await mcp.read_resource(uri)
            parts = [r.content for r in results if isinstance(r.content, str)]
            return "\n".join(parts) if parts else ""
        # `ResourceManager.get_resource()` raises `ValueError`,
        # not `ResourceError` for unknown resource.
        except ValueError as exc:
            logger.error("Unknown resource: %s — %s", uri, exc)
            raise ErrorResponse(
                error="resource_not_found",
                detail=str(exc),
                uri=uri,
            ) from exc
        # ResourceError fires when a resource is found
        # but its `content = await resource.read()` fails.
        except ResourceError as exc:
            logger.error("Resource read failed: %s — %s", uri, exc)
            raise ErrorResponse(
                error="resource_read_error",
                detail=str(exc),
                uri=uri,
            ) from exc

    @mcp.tool()
    @mock_intercept(settings)
    async def bng_health_summary(ctx: Context) -> BngHealthSummary:
        """Return an aggregated real-time health summary across all BNG devices.

        Lists every `NetworkDeviceTarget` and, for each, runs instant
        queries for CPU usage (`state_system_cpu_summary_usage_cpu_usage`)
        and memory utilisation (`in_use / (in_use + available) * 100`).
        Each device is classified `green`/`yellow`/`red` — or `red` when
        it is not reporting metrics — and the overall status is the worst
        observed across all devices.

        Per-device Prometheus failures are tolerated: an unreachable device
        is reported as `red`/unavailable rather than failing the whole
        summary.

        Args:
            ctx: MCP request context.

        Returns:
            `BngHealthSummary` with `overall_status`, `total_devices`,
            `status_counts`, per-device `devices`, and `generated_at`.

        Raises:
            ErrorResponse: on any Kubernetes API error while listing targets.
        """
        logger.info("Building BNG health summary")
        await ctx.info("Building BNG health summary across all devices")
        try:
            devices = await k8s.list_network_device_targets()
        except KubernetesClientError as exc:
            await _raise_k8s_error("bng_health_summary", exc, ctx)

        assessments: list[DeviceHealth] = []
        for target in devices:
            if (
                target.spec
                and target.spec.gnmic
                and target.spec.gnmic.enabled
                and target.spec.gnmic.labels
                and target.spec.gnmic.labels.get("role") == "bng"
            ):
                name = target.metadata.name if target.metadata else None
                namespace = (
                    target.metadata.namespace
                    if target.metadata and target.metadata.namespace
                    else settings.k8s_namespace
                )
                source = f"{namespace}/{name}"
                assessments.append(await _assess_device_health(prom, source))

        status_counts: dict[HealthStatus, int] = {
            "green": 0,
            "yellow": 0,
            "red": 0,
            "unknown": 0,
        }
        overall: HealthStatus = "unknown"
        for index, health in enumerate[DeviceHealth](assessments):
            status_counts[health.status] += 1
            overall = health.status if index == 0 else _worst_status(overall, health.status)

        return BngHealthSummary(
            overall_status=overall,
            total_devices=len(assessments),
            status_counts=status_counts,
            devices=assessments,
            generated_at=datetime.now(tz=timezone.utc),
        )

    @mcp.tool()
    @mock_intercept(settings)
    async def bng_device_unavailability_map(
        device: str, interval: str, step: str, ctx: Context, start_time: str = ""
    ) -> DeviceUnavailabilityResult:
        """Return an availability time series for a BNG device over a window.

        Executes a range query with `absent_over_time()` across CPU,
        next-hop, and interface-octets metrics to produce a matrix where
        each step is `1` (device absent) or `0` (device reporting).

        Args:
            device: Device name, with or without a namespace prefix.
                Both `"clab-sros-bngt-bng1"` (resolved via the default
                namespace) and `"nok-bng/clab-sros-bngt-bng1"` are
                accepted.  The resolved value is used as the Prometheus
                `source` label.
            interval: Lookback window duration. Accepts Prometheus-style
                shorthand (`"5m"`, `"1h"`, `"30s"`) and human-friendly
                forms (`"5min"`, `"2hours"`, `"7days"`, `"1week"`).
                Case-insensitive. Fractional values are supported
                (e.g. `"1.5h"`).
            step: Query resolution step, e.g. `"15s"`, `"1m"`.
            ctx: MCP request context.
            start_time: Optional ISO 8601 start time with timezone,
                e.g. `"2026-04-07T12:00:00+00:00"`. When empty the
                window starts at `now - interval`.

        Returns:
            `DeviceUnavailabilityResult` with `device`, `interval`, `step`,
            `unavailable_steps` count, `currently_available` (last step
            state), and the raw `MetricResult` matrix.

        Raises:
            ErrorResponse: on invalid parameters, device lookup failure,
                or Prometheus query error.
        """
        device = await prepare_device(device, settings.k8s_namespace, mcp, ctx)
        logger.info(
            "bng_device_unavailability_map: device=%s interval=%s step=%s start_time=%s",
            device,
            interval,
            step,
            start_time or "auto",
        )
        await ctx.info(f"Checking unavailability for {device} over {interval} @ {step}")

        window = resolve_query_window(interval, step, device, start_time)

        # label_replace(
        #   absent_over_time(
        #     {__name__=~"state_system_cpu_summary_usage_cpu_time|state_system_resource_usage_subscriber_next_hop_entries_total|
        # state_router_interface_statistics_ip_in_octets", source=~".*bngt-bng1"}[$__interval]
        #   ),
        #   "source", "nok-bng/clab-sros-bngt-bng1", "", ""
        # )
        # or
        # count by (source) (
        #   {__name__=~"state_system_cpu_summary_usage_cpu_time|state_system_resource_usage_subscriber_next_hop_entries_total|
        # state_router_interface_statistics_ip_in_octets", source=~".*bngt-bng1"}
        # ) * 0
        _unavail_metrics = (
            "state_system_cpu_summary_usage_cpu_time|"
            "state_system_resource_usage_subscriber_next_hop_entries_total|"
            "state_router_interface_statistics_ip_in_octets"
        )
        promql = (
            f"label_replace("
            f"absent_over_time("
            f'{{__name__=~"{_unavail_metrics}", source=~".*{device}"}}[{window.prom_interval}]'
            f"), "
            f'"source", "{device}", "", ""'
            f") "
            f"or "
            f"count by (source) ("
            f'{{__name__=~"{_unavail_metrics}", source=~".*{device}"}}'
            f") * 0"
        )

        result = await execute_range_query(
            prom=prom,
            promql=promql,
            start=window.start_dt.isoformat(),
            end=window.end_dt.isoformat(),
            step=window.prom_step,
            device=device,
            interval=interval,
            tool_name=sys._getframe(0).f_code.co_name,
        )

        # Count steps where the device was absent (value == 1).
        unavailable_steps = 0
        last_available: bool | None = None
        for series in result.series:
            for sample in series.samples:
                if sample.value == 1.0:
                    unavailable_steps += 1
            if series.samples:
                last_available = series.samples[-1].value == 0.0

        return DeviceUnavailabilityResult(
            device=device,
            interval=interval,
            step=step,
            unavailable_steps=unavailable_steps,
            currently_available=last_available,
            result=result,
        )

    @mcp.tool()
    @mock_intercept(settings)
    async def bng_device_cpu_usage(
        device: str, interval: str, step: str, ctx: Context, start_time: str = ""
    ) -> DeviceCpuUsageResult:
        """Return CPU usage time series for a BNG device over a window.

        Executes a range query on
        `state_system_cpu_summary_usage_cpu_usage` (with
        `cpu_sample_period="60"`) to produce a matrix of CPU usage
        values at each step. The device is matched by the Prometheus
        `source` label and corresponds to `bng://targets/devices`.

        The response includes summary statistics (average, min, max,
        p80, p95, p98 quantiles) and the full raw metric matrix.

        Args:
            device: Device name, with or without a namespace prefix.
                Both `"clab-sros-bngt-bng1"` (resolved via the default
                namespace) and `"nok-bng/clab-sros-bngt-bng1"` are
                accepted.  The resolved value is used as the Prometheus
                `source` label.
            interval: Lookback window duration. Accepts Prometheus-style
                shorthand (`"5m"`, `"1h"`, `"30s"`) and human-friendly
                forms (`"5min"`, `"2hours"`, `"7days"`, `"1week"`).
                Case-insensitive. Fractional values are supported
                (e.g. `"1.5h"`).
            step: Query resolution step, e.g. `"15s"`, `"1m"`.
            ctx: MCP request context.
            start_time: Optional ISO 8601 start time with timezone,
                e.g. `"2026-04-07T12:00:00+00:00"`. When empty the
                window starts at `now - interval`.

        Returns:
            `DeviceCpuUsageResult` with `device`, `interval`, `step`,
            `sample_count`, `avg_cpu_usage`, `min_cpu_usage`,
            `max_cpu_usage`, and the raw `MetricResult` matrix.

        Raises:
            ErrorResponse: on invalid parameters, device lookup failure,
                or Prometheus query error.
        """
        device = await prepare_device(device, settings.k8s_namespace, mcp, ctx)
        logger.info(
            "bng_device_cpu_usage: device=%s interval=%s step=%s start_time=%s",
            device,
            interval,
            step,
            start_time or "auto",
        )
        await ctx.info(f"Querying CPU usage for {device} over {interval} @ {step}")

        window = resolve_query_window(interval, step, device, start_time)

        promql = (
            "state_system_cpu_summary_usage_cpu_usage"
            f'{{cpu_sample_period="60",source="{device}"}}'
        )

        result = await execute_range_query(
            prom=prom,
            promql=promql,
            start=window.start_dt.isoformat(),
            end=window.end_dt.isoformat(),
            step=window.prom_step,
            device=device,
            interval=interval,
            tool_name=sys._getframe(0).f_code.co_name,
        )

        # Compute summary stats across all samples in the matrix.
        stats = compute_sample_stats(result)

        # Calculate 80, 95, 98% quantile
        quantiles: dict[float, float | None] = {}
        for q in [0.80, 0.95, 0.98]:
            promql_xxq = (
                f"quantile_over_time({q}, state_system_cpu_summary_usage_cpu_usage"
                f'{{cpu_sample_period=60,source="{device}"}}'
                f"[{window.prom_interval}:{window.prom_step}])"
            )

            quantile_result = await execute_range_query(
                prom=prom,
                promql=promql_xxq,
                start=window.start_dt.isoformat(),
                end=window.end_dt.isoformat(),
                step=window.prom_step,
                device=device,
                interval=interval,
                tool_name=sys._getframe(0).f_code.co_name,
            )

            if quantile_result.series and quantile_result.series[0].samples:
                quantiles[q] = quantile_result.series[0].samples[-1].value
            else:
                quantiles[q] = None

        return DeviceCpuUsageResult(
            device=device,
            interval=interval,
            step=step,
            sample_count=stats.count,
            avg_cpu_usage=stats.avg,
            min_cpu_usage=stats.min,
            max_cpu_usage=stats.max,
            p80_cpu_usage=quantiles.get(0.80),
            p95_cpu_usage=quantiles.get(0.95),
            p98_cpu_usage=quantiles.get(0.98),
            result=result,
        )

    @mcp.tool()
    @mock_intercept(settings)
    async def bng_device_memory_usage(
        device: str, interval: str, step: str, ctx: Context, start_time: str = ""
    ) -> DeviceMemoryUsageResult:
        """Return memory utilization time series for a BNG device over a window.

        Computes memory utilization as a percentage using:
        `sum by(source)(in_use) / (sum by(source)(in_use) + sum by(source)(available)) * 100`
        where `in_use` is `state_system_memory_pools_summary_total_in_use`
        and `available` is `state_system_memory_pools_summary_available_memory`.
        The device is matched by the Prometheus `source` label and
        corresponds to `bng://targets/devices`.

        The response includes summary statistics (average, min, max,
        p80, p95, p98 quantiles) and the full raw metric matrix.

        Args:
            device: Device name, with or without a namespace prefix.
                Both `"clab-sros-bngt-bng1"` (resolved via the default
                namespace) and `"nok-bng/clab-sros-bngt-bng1"` are
                accepted.  The resolved value is used as the Prometheus
                `source` label.
            interval: Lookback window duration. Accepts Prometheus-style
                shorthand (`"5m"`, `"1h"`, `"30s"`) and human-friendly
                forms (`"5min"`, `"2hours"`, `"7days"`, `"1week"`).
                Case-insensitive. Fractional values are supported
                (e.g. `"1.5h"`).
            step: Query resolution step, e.g. `"15s"`, `"1m"`.
            ctx: MCP request context.
            start_time: Optional ISO 8601 start time with timezone,
                e.g. `"2026-04-07T12:00:00+00:00"`. When empty the
                window starts at `now - interval`.

        Returns:
            `DeviceMemoryUsageResult` with `device`, `interval`, `step`,
            `sample_count`, `avg_memory_usage_pct`, `min_memory_usage_pct`,
            `max_memory_usage_pct`, and the raw `MetricResult` matrix.

        Raises:
            ErrorResponse: on invalid parameters, device lookup failure,
                or Prometheus query error.
        """
        device = await prepare_device(device, settings.k8s_namespace, mcp, ctx)
        logger.info(
            "bng_device_memory_usage: device=%s interval=%s step=%s start_time=%s",
            device,
            interval,
            step,
            start_time or "auto",
        )
        await ctx.info(f"Querying memory usage for {device} over {interval} @ {step}")

        window = resolve_query_window(interval, step, device, start_time)

        in_use = f'state_system_memory_pools_summary_total_in_use{{source="{device}"}}'
        available = f'state_system_memory_pools_summary_available_memory{{source="{device}"}}'
        promql = (
            f"sum by(source) ({in_use})"
            f" / on(source) "
            f"(sum by(source) ({in_use}) + sum by(source) ({available}))"
            f" * 100"
        )

        result = await execute_range_query(
            prom=prom,
            promql=promql,
            start=window.start_dt.isoformat(),
            end=window.end_dt.isoformat(),
            step=window.prom_step,
            device=device,
            interval=interval,
            tool_name=sys._getframe(0).f_code.co_name,
        )

        stats = compute_sample_stats(result)

        # Calculate 80, 95, 98% quantile via subquery on the computed expression.
        mem_pct_expr = (
            f"sum by(source) ({in_use})"
            f" / on(source) "
            f"(sum by(source) ({in_use}) + sum by(source) ({available}))"
            f" * 100"
        )
        quantiles: dict[float, float | None] = {}
        for q in [0.80, 0.95, 0.98]:
            promql_xxq = (
                f"quantile_over_time({q}, ({mem_pct_expr})"
                f"[{window.prom_interval}:{window.prom_step}])"
            )

            quantile_result = await execute_range_query(
                prom=prom,
                promql=promql_xxq,
                start=window.start_dt.isoformat(),
                end=window.end_dt.isoformat(),
                step=window.prom_step,
                device=device,
                interval=interval,
                tool_name=sys._getframe(0).f_code.co_name,
            )

            if quantile_result.series and quantile_result.series[0].samples:
                quantiles[q] = quantile_result.series[0].samples[-1].value
            else:
                quantiles[q] = None

        return DeviceMemoryUsageResult(
            device=device,
            interval=interval,
            step=step,
            sample_count=stats.count,
            avg_memory_usage_pct=stats.avg,
            min_memory_usage_pct=stats.min,
            max_memory_usage_pct=stats.max,
            p80_memory_usage_pct=quantiles.get(0.80),
            p95_memory_usage_pct=quantiles.get(0.95),
            p98_memory_usage_pct=quantiles.get(0.98),
            result=result,
        )

    @mcp.tool()
    @mock_intercept(settings)
    async def bng_device_hosts_stats(device: str, ctx: Context) -> DeviceHostsStatsResult:
        """Return peak and current IPv4/IPv6 host counts for a BNG device.

        Executes four instant queries — peak and current values for
        each of IPv4 and IPv6 — using:

        - `state_subscriber_mgmt_statistics_total_hosts_peak_value`
        - `state_subscriber_mgmt_statistics_total_hosts_current_value`

        Both metrics use `total_hosts_counter` label to select
        `"ipv4"` or `"ipv6"`.  They are sourced from the SROS xpaths:

        - `/state/subscriber-mgmt/statistics/total-hosts[counter=ipv4]`
        - `/state/subscriber-mgmt/statistics/total-hosts[counter=ipv6]`

        Args:
            device: Device name, with or without a namespace prefix.
                Both `"clab-sros-bngt-bng1"` (resolved via the default
                namespace) and `"nok-bng/clab-sros-bngt-bng1"` are
                accepted.  The resolved value is used as the Prometheus
                `source` label.
            ctx: MCP request context.

        Returns:
            `DeviceHostsStatsResult` with `device`, `ipv4_peak`,
            `ipv6_peak`, `ipv4_current`, and `ipv6_current` fields.

        Raises:
            ErrorResponse: on device lookup failure or Prometheus query
                error.
        """
        device = await prepare_device(device, settings.k8s_namespace, mcp, ctx)
        logger.info("bng_device_hosts_stats: device=%s", device)
        await ctx.info(f"Querying host stats for {device}")

        tool_name = sys._getframe(0).f_code.co_name
        ipv4_peak: int | None = None
        ipv6_peak: int | None = None
        ipv4_current: int | None = None
        ipv6_current: int | None = None

        queries: list[tuple[str, str, str]] = [
            ("ipv4", "peak", "state_subscriber_mgmt_statistics_total_hosts_peak_value"),
            ("ipv6", "peak", "state_subscriber_mgmt_statistics_total_hosts_peak_value"),
            ("ipv4", "current", "state_subscriber_mgmt_statistics_total_hosts_current_value"),
            ("ipv6", "current", "state_subscriber_mgmt_statistics_total_hosts_current_value"),
        ]
        for counter, kind, metric in queries:
            promql = f"{metric}" f'{{source="{device}",total_hosts_counter="{counter}"}}'
            result = await execute_instant_query(
                prom=prom,
                promql=promql,
                device=device,
                tool_name=tool_name,
            )

            value: int | None = None
            if result.series and result.series[0].samples:
                value = int(result.series[0].samples[0].value)

            if counter == "ipv4" and kind == "peak":
                ipv4_peak = value
            elif counter == "ipv6" and kind == "peak":
                ipv6_peak = value
            elif counter == "ipv4" and kind == "current":
                ipv4_current = value
            else:
                ipv6_current = value

        return DeviceHostsStatsResult(
            device=device,
            ipv4_peak=ipv4_peak,
            ipv6_peak=ipv6_peak,
            ipv4_current=ipv4_current,
            ipv6_current=ipv6_current,
        )

    @mcp.tool()
    @mock_intercept(settings)
    async def sap_instances_allocated(
        device: str,
        interval: str,
        step: str,
        ctx: Context,
        card: int | None = None,
        mda: int | None = None,
        start_time: str = "",
    ) -> SapInstancesAllocatedResult:
        """Return SAP instances allocated per card/MDA for a BNG device.

        Executes a range query and an instant query on
        `state_card_mda_resource_usage_sap_instances_allocated`.  When
        `card` and/or `mda` are omitted, the corresponding label is
        dropped from the Prometheus selector and one series per
        matching (card, mda) pair is returned — results are NOT
        aggregated across slots.

        The SROS source xpath is:
        `/state/card[slot-number=*]/mda[mda-slot=*]/resource-usage/sap-instances/allocated`.

        Args:
            device: Device name, with or without a namespace prefix.
                Both `"clab-sros-bngt-bng1"` (resolved via the default
                namespace) and `"nok-bng/clab-sros-bngt-bng1"` are
                accepted.  The resolved value is used as the Prometheus
                `source` label.
            interval: Lookback window duration. Accepts Prometheus-style
                shorthand (`"5m"`, `"1h"`) and human-friendly forms
                (`"5min"`, `"2hours"`, `"7days"`). Case-insensitive.
            step: Query resolution step, e.g. `"15s"`, `"1m"`.
            ctx: MCP request context.
            card: Optional card slot number. When `None`, all cards are
                returned as separate series in `by_slot`.
            mda: Optional MDA slot number. When `None`, all MDAs are
                returned as separate series in `by_slot`.
            start_time: Optional ISO 8601 start time with timezone,
                e.g. `"2026-04-07T12:00:00+00:00"`. When empty the
                window starts at `now - interval`.

        Returns:
            `SapInstancesAllocatedResult` with `device`, `interval`,
            `step`, a `by_slot` list (one entry per matching card/MDA
            with current/min/max), and the raw `MetricResult` matrix.

        Raises:
            ErrorResponse: on invalid parameters, device lookup failure,
                or Prometheus query error.
        """
        metric = "state_card_mda_resource_usage_sap_instances_allocated"
        tool_name = sys._getframe(0).f_code.co_name

        device = await prepare_device(device, settings.k8s_namespace, mcp, ctx)
        logger.info(
            "sap_instances_allocated: device=%s card=%s mda=%s interval=%s step=%s start_time=%s",
            device,
            card,
            mda,
            interval,
            step,
            start_time or "auto",
        )
        await ctx.info(
            f"Querying SAP instances allocated for {device} "
            f"card={card if card is not None else '*'} "
            f"mda={mda if mda is not None else '*'} "
            f"over {interval} @ {step}"
        )

        # Metric example:
        # state_card_mda_resource_usage_sap_instances_allocated{
        #   card_slot_number="1", mda_mda_slot="1",
        #   source="nok-bng/clab-sros-bngt-bng1", ...}
        selectors = [f'source="{device}"']
        if card is not None:
            selectors.append(f'card_slot_number="{card}"')
        if mda is not None:
            selectors.append(f'mda_mda_slot="{mda}"')
        promql = f'{metric}{{{",".join(selectors)}}}'

        window = resolve_query_window(interval, step, device, start_time)

        result = await execute_range_query(
            prom=prom,
            promql=promql,
            start=window.start_dt.isoformat(),
            end=window.end_dt.isoformat(),
            step=window.prom_step,
            device=device,
            interval=interval,
            tool_name=tool_name,
        )

        current = await execute_instant_query(
            prom=prom,
            promql=promql,
            device=device,
            tool_name=tool_name,
        )

        def _slot_key(labels: dict[str, str]) -> tuple[int | None, int | None]:
            c = labels.get("card_slot_number")
            m = labels.get("mda_mda_slot")
            return (int(c) if c is not None else None, int(m) if m is not None else None)

        current_by_slot: dict[tuple[int | None, int | None], float] = {}
        for series in current.series:
            if series.samples:
                current_by_slot[_slot_key(series.labels)] = series.samples[0].value

        by_slot: list[SapAllocationBySlot] = []
        for series in result.series:
            values = [s.value for s in series.samples]
            if not values:
                continue
            key = _slot_key(series.labels)
            by_slot.append(
                SapAllocationBySlot(
                    card=key[0],
                    mda=key[1],
                    currently_allocated=current_by_slot.get(key),
                    min_allocated=min(values),
                    max_allocated=max(values),
                )
            )

        return SapInstancesAllocatedResult(
            device=device,
            interval=interval,
            step=step,
            by_slot=by_slot,
            result=result,
        )

    @mcp.tool()
    @mock_intercept(settings)
    async def ppp_sessions_total_established(
        device: str, interval: str, step: str, ctx: Context, start_time: str = ""
    ) -> PppSessionsTotalEstablishedResult:
        """Return established PPP session counts for a BNG device over a window.

        Executes a range query and an instant query on
        `state_subscriber_mgmt_statistics_sessions_current_value` filtered by
        `sessions_counter="ppp-sessions-total-established"`.  Returns the
        current value plus min/max observed across the window and the raw
        Prometheus matrix.

        The SROS source xpath is:
        `/state/subscriber-mgmt/statistics/sessions[counter=ppp-sessions-total-established]/current-value`.

        Args:
            device: Device name, with or without a namespace prefix.
                Both `"clab-sros-bngt-bng1"` (resolved via the default
                namespace) and `"nok-bng/clab-sros-bngt-bng1"` are
                accepted.  The resolved value is used as the Prometheus
                `source` label.
            interval: Lookback window duration. Accepts Prometheus-style
                shorthand (`"5m"`, `"1h"`) and human-friendly forms
                (`"5min"`, `"2hours"`, `"7days"`). Case-insensitive.
            step: Query resolution step, e.g. `"15s"`, `"1m"`.
            ctx: MCP request context.
            start_time: Optional ISO 8601 start time with timezone,
                e.g. `"2026-04-07T12:00:00+00:00"`. When empty the
                window starts at `now - interval`.

        Returns:
            `PppSessionsTotalEstablishedResult` with `device`, `interval`,
            `step`, `currently_established`, `min_established`,
            `max_established`, and the raw `MetricResult` matrix.

        Raises:
            ErrorResponse: on invalid parameters, device lookup failure,
                or Prometheus query error.
        """
        metric = "state_subscriber_mgmt_statistics_sessions_current_value"
        counter = "ppp-sessions-total-established"
        tool_name = sys._getframe(0).f_code.co_name

        device = await prepare_device(device, settings.k8s_namespace, mcp, ctx)
        logger.info(
            "ppp_sessions_total_established: device=%s interval=%s step=%s start_time=%s",
            device,
            interval,
            step,
            start_time or "auto",
        )
        await ctx.info(f"Querying PPP sessions established for {device} over {interval} @ {step}")

        promql = f'{metric}{{source="{device}",sessions_counter="{counter}"}}'

        window = resolve_query_window(interval, step, device, start_time)

        result = await execute_range_query(
            prom=prom,
            promql=promql,
            start=window.start_dt.isoformat(),
            end=window.end_dt.isoformat(),
            step=window.prom_step,
            device=device,
            interval=interval,
            tool_name=tool_name,
        )
        current = await execute_instant_query(
            prom=prom,
            promql=promql,
            device=device,
            tool_name=tool_name,
        )

        currently_established: int | None = None
        if current.series and current.series[0].samples:
            currently_established = int(current.series[0].samples[0].value)

        min_v: int | None = None
        max_v: int | None = None
        for series in result.series:
            values = [s.value for s in series.samples]
            if not values:
                continue
            s_min = int(min(values))
            s_max = int(max(values))
            min_v = s_min if min_v is None else min(min_v, s_min)
            max_v = s_max if max_v is None else max(max_v, s_max)

        return PppSessionsTotalEstablishedResult(
            device=device,
            interval=interval,
            step=step,
            currently_established=currently_established,
            min_established=min_v,
            max_established=max_v,
            result=result,
        )

    # Metric: ( state_card_fp_resource_usage_egress_policers_allocated / on (source)
    # state_card_fp_resource_usage_egress_policers_total) * 100
    # state_card_fp_resource_usage_egress_policers_allocated{card_slot_number="1",
    # endpoint="metrics", fp_fp_number="1", instance="10.244.0.48:10332",
    # job="gnmic-bng-metrics-prom-bng-tel-prom-output",
    # namespace="nok-bng", pod="gnmic-bng-metrics-0",
    # service="gnmic-bng-metrics-prom-bng-tel-prom-output",
    # source="nok-bng/clab-sros-bngt-bng1", subscription_name="nok-bng/sros-fp-sub-mgmt-resources"}
    # state_card_fp_resource_usage_egress_policers_total{card_slot_number="1", endpoint="metrics",
    # fp_fp_number="1", instance="10.244.0.48:10332",
    # job="gnmic-bng-metrics-prom-bng-tel-prom-output",
    # namespace="nok-bng", pod="gnmic-bng-metrics-0",
    # service="gnmic-bng-metrics-prom-bng-tel-prom-output",
    # source="nok-bng/clab-sros-bngt-bng1", subscription_name="nok-bng/sros-fp-sub-mgmt-resources"}

    async def _query_policers_allocated(
        direction: PolicerDirection,
        device: str,
        interval: str,
        step: str,
        ctx: Context,
        card: int | None,
        fp: int | None,
        start_time: str,
        tool_name: str,
    ) -> PolicersAllocatedResult:
        """Run the three Prometheus queries powering the policer-allocation tools.

        Shared by `ingress_policers_allocated` and `egress_policers_allocated`.
        See those tools' docstrings for full argument and return semantics.
        """
        device = await prepare_device(device, settings.k8s_namespace, mcp, ctx)
        logger.info(
            "%s: device=%s card=%s fp=%s interval=%s step=%s start_time=%s",
            tool_name,
            device,
            card,
            fp,
            interval,
            step,
            start_time or "auto",
        )
        await ctx.info(
            f"Querying {direction} policers allocated for {device} "
            f"card={card if card is not None else '*'} "
            f"fp={fp if fp is not None else '*'} "
            f"over {interval} @ {step}"
        )

        allocated_metric = f"state_card_fp_resource_usage_{direction}_policers_allocated"
        total_metric = f"state_card_fp_resource_usage_{direction}_policers_total"

        selectors = [f'source="{device}"']
        if card is not None:
            selectors.append(f'card_slot_number="{card}"')
        if fp is not None:
            selectors.append(f'fp_fp_number="{fp}"')
        sel = ",".join(selectors)

        allocated_promql = f"{allocated_metric}{{{sel}}}"
        total_promql = f"{total_metric}{{{sel}}}"
        pct_promql = (
            f"{allocated_metric}{{{sel}}}"
            f" / on(source, card_slot_number, fp_fp_number) "
            f"{total_metric}{{{sel}}}"
            f" * 100"
        )

        window = resolve_query_window(interval, step, device, start_time)

        pct_range = await execute_range_query(
            prom=prom,
            promql=pct_promql,
            start=window.start_dt.isoformat(),
            end=window.end_dt.isoformat(),
            step=window.prom_step,
            device=device,
            interval=interval,
            tool_name=tool_name,
        )
        allocated_instant = await execute_instant_query(
            prom=prom,
            promql=allocated_promql,
            device=device,
            tool_name=tool_name,
        )
        total_instant = await execute_instant_query(
            prom=prom,
            promql=total_promql,
            device=device,
            tool_name=tool_name,
        )

        def _key(labels: dict[str, str]) -> tuple[int | None, int | None]:
            c = labels.get("card_slot_number")
            f = labels.get("fp_fp_number")
            return (int(c) if c is not None else None, int(f) if f is not None else None)

        allocated_by_slot: dict[tuple[int | None, int | None], float] = {}
        for series in allocated_instant.series:
            if series.samples:
                allocated_by_slot[_key(series.labels)] = series.samples[0].value

        total_by_slot: dict[tuple[int | None, int | None], float] = {}
        for series in total_instant.series:
            if series.samples:
                total_by_slot[_key(series.labels)] = series.samples[0].value

        by_slot: list[PolicersAllocationBySlot] = []
        seen: set[tuple[int | None, int | None]] = set()
        for series in pct_range.series:
            key = _key(series.labels)
            if key in seen:
                continue
            seen.add(key)
            values = [s.value for s in series.samples]
            min_pct = min(values) if values else None
            max_pct = max(values) if values else None

            allocated = allocated_by_slot.get(key)
            total = total_by_slot.get(key)
            if allocated is not None and total is not None and total != 0:
                currently_pct: float | None = allocated / total * 100
            else:
                currently_pct = None

            by_slot.append(
                PolicersAllocationBySlot(
                    card=key[0],
                    fp=key[1],
                    currently_allocated=allocated,
                    currently_total=total,
                    currently_pct=currently_pct,
                    min_pct=min_pct,
                    max_pct=max_pct,
                )
            )

        # Include slots that have instant values but no range series (rare).
        for key in set(allocated_by_slot) | set(total_by_slot):
            if key in seen:
                continue
            allocated = allocated_by_slot.get(key)
            total = total_by_slot.get(key)
            if allocated is not None and total is not None and total != 0:
                currently_pct = allocated / total * 100
            else:
                currently_pct = None
            by_slot.append(
                PolicersAllocationBySlot(
                    card=key[0],
                    fp=key[1],
                    currently_allocated=allocated,
                    currently_total=total,
                    currently_pct=currently_pct,
                    min_pct=None,
                    max_pct=None,
                )
            )

        return PolicersAllocatedResult(
            device=device,
            direction=direction,
            interval=interval,
            step=step,
            by_slot=by_slot,
            result=pct_range,
        )

    @mcp.tool()
    @mock_intercept(settings)
    async def ingress_policers_allocated(
        device: str,
        interval: str,
        step: str,
        ctx: Context,
        card: int | None = None,
        fp: int | None = None,
        start_time: str = "",
    ) -> PolicersAllocatedResult:
        """Return ingress policer allocation per (card, fp) for a BNG device.

        Executes instant queries on
        `state_card_fp_resource_usage_ingress_policers_allocated` and
        `state_card_fp_resource_usage_ingress_policers_total`, plus a
        range query on their ratio (in %), so callers can spot FPs
        approaching ingress policer exhaustion.

        When `card` and/or `fp` are omitted, the corresponding label is
        dropped from the Prometheus selector and one series per
        matching (card, fp) pair is returned — results are NOT
        aggregated across slots.

        The SROS source xpath is:
        `/state/card[slot-number=*]/fp[fp-number=*]/resource-usage/ingress-policers`.

        Args:
            device: Device name, with or without a namespace prefix.
                Both `"clab-sros-bngt-bng1"` (resolved via the default
                namespace) and `"nok-bng/clab-sros-bngt-bng1"` are
                accepted.  The resolved value is used as the Prometheus
                `source` label.
            interval: Lookback window duration. Accepts Prometheus-style
                shorthand (`"5m"`, `"1h"`) and human-friendly forms
                (`"5min"`, `"2hours"`, `"7days"`). Case-insensitive.
            step: Query resolution step, e.g. `"15s"`, `"1m"`.
            ctx: MCP request context.
            card: Optional card slot number. When `None`, all cards are
                returned as separate entries in `by_slot`.
            fp: Optional FP (forwarding plane) number. When `None`, all
                FPs are returned as separate entries in `by_slot`.
            start_time: Optional ISO 8601 start time with timezone,
                e.g. `"2026-04-07T12:00:00+00:00"`. When empty the
                window starts at `now - interval`.

        Returns:
            `PolicersAllocatedResult` with `device`, `direction="ingress"`,
            `interval`, `step`, a `by_slot` list (per matching card/fp
            with currently_allocated/total/pct, min/max pct), and the
            raw utilisation-% `MetricResult` matrix.

        Raises:
            ErrorResponse: on invalid parameters, device lookup failure,
                or Prometheus query error.
        """
        return await _query_policers_allocated(
            direction="ingress",
            device=device,
            interval=interval,
            step=step,
            ctx=ctx,
            card=card,
            fp=fp,
            start_time=start_time,
            tool_name=sys._getframe(0).f_code.co_name,
        )

    @mcp.tool()
    @mock_intercept(settings)
    async def egress_policers_allocated(
        device: str,
        interval: str,
        step: str,
        ctx: Context,
        card: int | None = None,
        fp: int | None = None,
        start_time: str = "",
    ) -> PolicersAllocatedResult:
        """Return egress policer allocation per (card, fp) for a BNG device.

        Executes instant queries on
        `state_card_fp_resource_usage_egress_policers_allocated` and
        `state_card_fp_resource_usage_egress_policers_total`, plus a
        range query on their ratio (in %), so callers can spot FPs
        approaching egress policer exhaustion.

        When `card` and/or `fp` are omitted, the corresponding label is
        dropped from the Prometheus selector and one series per
        matching (card, fp) pair is returned — results are NOT
        aggregated across slots.

        The SROS source xpath is:
        `/state/card[slot-number=*]/fp[fp-number=*]/resource-usage/egress-policers`.

        Args:
            device: Device name, with or without a namespace prefix.
                Both `"clab-sros-bngt-bng1"` (resolved via the default
                namespace) and `"nok-bng/clab-sros-bngt-bng1"` are
                accepted.  The resolved value is used as the Prometheus
                `source` label.
            interval: Lookback window duration. Accepts Prometheus-style
                shorthand (`"5m"`, `"1h"`) and human-friendly forms
                (`"5min"`, `"2hours"`, `"7days"`). Case-insensitive.
            step: Query resolution step, e.g. `"15s"`, `"1m"`.
            ctx: MCP request context.
            card: Optional card slot number. When `None`, all cards are
                returned as separate entries in `by_slot`.
            fp: Optional FP (forwarding plane) number. When `None`, all
                FPs are returned as separate entries in `by_slot`.
            start_time: Optional ISO 8601 start time with timezone,
                e.g. `"2026-04-07T12:00:00+00:00"`. When empty the
                window starts at `now - interval`.

        Returns:
            `PolicersAllocatedResult` with `device`, `direction="egress"`,
            `interval`, `step`, a `by_slot` list (per matching card/fp
            with currently_allocated/total/pct, min/max pct), and the
            raw utilisation-% `MetricResult` matrix.

        Raises:
            ErrorResponse: on invalid parameters, device lookup failure,
                or Prometheus query error.
        """
        return await _query_policers_allocated(
            direction="egress",
            device=device,
            interval=interval,
            step=step,
            ctx=ctx,
            card=card,
            fp=fp,
            start_time=start_time,
            tool_name=sys._getframe(0).f_code.co_name,
        )

    # Metric: ( state_card_fp_resource_usage_egress_queues_allocated/
    # on (source) state_card_fp_resource_usage_egress_queues_total) * 100
    # state_card_fp_resource_usage_egress_queues_allocated{card_slot_number="1",
    # endpoint="metrics", fp_fp_number="1", instance="10.244.0.48:10332",
    # job="gnmic-bng-metrics-prom-bng-tel-prom-output", namespace="nok-bng",
    # pod="gnmic-bng-metrics-0", service="gnmic-bng-metrics-prom-bng-tel-prom-output",
    # source="nok-bng/clab-sros-bngt-bng1", subscription_name="nok-bng/sros-fp-sub-mgmt-resources"}
    # state_card_fp_resource_usage_egress_queues_total{card_slot_number="1",
    # endpoint="metrics", fp_fp_number="1", instance="10.244.0.48:10332",
    # job="gnmic-bng-metrics-prom-bng-tel-prom-output", namespace="nok-bng",
    # pod="gnmic-bng-metrics-0", service="gnmic-bng-metrics-prom-bng-tel-prom-output",
    # source="nok-bng/clab-sros-bngt-bng1", subscription_name="nok-bng/sros-fp-sub-mgmt-resources"}

    async def _query_queues_allocated(
        direction: QueuesDirection,
        device: str,
        interval: str,
        step: str,
        ctx: Context,
        card: int | None,
        fp: int | None,
        start_time: str,
        tool_name: str,
    ) -> QueuesAllocatedResult:
        """Run the three Prometheus queries powering the queue-allocation tools.

        Shared by `ingress_queues_allocated` and `egress_queues_allocated`.
        See those tools' docstrings for full argument and return semantics.
        """
        device = await prepare_device(device, settings.k8s_namespace, mcp, ctx)
        logger.info(
            "%s: device=%s card=%s fp=%s interval=%s step=%s start_time=%s",
            tool_name,
            device,
            card,
            fp,
            interval,
            step,
            start_time or "auto",
        )
        await ctx.info(
            f"Querying {direction} queues allocated for {device} "
            f"card={card if card is not None else '*'} "
            f"fp={fp if fp is not None else '*'} "
            f"over {interval} @ {step}"
        )

        allocated_metric = f"state_card_fp_resource_usage_{direction}_queues_allocated"
        total_metric = f"state_card_fp_resource_usage_{direction}_queues_total"

        selectors = [f'source="{device}"']
        if card is not None:
            selectors.append(f'card_slot_number="{card}"')
        if fp is not None:
            selectors.append(f'fp_fp_number="{fp}"')
        sel = ",".join(selectors)

        allocated_promql = f"{allocated_metric}{{{sel}}}"
        total_promql = f"{total_metric}{{{sel}}}"
        pct_promql = (
            f"{allocated_metric}{{{sel}}}"
            f" / on(source, card_slot_number, fp_fp_number) "
            f"{total_metric}{{{sel}}}"
            f" * 100"
        )

        window = resolve_query_window(interval, step, device, start_time)

        pct_range = await execute_range_query(
            prom=prom,
            promql=pct_promql,
            start=window.start_dt.isoformat(),
            end=window.end_dt.isoformat(),
            step=window.prom_step,
            device=device,
            interval=interval,
            tool_name=tool_name,
        )
        allocated_instant = await execute_instant_query(
            prom=prom,
            promql=allocated_promql,
            device=device,
            tool_name=tool_name,
        )
        total_instant = await execute_instant_query(
            prom=prom,
            promql=total_promql,
            device=device,
            tool_name=tool_name,
        )

        def _key(labels: dict[str, str]) -> tuple[int | None, int | None]:
            c = labels.get("card_slot_number")
            f = labels.get("fp_fp_number")
            return (int(c) if c is not None else None, int(f) if f is not None else None)

        allocated_by_slot: dict[tuple[int | None, int | None], float] = {}
        for series in allocated_instant.series:
            if series.samples:
                allocated_by_slot[_key(series.labels)] = series.samples[0].value

        total_by_slot: dict[tuple[int | None, int | None], float] = {}
        for series in total_instant.series:
            if series.samples:
                total_by_slot[_key(series.labels)] = series.samples[0].value

        by_slot: list[QueuesAllocationBySlot] = []
        seen: set[tuple[int | None, int | None]] = set()
        for series in pct_range.series:
            key = _key(series.labels)
            if key in seen:
                continue
            seen.add(key)
            values = [s.value for s in series.samples]
            min_pct = min(values) if values else None
            max_pct = max(values) if values else None

            allocated = allocated_by_slot.get(key)
            total = total_by_slot.get(key)
            if allocated is not None and total is not None and total != 0:
                currently_pct: float | None = allocated / total * 100
            else:
                currently_pct = None

            by_slot.append(
                QueuesAllocationBySlot(
                    card=key[0],
                    fp=key[1],
                    currently_allocated=allocated,
                    currently_total=total,
                    currently_pct=currently_pct,
                    min_pct=min_pct,
                    max_pct=max_pct,
                )
            )

        # Include slots that have instant values but no range series (rare).
        for key in set(allocated_by_slot) | set(total_by_slot):
            if key in seen:
                continue
            allocated = allocated_by_slot.get(key)
            total = total_by_slot.get(key)
            if allocated is not None and total is not None and total != 0:
                currently_pct = allocated / total * 100
            else:
                currently_pct = None
            by_slot.append(
                QueuesAllocationBySlot(
                    card=key[0],
                    fp=key[1],
                    currently_allocated=allocated,
                    currently_total=total,
                    currently_pct=currently_pct,
                    min_pct=None,
                    max_pct=None,
                )
            )

        return QueuesAllocatedResult(
            device=device,
            direction=direction,
            interval=interval,
            step=step,
            by_slot=by_slot,
            result=pct_range,
        )

    @mcp.tool()
    @mock_intercept(settings)
    async def ingress_queues_allocated(
        device: str,
        interval: str,
        step: str,
        ctx: Context,
        card: int | None = None,
        fp: int | None = None,
        start_time: str = "",
    ) -> QueuesAllocatedResult:
        """Return ingress queue allocation per (card, fp) for a BNG device.

        Executes instant queries on
        `state_card_fp_resource_usage_ingress_queues_allocated` and
        `state_card_fp_resource_usage_ingress_queues_total`, plus a
        range query on their ratio (in %), so callers can spot FPs
        approaching ingress queue exhaustion.

        When `card` and/or `fp` are omitted, the corresponding label is
        dropped from the Prometheus selector and one series per
        matching (card, fp) pair is returned — results are NOT
        aggregated across slots.

        The SROS source xpath is:
        `/state/card[slot-number=*]/fp[fp-number=*]/resource-usage/ingress-queues`.

        Args:
            device: Device name, with or without a namespace prefix.
                Both `"clab-sros-bngt-bng1"` (resolved via the default
                namespace) and `"nok-bng/clab-sros-bngt-bng1"` are
                accepted.  The resolved value is used as the Prometheus
                `source` label.
            interval: Lookback window duration. Accepts Prometheus-style
                shorthand (`"5m"`, `"1h"`) and human-friendly forms
                (`"5min"`, `"2hours"`, `"7days"`). Case-insensitive.
            step: Query resolution step, e.g. `"15s"`, `"1m"`.
            ctx: MCP request context.
            card: Optional card slot number. When `None`, all cards are
                returned as separate entries in `by_slot`.
            fp: Optional FP (forwarding plane) number. When `None`, all
                FPs are returned as separate entries in `by_slot`.
            start_time: Optional ISO 8601 start time with timezone,
                e.g. `"2026-04-07T12:00:00+00:00"`. When empty the
                window starts at `now - interval`.

        Returns:
            `QueuesAllocatedResult` with `device`, `direction="ingress"`,
            `interval`, `step`, a `by_slot` list (per matching card/fp
            with currently_allocated/total/pct, min/max pct), and the
            raw utilisation-% `MetricResult` matrix.

        Raises:
            ErrorResponse: on invalid parameters, device lookup failure,
                or Prometheus query error.
        """
        return await _query_queues_allocated(
            direction="ingress",
            device=device,
            interval=interval,
            step=step,
            ctx=ctx,
            card=card,
            fp=fp,
            start_time=start_time,
            tool_name=sys._getframe(0).f_code.co_name,
        )

    @mcp.tool()
    @mock_intercept(settings)
    async def egress_queues_allocated(
        device: str,
        interval: str,
        step: str,
        ctx: Context,
        card: int | None = None,
        fp: int | None = None,
        start_time: str = "",
    ) -> QueuesAllocatedResult:
        """Return egress queue allocation per (card, fp) for a BNG device.

        Executes instant queries on
        `state_card_fp_resource_usage_egress_queues_allocated` and
        `state_card_fp_resource_usage_egress_queues_total`, plus a
        range query on their ratio (in %), so callers can spot FPs
        approaching egress queue exhaustion.

        When `card` and/or `fp` are omitted, the corresponding label is
        dropped from the Prometheus selector and one series per
        matching (card, fp) pair is returned — results are NOT
        aggregated across slots.

        The SROS source xpath is:
        `/state/card[slot-number=*]/fp[fp-number=*]/resource-usage/egress-queues`.

        Args:
            device: Device name, with or without a namespace prefix.
                Both `"clab-sros-bngt-bng1"` (resolved via the default
                namespace) and `"nok-bng/clab-sros-bngt-bng1"` are
                accepted.  The resolved value is used as the Prometheus
                `source` label.
            interval: Lookback window duration. Accepts Prometheus-style
                shorthand (`"5m"`, `"1h"`) and human-friendly forms
                (`"5min"`, `"2hours"`, `"7days"`). Case-insensitive.
            step: Query resolution step, e.g. `"15s"`, `"1m"`.
            ctx: MCP request context.
            card: Optional card slot number. When `None`, all cards are
                returned as separate entries in `by_slot`.
            fp: Optional FP (forwarding plane) number. When `None`, all
                FPs are returned as separate entries in `by_slot`.
            start_time: Optional ISO 8601 start time with timezone,
                e.g. `"2026-04-07T12:00:00+00:00"`. When empty the
                window starts at `now - interval`.

        Returns:
            `QueuesAllocatedResult` with `device`, `direction="egress"`,
            `interval`, `step`, a `by_slot` list (per matching card/fp
            with currently_allocated/total/pct, min/max pct), and the
            raw utilisation-% `MetricResult` matrix.

        Raises:
            ErrorResponse: on invalid parameters, device lookup failure,
                or Prometheus query error.
        """
        return await _query_queues_allocated(
            direction="egress",
            device=device,
            interval=interval,
            step=step,
            ctx=ctx,
            card=card,
            fp=fp,
            start_time=start_time,
            tool_name=sys._getframe(0).f_code.co_name,
        )

    # Metric: ( state_system_resource_usage_subscriber_next_hop_entries_allocated /
    # on (source) state_system_resource_usage_subscriber_next_hop_entries_total) * 100
    # state_system_resource_usage_subscriber_next_hop_entries_allocated{endpoint="metrics",
    # instance="10.244.0.48:10332", job="gnmic-bng-metrics-prom-bng-tel-prom-output",
    # namespace="nok-bng", pod="gnmic-bng-metrics-0",
    # service="gnmic-bng-metrics-prom-bng-tel-prom-output",
    # source="nok-bng/clab-sros-bngt-bng1", subscription_name="nok-bng/sros-system-resources"}
    # state_system_resource_usage_subscriber_next_hop_entries_total{endpoint="metrics",
    # instance="10.244.0.48:10332", job="gnmic-bng-metrics-prom-bng-tel-prom-output",
    # namespace="nok-bng", pod="gnmic-bng-metrics-0",
    # service="gnmic-bng-metrics-prom-bng-tel-prom-output",
    # source="nok-bng/clab-sros-bngt-bng1", subscription_name="nok-bng/sros-system-resources"}

    @mcp.tool()
    @mock_intercept(settings)
    async def subscriber_next_hop_entries_allocated(
        device: str, interval: str, step: str, ctx: Context, start_time: str = ""
    ) -> SubscriberNextHopEntriesResult:
        """Return subscriber next-hop entries allocation for a BNG device.

        Executes instant queries on
        `state_system_resource_usage_subscriber_next_hop_entries_allocated`
        and `state_system_resource_usage_subscriber_next_hop_entries_total`,
        plus a range query on their ratio (in %), so callers can spot
        devices approaching subscriber next-hop table exhaustion.  Results
        are device-scoped (joined on the `source` label); there is no
        card/fp dimension.

        The SROS source xpath is:
        `/state/system/resource-usage/subscriber-next-hop-entries`.

        Args:
            device: Device name, with or without a namespace prefix.
                Both `"clab-sros-bngt-bng1"` (resolved via the default
                namespace) and `"nok-bng/clab-sros-bngt-bng1"` are
                accepted.  The resolved value is used as the Prometheus
                `source` label.
            interval: Lookback window duration. Accepts Prometheus-style
                shorthand (`"5m"`, `"1h"`) and human-friendly forms
                (`"5min"`, `"2hours"`, `"7days"`). Case-insensitive.
            step: Query resolution step, e.g. `"15s"`, `"1m"`.
            ctx: MCP request context.
            start_time: Optional ISO 8601 start time with timezone,
                e.g. `"2026-04-07T12:00:00+00:00"`. When empty the
                window starts at `now - interval`.

        Returns:
            `SubscriberNextHopEntriesResult` with `device`, `interval`,
            `step`, `currently_allocated`, `currently_total`,
            `currently_pct`, `min_pct`, `max_pct`, and the raw
            utilisation-% `MetricResult` matrix.

        Raises:
            ErrorResponse: on invalid parameters, device lookup failure,
                or Prometheus query error.
        """
        tool_name = sys._getframe(0).f_code.co_name
        device = await prepare_device(device, settings.k8s_namespace, mcp, ctx)
        logger.info(
            "subscriber_next_hop_entries_allocated: device=%s interval=%s step=%s start_time=%s",
            device,
            interval,
            step,
            start_time or "auto",
        )
        await ctx.info(
            f"Querying subscriber next-hop entries allocated for {device} "
            f"over {interval} @ {step}"
        )

        allocated_metric = "state_system_resource_usage_subscriber_next_hop_entries_allocated"
        total_metric = "state_system_resource_usage_subscriber_next_hop_entries_total"
        sel = f'source="{device}"'

        allocated_promql = f"{allocated_metric}{{{sel}}}"
        total_promql = f"{total_metric}{{{sel}}}"
        pct_promql = (
            f"{allocated_metric}{{{sel}}}" f" / on(source) " f"{total_metric}{{{sel}}}" f" * 100"
        )

        window = resolve_query_window(interval, step, device, start_time)

        pct_range = await execute_range_query(
            prom=prom,
            promql=pct_promql,
            start=window.start_dt.isoformat(),
            end=window.end_dt.isoformat(),
            step=window.prom_step,
            device=device,
            interval=interval,
            tool_name=tool_name,
        )
        allocated_instant = await execute_instant_query(
            prom=prom,
            promql=allocated_promql,
            device=device,
            tool_name=tool_name,
        )
        total_instant = await execute_instant_query(
            prom=prom,
            promql=total_promql,
            device=device,
            tool_name=tool_name,
        )

        currently_allocated: float | None = None
        if allocated_instant.series and allocated_instant.series[0].samples:
            currently_allocated = allocated_instant.series[0].samples[0].value

        currently_total: float | None = None
        if total_instant.series and total_instant.series[0].samples:
            currently_total = total_instant.series[0].samples[0].value

        if currently_allocated is not None and currently_total is not None and currently_total != 0:
            currently_pct: float | None = currently_allocated / currently_total * 100
        else:
            currently_pct = None

        min_pct: float | None = None
        max_pct: float | None = None
        for series in pct_range.series:
            values = [s.value for s in series.samples]
            if not values:
                continue
            s_min = min(values)
            s_max = max(values)
            min_pct = s_min if min_pct is None else min(min_pct, s_min)
            max_pct = s_max if max_pct is None else max(max_pct, s_max)

        return SubscriberNextHopEntriesResult(
            device=device,
            interval=interval,
            step=step,
            currently_allocated=currently_allocated,
            currently_total=currently_total,
            currently_pct=currently_pct,
            min_pct=min_pct,
            max_pct=max_pct,
            result=pct_range,
        )
