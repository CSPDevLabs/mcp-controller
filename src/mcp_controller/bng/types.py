"""Pydantic response models for BNG MCP resources and tools."""

from pydantic import BaseModel, Field

from mcp_controller.core.types import MetricResult


class TargetSummary(BaseModel):
    """Flat summary of a `NetworkDeviceTarget` or `NetworkHostTarget` CR.

    Used by target-listing resources and tools to return a lightweight
    representation without the full CRD spec/status.
    """

    name: str | None = None
    address: str | None = None
    hostname: str | None = None
    kind: str | None = None
    namespace: str | None = None


class DeviceCpuUsageResult(BaseModel):
    """Result of a device CPU usage time-series query.

    Returned by the `bng_device_cpu_usage` tool after executing a
    range query on `state_system_cpu_summary_usage_cpu_usage`.
    """

    device: str = Field(description="Device name (Prometheus `source` label).")
    interval: str = Field(description="Lookback window that was queried.")
    step: str = Field(description="Query resolution step.")
    sample_count: int = Field(
        default=0,
        description="Total number of CPU usage samples returned.",
    )
    avg_cpu_usage: float | None = Field(
        default=None,
        description="Average CPU usage across all samples, or `null` if no data.",
    )
    min_cpu_usage: float | None = Field(
        default=None,
        description="Minimum CPU usage observed, or `null` if no data.",
    )
    max_cpu_usage: float | None = Field(
        default=None,
        description="Maximum CPU usage observed, or `null` if no data.",
    )
    p80_cpu_usage: float | None = Field(
        default=None,
        description="80th percentile CPU usage, or `null` if no data.",
    )
    p95_cpu_usage: float | None = Field(
        default=None,
        description="95th percentile CPU usage, or `null` if no data.",
    )
    p98_cpu_usage: float | None = Field(
        default=None,
        description="98th percentile CPU usage, or `null` if no data.",
    )
    result: MetricResult | None = Field(
        default=None,
        description="Raw Prometheus `MetricResult` matrix.",
    )


class DeviceMemoryUsageResult(BaseModel):
    """Result of a device memory utilization time-series query.

    Returned by the `bng_device_memory_usage` tool after executing a
    range query that computes memory utilization as a percentage:
    `in_use / (in_use + available) * 100`.
    """

    device: str = Field(description="Device name (Prometheus `source` label).")
    interval: str = Field(description="Lookback window that was queried.")
    step: str = Field(description="Query resolution step.")
    sample_count: int = Field(
        default=0,
        description="Total number of memory utilization samples returned.",
    )
    avg_memory_usage_pct: float | None = Field(
        default=None,
        description="Average memory utilization (%) across all samples, or `null` if no data.",
    )
    min_memory_usage_pct: float | None = Field(
        default=None,
        description="Minimum memory utilization (%) observed, or `null` if no data.",
    )
    max_memory_usage_pct: float | None = Field(
        default=None,
        description="Maximum memory utilization (%) observed, or `null` if no data.",
    )
    p80_memory_usage_pct: float | None = Field(
        default=None,
        description="80th percentile memory utilization (%), or `null` if no data.",
    )
    p95_memory_usage_pct: float | None = Field(
        default=None,
        description="95th percentile memory utilization (%), or `null` if no data.",
    )
    p98_memory_usage_pct: float | None = Field(
        default=None,
        description="98th percentile memory utilization (%), or `null` if no data.",
    )
    result: MetricResult | None = Field(
        default=None,
        description="Raw Prometheus `MetricResult` matrix.",
    )


class DeviceHostsStatsResult(BaseModel):
    """Result of a subscriber host-count instant query.

    Returned by the `bng_device_hosts_stats` tool after executing
    instant queries on
    `state_subscriber_mgmt_statistics_total_hosts_peak_value` and
    `state_subscriber_mgmt_statistics_total_hosts_current_value` for
    IPv4 and IPv6 counters.  The metrics are sourced from the SROS
    xpaths:

    - `/state/subscriber-mgmt/statistics/total-hosts[counter=ipv4]`
    - `/state/subscriber-mgmt/statistics/total-hosts[counter=ipv6]`
    """

    device: str = Field(description="Device name (Prometheus `source` label).")
    ipv4_peak: int | None = Field(
        default=None,
        description="Peak IPv4 subscriber count, or `null` if no data.",
    )
    ipv6_peak: int | None = Field(
        default=None,
        description="Peak IPv6 subscriber count, or `null` if no data.",
    )
    ipv4_current: int | None = Field(
        default=None,
        description="Current IPv4 subscriber count, or `null` if no data.",
    )
    ipv6_current: int | None = Field(
        default=None,
        description="Current IPv6 subscriber count, or `null` if no data.",
    )


class DeviceUnavailabilityResult(BaseModel):
    """Result of a device unavailability time-series check.

    Returned by the `bng_device_unavailability_map` tool after
    executing an `absent_over_time()` range query.
    """

    device: str = Field(description="Device name (Prometheus `source` label).")
    interval: str = Field(description="Lookback window that was queried.")
    step: str = Field(description="Query resolution step.")
    unavailable_steps: int = Field(
        default=0,
        description="Number of steps where the device was absent.",
    )
    currently_available: bool | None = Field(
        default=None,
        description="State at the last step: `true` if reporting, `null` if no data.",
    )
    result: MetricResult | None = Field(
        default=None,
        description="Raw Prometheus `MetricResult` matrix.",
    )
