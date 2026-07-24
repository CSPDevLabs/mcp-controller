"""Pydantic response models for BNG MCP resources and tools."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field  # , FieldSerializationInfo

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


class SapAllocationBySlot(BaseModel):
    """SAP allocation stats for a single (card, mda) pair."""

    card: int | None = Field(
        default=None,
        description="Card slot number, or `null` if absent in labels.",
    )
    mda: int | None = Field(
        default=None,
        description="MDA slot number, or `null` if absent in labels.",
    )
    currently_allocated: float | None = Field(
        default=None,
        description="Current allocated SAP instances (instant query), or `null`.",
    )
    min_allocated: float | None = Field(
        default=None,
        description="Minimum allocated SAP instances observed in the window, or `null`.",
    )
    max_allocated: float | None = Field(
        default=None,
        description="Maximum allocated SAP instances observed in the window, or `null`.",
    )


class SapInstancesAllocatedResult(BaseModel):
    """Result of a SAP-instances-allocated query, broken down per (card, mda).

    Returned by the `sap_instances_allocated` tool after executing a
    range and an instant query on
    `state_card_mda_resource_usage_sap_instances_allocated`.  When
    `card` and/or `mda` arguments are omitted, the corresponding label
    is dropped from the Prometheus selector and one entry per matching
    (card, mda) pair is returned in `by_slot` — results are not
    aggregated across slots.  The metric is sourced from the SROS
    xpath:

    - `/state/card[slot-number=*]/mda[mda-slot=*]/resource-usage/sap-instances/allocated`
    """

    device: str = Field(description="Device name (Prometheus `source` label).")
    interval: str = Field(description="Lookback window that was queried.")
    step: str = Field(description="Query resolution step.")
    by_slot: list[SapAllocationBySlot] = Field(
        default_factory=list,
        description="Per-(card, mda) breakdown of allocation stats.",
    )
    result: MetricResult | None = Field(
        default=None,
        description="Raw Prometheus `MetricResult` matrix.",
    )


class PppSessionsTotalEstablishedResult(BaseModel):
    """Result of a PPP-sessions-established query for a BNG device.

    Returned by the `ppp_sessions_total_established` tool after executing
    a range and an instant query on
    `state_subscriber_mgmt_statistics_sessions_current_value` with
    `sessions_counter="ppp-sessions-total-established"`.  The metric is
    sourced from the SROS xpath:

    - `/state/subscriber-mgmt/statistics/sessions[counter=ppp-sessions-total-established]/current-value`
    """

    device: str = Field(description="Device name (Prometheus `source` label).")
    interval: str = Field(description="Lookback window that was queried.")
    step: str = Field(description="Query resolution step.")
    currently_established: int | None = Field(
        default=None,
        description="Current PPP sessions established (instant query), or `null`.",
    )
    min_established: int | None = Field(
        default=None,
        description="Minimum established sessions observed in the window, or `null`.",
    )
    max_established: int | None = Field(
        default=None,
        description="Maximum established sessions observed in the window, or `null`.",
    )
    result: MetricResult | None = Field(
        default=None,
        description="Raw Prometheus `MetricResult` matrix.",
    )


PolicerDirection = Literal["ingress", "egress"]


class PolicersAllocationBySlot(BaseModel):
    """Policer allocation stats for a single (card, fp) pair."""

    card: int | None = Field(
        default=None,
        description="Card slot number, or `null` if absent in labels.",
    )
    fp: int | None = Field(
        default=None,
        description="FP (forwarding plane) number, or `null` if absent in labels.",
    )
    currently_allocated: float | None = Field(
        default=None,
        description="Current allocated policer count (instant query), or `null`.",
    )
    currently_total: float | None = Field(
        default=None,
        description="Current total policer capacity (instant query), or `null`.",
    )
    currently_pct: float | None = Field(
        default=None,
        description=(
            "Current utilisation `currently_allocated / currently_total * 100`, "
            "or `null` when total is zero or missing."
        ),
    )
    min_pct: float | None = Field(
        default=None,
        description="Minimum utilisation (%) observed in the window, or `null`.",
    )
    max_pct: float | None = Field(
        default=None,
        description="Maximum utilisation (%) observed in the window, or `null`.",
    )


class PolicersAllocatedResult(BaseModel):
    """Result of an ingress/egress policers-allocated query, per (card, fp).

    Returned by the `ingress_policers_allocated` and
    `egress_policers_allocated` tools after executing instant queries on
    `state_card_fp_resource_usage_{direction}_policers_allocated` and
    `state_card_fp_resource_usage_{direction}_policers_total`, plus a
    range query on their utilisation ratio.  When `card` and/or `fp`
    arguments are omitted, the corresponding label is dropped from the
    Prometheus selector and one entry per matching (card, fp) pair is
    returned in `by_slot`.  The metrics are sourced from the SROS
    xpaths:

    - `/state/card[slot-number=*]/fp[fp-number=*]/resource-usage/ingress-policers`
    - `/state/card[slot-number=*]/fp[fp-number=*]/resource-usage/egress-policers`
    """

    device: str = Field(description="Device name (Prometheus `source` label).")
    direction: PolicerDirection = Field(
        description='Policer direction: `"ingress"` or `"egress"`.',
    )
    interval: str = Field(description="Lookback window that was queried.")
    step: str = Field(description="Query resolution step.")
    by_slot: list[PolicersAllocationBySlot] = Field(
        default_factory=list,
        description="Per-(card, fp) breakdown of policer allocation stats.",
    )
    result: MetricResult | None = Field(
        default=None,
        description="Raw Prometheus `MetricResult` matrix of utilisation (%).",
    )


QueuesDirection = Literal["ingress", "egress"]


class QueuesAllocationBySlot(BaseModel):
    """Queues allocation stats for a single (card, fp) pair."""

    card: int | None = Field(
        default=None,
        description="Card slot number, or `null` if absent in labels.",
    )
    fp: int | None = Field(
        default=None,
        description="FP (forwarding plane) number, or `null` if absent in labels.",
    )
    currently_allocated: float | None = Field(
        default=None,
        description="Current allocated queue count (instant query), or `null`.",
    )
    currently_total: float | None = Field(
        default=None,
        description="Current total queue capacity (instant query), or `null`.",
    )
    currently_pct: float | None = Field(
        default=None,
        description=(
            "Current utilisation `currently_allocated / currently_total * 100`, "
            "or `null` when total is zero or missing."
        ),
    )
    min_pct: float | None = Field(
        default=None,
        description="Minimum utilisation (%) observed in the window, or `null`.",
    )
    max_pct: float | None = Field(
        default=None,
        description="Maximum utilisation (%) observed in the window, or `null`.",
    )


class QueuesAllocatedResult(BaseModel):
    """Result of an ingress/egress queues-allocated query, per (card, fp).

    Returned by the `ingress_queues_allocated` and
    `egress_queues_allocated` tools after executing instant queries on
    `state_card_fp_resource_usage_{direction}_queues_allocated` and
    `state_card_fp_resource_usage_{direction}_queues_total`, plus a
    range query on their utilisation ratio.  When `card` and/or `fp`
    arguments are omitted, the corresponding label is dropped from the
    Prometheus selector and one entry per matching (card, fp) pair is
    returned in `by_slot`.  The metrics are sourced from the SROS
    xpaths:

    - `/state/card[slot-number=*]/fp[fp-number=*]/resource-usage/ingress-queues`
    - `/state/card[slot-number=*]/fp[fp-number=*]/resource-usage/egress-queues`
    """

    device: str = Field(description="Device name (Prometheus `source` label).")
    direction: QueuesDirection = Field(
        description='Queues direction: `"ingress"` or `"egress"`.',
    )
    interval: str = Field(description="Lookback window that was queried.")
    step: str = Field(description="Query resolution step.")
    by_slot: list[QueuesAllocationBySlot] = Field(
        default_factory=list,
        description="Per-(card, fp) breakdown of queues allocation stats.",
    )
    result: MetricResult | None = Field(
        default=None,
        description="Raw Prometheus `MetricResult` matrix of utilisation (%).",
    )


class SubscriberNextHopEntriesResult(BaseModel):
    """Result of a subscriber-next-hop-entries allocation query for a BNG device.

    Returned by the `subscriber_next_hop_entries_allocated` tool after
    executing instant queries on
    `state_system_resource_usage_subscriber_next_hop_entries_allocated` and
    `state_system_resource_usage_subscriber_next_hop_entries_total`, plus a
    range query on their utilisation ratio.  The metrics are sourced from
    the SROS xpath:

    - `/state/system/resource-usage/subscriber-next-hop-entries`
    """

    device: str = Field(description="Device name (Prometheus `source` label).")
    interval: str = Field(description="Lookback window that was queried.")
    step: str = Field(description="Query resolution step.")
    currently_allocated: float | None = Field(
        default=None,
        description="Current allocated subscriber next-hop entries (instant query), or `null`.",
    )
    currently_total: float | None = Field(
        default=None,
        description="Current total subscriber next-hop entries capacity (instant query), or `null`.",
    )
    currently_pct: float | None = Field(
        default=None,
        description=(
            "Current utilisation `currently_allocated / currently_total * 100`, "
            "or `null` when total is zero or missing."
        ),
    )
    min_pct: float | None = Field(
        default=None,
        description="Minimum utilisation (%) observed in the window, or `null`.",
    )
    max_pct: float | None = Field(
        default=None,
        description="Maximum utilisation (%) observed in the window, or `null`.",
    )
    result: MetricResult | None = Field(
        default=None,
        description="Raw Prometheus `MetricResult` matrix of utilisation (%).",
    )


HealthStatus = Literal["green", "yellow", "red", "unknown"]


class DeviceHealth(BaseModel):
    """Per-device health assessment for the BNG health summary.

    Derived from instant queries on CPU usage and memory utilisation.
    A device that is not reporting metrics is reported as `"red"` with
    `available=False`; a device with no usable data is `"unknown"`.
    """

    device: str = Field(description="Device name (Prometheus `source` label).")
    status: HealthStatus = Field(description="Aggregated per-device health status.")
    available: bool = Field(
        description="Whether the device is currently reporting metrics.",
    )
    cpu_usage_pct: float | None = Field(
        default=None,
        description="Current CPU usage (%), or `null` if no data.",
    )
    memory_usage_pct: float | None = Field(
        default=None,
        description="Current memory utilisation (%), or `null` if no data.",
    )
    reasons: list[str] = Field(
        default_factory=list,
        description="Human-readable explanations for a non-green status.",
    )


class BngHealthSummary(BaseModel):
    """Aggregated real-time health status across all BNG devices.

    Returned by the `bng_health_summary` tool. `overall_status` is the
    worst status observed across all assessed devices.
    """

    overall_status: HealthStatus = Field(
        description="Worst status across all devices (`red` > `yellow` > `green`).",
    )
    total_devices: int = Field(
        default=0,
        description="Number of BNG device targets assessed.",
    )
    status_counts: dict[HealthStatus, int] = Field(
        default_factory=dict,
        description="Count of devices in each health status.",
    )
    devices: list[DeviceHealth] = Field(
        default_factory=list,
        description="Per-device health assessments.",
    )
    generated_at: datetime = Field(
        description="UTC timestamp when the summary was produced.",
    )


@dataclass(frozen=True, slots=True)
class QueryWindow:
    """Resolved query time window returned by `resolve_query_window`.

    Holds all parameters needed to execute a Prometheus range query
    after parsing and validating the caller-supplied `interval`, `step`,
    and optional `start_time`.
    """

    prom_interval: str
    prom_step: str
    start_dt: datetime
    end_dt: datetime
