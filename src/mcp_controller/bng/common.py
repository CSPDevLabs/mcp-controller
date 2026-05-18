"""Shared helpers for BNG MCP resources and tools."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import NoReturn

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ResourceError
from mcp.server.fastmcp.server import Context

from mcp_controller.bng.types import QueryWindow, TargetSummary
from mcp_controller.core.k8s_types import NetworkDeviceTarget, NetworkHostTarget
from mcp_controller.core.types import ErrorResponse, MetricResult
from mcp_controller.core.kubernetes_client import (
    KubernetesBadRequestError,
    KubernetesClientError,
    KubernetesNamespaceNotFoundError,
    KubernetesNotFoundError,
    KubernetesPermissionError,
    KubernetesSchemaError,
)
from mcp_controller.core.prometheus_client import (
    PrometheusClient,
    PrometheusClientError,
)

logger = logging.getLogger(__name__)


async def _raise_resource_error(
    uri: str,
    exc: KubernetesClientError | NotImplementedError,
    ctx: Context | None = None,
) -> NoReturn:
    """Classify a K8s error and raise a `ResourceError`.

    Resource-facing counterpart of `_raise_k8s_error`: logs the error,
    optionally notifies the MCP client, and raises `ResourceError` so
    FastMCP surfaces it as a proper resource read failure rather than
    returning an error payload as content.

    Args:
        uri: The resource URI that failed (for context in the message).
        exc: The caught `KubernetesClientError` (or subclass), or a
            `NotImplementedError` for capabilities not yet available.
        ctx: Optional MCP request context for client-visible error messages.

    Raises:
        ResourceError: always raised with a structured JSON error message.
    """
    if isinstance(exc, NotImplementedError):
        category = "not_implemented"
    elif isinstance(exc, KubernetesBadRequestError):
        category = "bad_request"
    elif isinstance(exc, KubernetesNamespaceNotFoundError):
        category = "namespace_not_found"
    elif isinstance(exc, KubernetesNotFoundError):
        category = "not_found"
    elif isinstance(exc, KubernetesPermissionError):
        category = "permission_denied"
    elif isinstance(exc, KubernetesSchemaError):
        category = "schema_validation"
    else:
        category = "api_error"

    detail = str(exc)
    logger.error("%s: %s (%s)", uri, detail, category)
    if ctx is not None:
        await ctx.error(f"{uri}: {detail}")

    raise ResourceError(
        ErrorResponse(error=category, detail=detail, uri=uri).model_dump_json(
            exclude_none=True, indent=2
        )
    ) from exc


async def _raise_k8s_error(
    uri: str,
    exc: KubernetesClientError | NotImplementedError,
    ctx: Context | None = None,
) -> NoReturn:
    """Classify a K8s error and raise a structured `ErrorResponse`.

    Tool-facing counterpart of `_raise_resource_error`: logs the error,
    optionally notifies the MCP client, and raises an `ErrorResponse`
    exception.  FastMCP catches the exception and produces a
    ``CallToolResult(isError=True)`` automatically.

    Args:
        uri: The resource URI that failed (for context in the message).
        exc: The caught `KubernetesClientError` (or subclass), or a
            `NotImplementedError` for capabilities not yet available.
        ctx: Optional MCP request context for client-visible error messages.

    Raises:
        ErrorResponse: always raised with `error`, `detail`, and `uri` fields.
    """
    if isinstance(exc, NotImplementedError):
        category = "not_implemented"
    elif isinstance(exc, KubernetesBadRequestError):
        category = "bad_request"
    elif isinstance(exc, KubernetesNamespaceNotFoundError):
        category = "namespace_not_found"
    elif isinstance(exc, KubernetesNotFoundError):
        category = "not_found"
    elif isinstance(exc, KubernetesPermissionError):
        category = "permission_denied"
    elif isinstance(exc, KubernetesSchemaError):
        category = "schema_validation"
    else:
        category = "api_error"

    detail = str(exc)
    logger.error("%s: %s (%s)", uri, detail, category)
    if ctx is not None:
        await ctx.error(f"{uri}: {detail}")

    raise ErrorResponse(error=category, detail=detail, uri=uri) from exc


# Mapping from duration suffix to `timedelta` keyword argument.
_DURATION_UNITS: dict[str, str] = {
    "s": "seconds",
    "sec": "seconds",
    "second": "seconds",
    "seconds": "seconds",
    "m": "minutes",
    "min": "minutes",
    "minute": "minutes",
    "minutes": "minutes",
    "h": "hours",
    "hr": "hours",
    "hour": "hours",
    "hours": "hours",
    "d": "days",
    "day": "days",
    "days": "days",
    "w": "weeks",
    "wk": "weeks",
    "week": "weeks",
    "weeks": "weeks",
}

# Canonical Prometheus suffix for each alias.
# Prometheus accepts only: s, m, h, d, w, y.
_PROMETHEUS_SUFFIX: dict[str, str] = {
    "s": "s",
    "sec": "s",
    "second": "s",
    "seconds": "s",
    "m": "m",
    "min": "m",
    "minute": "m",
    "minutes": "m",
    "h": "h",
    "hr": "h",
    "hour": "h",
    "hours": "h",
    "d": "d",
    "day": "d",
    "days": "d",
    "w": "w",
    "wk": "w",
    "week": "w",
    "weeks": "w",
}


def parse_duration(value: str) -> tuple[timedelta, str]:
    """Parse a Prometheus-style or human-friendly duration into a `timedelta`.

    Accepts shorthand (`"5m"`, `"1h"`, `"30s"`) and human-friendly forms
    (`"5min"`, `"2hours"`, `"7days"`, `"1week"`).  Case-insensitive.
    Fractional values are supported (e.g. `"1.5h"`).

    The second element of the returned tuple is the value normalized to
    a Prometheus-compatible duration string (e.g. `"30sec"` becomes
    `"30s"`, `"2hours"` becomes `"2h"`).

    Args:
        value: Duration string to parse.

    Returns:
        A `(timedelta, prometheus_duration)` tuple.

    Raises:
        ValueError: If the string cannot be parsed (missing number or
            unknown unit suffix).
    """
    normalized = value.strip().lower()
    i = 0
    while i < len(normalized) and (normalized[i].isdigit() or normalized[i] == "."):
        i += 1
    num_str = normalized[:i]
    unit = normalized[i:]
    if not num_str or unit not in _DURATION_UNITS:
        raise ValueError(f"Cannot parse duration '{value}': unknown unit '{unit}'")
    delta = timedelta(**{_DURATION_UNITS[unit]: float(num_str)})
    prom_str = f"{num_str}{_PROMETHEUS_SUFFIX[unit]}"
    return delta, prom_str


def parse_start_time(value: str) -> datetime:
    """Parse and validate an ISO 8601 start-time string.

    The value must be a valid ISO 8601 datetime that is timezone-aware
    and not in the future.

    Args:
        value: ISO 8601 datetime string, e.g. `"2026-04-07T12:00:00+00:00"`.

    Returns:
        A timezone-aware `datetime` in UTC.

    Raises:
        ValueError: If the string is not valid ISO 8601, is naive
            (missing timezone), or represents a future timestamp.
    """
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Cannot parse start_time '{value}': {exc}") from exc

    if dt.tzinfo is None:
        raise ValueError(
            f"start_time '{value}' must include a timezone offset (e.g. '+00:00' or 'Z')"
        )

    dt_utc = dt.astimezone(timezone.utc)
    if dt_utc > datetime.now(tz=timezone.utc):
        raise ValueError(f"start_time '{value}' is in the future")

    return dt_utc


def resolve_query_window(
    interval: str,
    step: str,
    device: str,
    start_time: str = "",
) -> QueryWindow:
    """Resolve interval, step, and optional start_time into a `QueryWindow`.

    Parses Prometheus-style duration strings and computes the query
    time range, raising a typed `ErrorResponse` on any parse failure.

    Args:
        interval: Lookback window duration (e.g. `"1h"`, `"30m"`).
        step: Query resolution step (e.g. `"1m"`, `"15s"`).
        device: Device identifier used in error context.
        start_time: Optional ISO 8601 start time. When empty the
            window ends at `now`.

    Returns:
        `QueryWindow` with `prom_interval`, `prom_step`,
        `start_dt`, and `end_dt`.

    Raises:
        ErrorResponse: on invalid `interval`, `step`, or `start_time`.
    """
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

    return QueryWindow(
        prom_interval=prom_interval,
        prom_step=prom_step,
        start_dt=start_dt,
        end_dt=end_dt,
    )


def resolve_device_source(device: str, default_namespace: str) -> str:
    """Normalize a device identifier into a Prometheus `source` label value.

    gNMIc targets use `namespace/name` as the source label (e.g.
    `"nok-bng/clab-sros-bngt-bng1"`).  Callers may supply either form:

    - ``"clab-sros-bngt-bng1"`` → ``"nok-bng/clab-sros-bngt-bng1"``
      (default namespace prepended)
    - ``"nok-bng/clab-sros-bngt-bng1"`` → returned unchanged

    Args:
        device: Device name, with or without a namespace prefix.
        default_namespace: Namespace to prepend when `device` has none.

    Returns:
        Fully qualified ``namespace/name`` string.
    """
    if "/" in device:
        return device
    return f"{default_namespace}/{device}"


async def verify_device_target(device_source: str, mcp: FastMCP) -> None:
    """Verify that a device exists by reading the ``bng://targets/devices`` resource.

    Reads the static ``bng://targets/devices`` resource via the MCP server
    and checks that a target with the expected name and namespace is present.
    The static resource lists all ``NetworkDeviceTarget`` CRs in the
    controller's default namespace, which is the expected scope for device
    metric queries.

    Args:
        device_source: Fully qualified ``namespace/name`` string
            (output of `resolve_device_source`).
        mcp: The FastMCP server instance used to read registered resources.

    Raises:
        KubernetesNotFoundError: if the device is not found, or if the
            returned ``ObjectMeta.namespace`` does not match the expected
            namespace.
        KubernetesClientError: on any K8s backend error reported by
            the resource handler.
    """
    namespace, name = device_source.split("/", 1)

    try:
        results = await mcp.read_resource("bng://targets/devices")
    except ResourceError as exc:
        raise KubernetesClientError(str(exc)) from exc

    content_parts = [r.content for r in results if isinstance(r.content, str)]
    content = "\n".join(content_parts)

    if not content:
        raise KubernetesNotFoundError(
            f"NetworkDeviceTarget '{name}' not found in namespace '{namespace}'"
        )

    data = json.loads(content)

    for target in data:
        meta = target.get("metadata", {})
        if meta.get("name") == name and meta.get("namespace") == namespace:
            return

    raise KubernetesNotFoundError(
        f"NetworkDeviceTarget '{name}' not found in namespace '{namespace}'"
    )


async def prepare_device(device: str, k8s_namespace: str, mcp: FastMCP, ctx: Context) -> str:
    """Resolve and verify a device identifier.

    Normalizes `device` to a fully-qualified `namespace/name` source label,
    logs when the name is adjusted, and verifies the device exists in the
    registered targets.

    Args:
        device: Device name, with or without a namespace prefix.
        k8s_namespace: Default namespace used when `device` has none.
        mcp: The FastMCP server instance used to verify the target.
        ctx: MCP request context for client-visible progress messages.

    Returns:
        Fully qualified `namespace/name` source label string.

    Raises:
        ErrorResponse: if the device is not found in the registered targets.
    """
    resolved = resolve_device_source(device, k8s_namespace)
    if resolved != device:
        logger.info("Device name resolved: %r -> %r", device, resolved)
        await ctx.info(f"Device name resolved: {device!r} → {resolved!r}")
    try:
        await verify_device_target(resolved, mcp)
    except KubernetesClientError as exc:
        raise ErrorResponse(
            error="device_not_found",
            detail=str(exc),
            device=resolved,
        ) from exc
    return resolved


@dataclass(frozen=True, slots=True)
class SampleStats:
    """Summary statistics computed over metric sample values.

    All fields are `None` when the input series contains no samples.
    """

    count: int
    avg: float | None
    min: float | None
    max: float | None


def compute_sample_stats(result: MetricResult) -> SampleStats:
    """Compute count, average, min, and max over all samples in a `MetricResult`.

    Args:
        result: Prometheus range-query result containing zero or more series.

    Returns:
        `SampleStats` with aggregated statistics.  All stat fields are
        `None` when the result contains no samples.
    """
    values: list[float] = [sample.value for series in result.series for sample in series.samples]
    if not values:
        return SampleStats(count=0, avg=None, min=None, max=None)
    return SampleStats(
        count=len(values),
        avg=sum(values) / len(values),
        min=min(values),
        max=max(values),
    )


def _target_summary(target: NetworkDeviceTarget | NetworkHostTarget) -> TargetSummary:
    """Extract a flat summary from a device or host target CR.

    Args:
        target: A validated `NetworkDeviceTarget` or `NetworkHostTarget`.

    Returns:
        A `TargetSummary` with `name`, `address`, `hostname`, `kind`,
        `namespace`.
    """
    return TargetSummary(
        name=target.metadata.name if target.metadata else None,
        address=target.spec.address if target.spec else None,
        hostname=target.spec.hostname if target.spec else None,
        kind=target.kind,
        namespace=target.metadata.namespace if target.metadata else None,
    )


def _serialize_targets(targets: list[NetworkDeviceTarget] | list[NetworkHostTarget]) -> str:
    """Serialize a list of target CRs to JSON using Pydantic's `model_dump`.

    Args:
        targets: List of validated target models.

    Returns:
        Pretty-printed JSON string.
    """
    return json.dumps(
        [t.model_dump(mode="json", by_alias=True) for t in targets],
        indent=2,
    )


async def execute_instant_query(
    prom: PrometheusClient,
    promql: str,
    device: str,
    tool_name: str,
) -> MetricResult:
    """Execute a Prometheus instant query and handle expected errors.

    Args:
        prom: The Prometheus client instance.
        promql: The PromQL query string.
        device: Device name for error context.
        tool_name: Tool name for logging context.

    Returns:
        A `MetricResult` with `result_type="vector"`.

    Raises:
        ErrorResponse: on Prometheus client failure or unexpected result type.
    """
    try:
        result = await prom.query(promql)
    except PrometheusClientError as exc:
        logger.error("%s failed: %s", tool_name, exc)
        raise ErrorResponse(
            error="prometheus_error",
            detail=str(exc),
            device=device,
        ) from exc

    if not isinstance(result, MetricResult):
        logger.error(
            "%s: expected MetricResult, got %s",
            tool_name,
            type(result).__name__,
        )
        raise ErrorResponse(
            error="unexpected_result_type",
            detail=f"Expected vector result, got {type(result).__name__}",
            device=device,
        )

    return result


async def execute_range_query(
    prom: PrometheusClient,
    promql: str,
    start: str,
    end: str,
    step: str,
    device: str,
    interval: str,
    tool_name: str,
) -> MetricResult:
    """Execute a Prometheus range query and handle expected errors.

    Args:
        prom: The Prometheus client instance.
        promql: The PromQL query string.
        start: Query start time (ISO 8601).
        end: Query end time (ISO 8601).
        step: Query resolution step (e.g. "15s").
        device: Device name for error context.
        interval: Original interval string for error context.
        tool_name: Tool name for logging context.

    Returns:
        A `MetricResult` on success.

    Raises:
        ErrorResponse: on Prometheus client failure or unexpected result type.
    """
    try:
        result = await prom.query_range(
            promql,
            start=start,
            end=end,
            step=step,
        )
    except PrometheusClientError as exc:
        logger.error("%s failed: %s", tool_name, exc)
        raise ErrorResponse(
            error="prometheus_error",
            detail=str(exc),
            device=device,
            interval=interval,
        ) from exc

    if not isinstance(result, MetricResult):
        logger.error(
            "%s: expected MetricResult, got %s",
            tool_name,
            type(result).__name__,
        )
        raise ErrorResponse(
            error="unexpected_result_type",
            detail=f"Expected matrix result, got {type(result).__name__}",
            device=device,
            interval=interval,
        )

    return result
