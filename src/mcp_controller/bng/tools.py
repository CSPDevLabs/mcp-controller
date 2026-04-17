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
    parse_duration,
    parse_start_time,
    resolve_device_source,
    verify_device_target,
)
from mcp_controller.bng.resources import BNG_MANIFEST
from mcp_controller.bng.types import (
    DeviceCpuUsageResult,
    DeviceHostsStatsResult,
    DeviceMemoryUsageResult,
    DeviceUnavailabilityResult,
    TargetSummary,
)
from mcp_controller.core.types import ErrorResponse
from mcp_controller.core.registry import ControllerManifest
from mcp_controller.config import Settings
from mcp_controller.core.mock import mock_intercept
from mcp_controller.core.kubernetes_client import (
    KubernetesClient,
    KubernetesClientError,
)
from mcp_controller.core.prometheus_client import PrometheusClient

logger = logging.getLogger(__name__)


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
    async def bng_device_unavailability_map(
        device: str, interval: str, step: str, ctx: Context, start_time: str = ""
    ) -> DeviceUnavailabilityResult:
        """Return an availability time series for a BNG device over a window.

        Executes a range query with `absent_over_time()` on
        `state_system_cpu_summary_usage_cpu_time` to produce a matrix where
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
        device = resolve_device_source(device, settings.k8s_namespace)
        logger.info(
            "bng_device_unavailability_map: device=%s interval=%s step=%s start_time=%s",
            device,
            interval,
            step,
            start_time or "auto",
        )
        await ctx.info(f"Checking unavailability for {device} over {interval} @ {step}")

        try:
            await verify_device_target(device, mcp)
        except KubernetesClientError as exc:
            raise ErrorResponse(
                error="device_not_found",
                detail=str(exc),
                device=device,
            ) from exc

        try:
            delta, prom_interval = parse_duration(interval)
        except ValueError as exc:
            raise ErrorResponse(
                error="invalid_interval",
                detail=f"Cannot parse interval '{interval}'",
                device=device,
                interval=interval,
            ) from exc

        try:
            _, prom_step = parse_duration(step)
        except ValueError as exc:
            raise ErrorResponse(
                error="invalid_step",
                detail=f"Cannot parse step '{step}'",
                device=device,
                interval=interval,
            ) from exc

        if start_time:
            try:
                start_dt = parse_start_time(start_time)
            except ValueError as exc:
                raise ErrorResponse(
                    error="invalid_start_time",
                    detail=f"Cannot parse start_time '{start_time}'",
                    device=device,
                    interval=interval,
                ) from exc
            end_dt = start_dt + delta
        else:
            end_dt = datetime.now(tz=timezone.utc)
            start_dt = end_dt - delta

        promql = (
            f"(absent_over_time("
            f'state_system_cpu_summary_usage_cpu_time{{source="{device}"}}'
            f"[{prom_interval}]) == 1) OR "
            f"(count by (source) ("
            f'state_system_cpu_summary_usage_cpu_time{{source="{device}"}}'
            f") * 0)"
        )

        result = await execute_range_query(
            prom=prom,
            promql=promql,
            start=start_dt.isoformat(),
            end=end_dt.isoformat(),
            step=prom_step,
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
        device = resolve_device_source(device, settings.k8s_namespace)
        logger.info(
            "bng_device_cpu_usage: device=%s interval=%s step=%s start_time=%s",
            device,
            interval,
            step,
            start_time or "auto",
        )
        await ctx.info(f"Querying CPU usage for {device} over {interval} @ {step}")

        try:
            await verify_device_target(device, mcp)
        except KubernetesClientError as exc:
            raise ErrorResponse(
                error="device_not_found",
                detail=str(exc),
                device=device,
            ) from exc

        try:
            delta, prom_interval = parse_duration(interval)
        except ValueError as exc:
            raise ErrorResponse(
                error="invalid_interval",
                detail=f"Cannot parse interval '{interval}'",
                device=device,
                interval=interval,
            ) from exc

        try:
            _, prom_step = parse_duration(step)
        except ValueError as exc:
            raise ErrorResponse(
                error="invalid_step",
                detail=f"Cannot parse step '{step}'",
                device=device,
                interval=interval,
            ) from exc

        if start_time:
            try:
                start_dt = parse_start_time(start_time)
            except ValueError as exc:
                raise ErrorResponse(
                    error="invalid_start_time",
                    detail=f"Cannot parse start_time '{start_time}'",
                    device=device,
                    interval=interval,
                ) from exc
            end_dt = start_dt + delta
        else:
            end_dt = datetime.now(tz=timezone.utc)
            start_dt = end_dt - delta

        promql = (
            "state_system_cpu_summary_usage_cpu_usage"
            f'{{cpu_sample_period="60",source="{device}"}}'
        )

        result = await execute_range_query(
            prom=prom,
            promql=promql,
            start=start_dt.isoformat(),
            end=end_dt.isoformat(),
            step=prom_step,
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
                f'{{cpu_sample_period="60",source="{device}"}}[{prom_interval}:{prom_step}])'
            )

            quantile_result = await execute_range_query(
                prom=prom,
                promql=promql_xxq,
                start=start_dt.isoformat(),
                end=end_dt.isoformat(),
                step=prom_step,
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
        device = resolve_device_source(device, settings.k8s_namespace)
        logger.info(
            "bng_device_memory_usage: device=%s interval=%s step=%s start_time=%s",
            device,
            interval,
            step,
            start_time or "auto",
        )
        await ctx.info(f"Querying memory usage for {device} over {interval} @ {step}")

        try:
            await verify_device_target(device, mcp)
        except KubernetesClientError as exc:
            raise ErrorResponse(
                error="device_not_found",
                detail=str(exc),
                device=device,
            ) from exc

        try:
            delta, prom_interval = parse_duration(interval)
        except ValueError as exc:
            raise ErrorResponse(
                error="invalid_interval",
                detail=f"Cannot parse interval '{interval}'",
                device=device,
                interval=interval,
            ) from exc

        try:
            _, prom_step = parse_duration(step)
        except ValueError as exc:
            raise ErrorResponse(
                error="invalid_step",
                detail=f"Cannot parse step '{step}'",
                device=device,
                interval=interval,
            ) from exc

        if start_time:
            try:
                start_dt = parse_start_time(start_time)
            except ValueError as exc:
                raise ErrorResponse(
                    error="invalid_start_time",
                    detail=f"Cannot parse start_time '{start_time}'",
                    device=device,
                    interval=interval,
                ) from exc
            end_dt = start_dt + delta
        else:
            end_dt = datetime.now(tz=timezone.utc)
            start_dt = end_dt - delta

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
            start=start_dt.isoformat(),
            end=end_dt.isoformat(),
            step=prom_step,
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
                f"quantile_over_time({q}, ({mem_pct_expr})" f"[{prom_interval}:{prom_step}])"
            )

            quantile_result = await execute_range_query(
                prom=prom,
                promql=promql_xxq,
                start=start_dt.isoformat(),
                end=end_dt.isoformat(),
                step=prom_step,
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
        device = resolve_device_source(device, settings.k8s_namespace)
        logger.info("bng_device_hosts_stats: device=%s", device)
        await ctx.info(f"Querying host stats for {device}")

        try:
            await verify_device_target(device, mcp)
        except KubernetesClientError as exc:
            raise ErrorResponse(
                error="device_not_found",
                detail=str(exc),
                device=device,
            ) from exc

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
