"""Tests for the `bng_health_summary` BNG tool.

The tool lists every `NetworkDeviceTarget`, keeps only the BNG devices
(those whose `spec.gnmic.enabled` is `True` and whose
`spec.gnmic.labels["role"] == "bng"`) and, per kept device, runs two
Prometheus instant queries — CPU usage and memory utilisation — then
classifies each device `green`/`yellow`/`red`/`unknown` and reports an
overall worst-case status. Non-BNG targets are excluded from the summary
entirely.

The tests drive the tool through mocked `KubernetesClient` and
`PrometheusClient` instances swapped in at the factory level (per the
project testing conventions), never patching handler internals. A
regex-based Prometheus `query` side effect returns a per-device value
keyed by the `source` label and the metric name embedded in the PromQL,
so a single mock answers both the CPU and memory queries for every
device.

Threshold reminder (see `bng/tools.py`):

- CPU:    `>= 80` red, `>= 70` yellow, else green.
- Memory: `>= 85` red, `>= 70` yellow, else green.
- Severity order: `red` > `yellow` > `unknown` > `green`.

Source-label note: for a kept device the tool resolves the Prometheus
`source` namespace to the target's own metadata namespace, falling back to
`settings.k8s_namespace` only when the target carries no namespace.
"""

import hashlib
import re
from unittest.mock import AsyncMock, patch

import pytest

from mcp_controller.bng.tools import register_bng_tools
from mcp_controller.config import Settings
from mcp_controller.core.k8s_types import NetworkDeviceTarget
from mcp_controller.core.kubernetes_client import KubernetesClientError
from mcp_controller.core.prometheus_client import PrometheusClient, PrometheusClientError
from mcp_controller.core.types import ErrorResponse, MetricResult, MetricSample, MetricSeries

import logging

logger = logging.getLogger(__name__)

TEST_NAMESPACE = "test-ns"  # settings namespace, used only for the fallback case
DEV_NAMESPACE = "nok"  # metadata namespace of the fixtures
TOOL_NAME = "bng_health_summary"

# Source label a kept device resolves to (its own metadata namespace).
SRC1 = f"{DEV_NAMESPACE}/bng1"
SRC2 = f"{DEV_NAMESPACE}/bng2"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vector(series: list[MetricSeries]) -> MetricResult:
    """Wrap series in a vector (instant-query) `MetricResult`."""
    return MetricResult(query="fixture", result_type="vector", series=series)


def _device(
    name: str,
    *,
    namespace: str = DEV_NAMESPACE,
    bng_gnmic_label: bool = False,
    gnmic_enabled: bool = False,
    sdcio_enabled: bool = False,
) -> NetworkDeviceTarget:
    """Build a `NetworkDeviceTarget`.

    A device is kept by `bng_health_summary` only when it is gnmic-enabled
    and carries the `gnmic.labels.role="bng"` label, i.e. `_bng_device` or
    `_device(..., bng_gnmic_label=True, gnmic_enabled=True)`.

    Each device gets a stable, name-derived `address` so that distinct
    names never collide on a shared literal address; the `hostname` is
    likewise derived from `name`.

    Args:
        name: Metadata name.
        namespace: Metadata namespace. An empty string exercises the
            settings-namespace fallback.
        bng_gnmic_label: When `True`, attach the `gnmic.labels.role="bng"`
            label required for the device to be considered a BNG.
        gnmic_enabled: Whether `spec.gnmic.enabled` is set.
        sdcio_enabled: Whether `spec.sdcio.enabled` is set (irrelevant to
            BNG selection).

    Returns:
        A validated `NetworkDeviceTarget`.
    """
    gnmic: dict = {"enabled": gnmic_enabled}

    # Adding label for BNG devices
    if bng_gnmic_label:
        gnmic["labels"] = {"role": "bng"}
    # Deterministic per-name last octet (1..254) so fixtures are distinct.
    octet = int(hashlib.sha1(name.encode()).hexdigest(), 16) % 254 + 1
    data: dict = {
        "apiVersion": "nok.dev/v1alpha1",
        "kind": "NetworkDeviceTarget",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "address": f"10.0.0.{octet}",
            "hostname": f"{name}.lab.nok.dev",
            "gnmic": gnmic,
            "sdcio": {"enabled": sdcio_enabled},
        },
    }
    return NetworkDeviceTarget.model_validate(data)


def _bng_device(
    name: str,
    *,
    namespace: str = DEV_NAMESPACE,
    sdcio_enabled: bool = False,
) -> NetworkDeviceTarget:
    """Build a device that `bng_health_summary` keeps (gnmic-enabled + role=bng)."""
    return _device(
        name,
        namespace=namespace,
        bng_gnmic_label=True,
        gnmic_enabled=True,
        sdcio_enabled=sdcio_enabled,
    )


def _make_k8s(targets: list[NetworkDeviceTarget]) -> AsyncMock:
    """Build a mock `KubernetesClient` returning the given device targets."""
    mock = AsyncMock()
    mock.list_network_device_targets = AsyncMock(return_value=targets)
    return mock


def _make_health_prom(values: dict[str, dict[str, float | None]]) -> AsyncMock:
    """Build a mock `PrometheusClient` that answers per-device instant queries.

    Args:
        values: Mapping of `source` label to `{"cpu": <pct|None>,
            "mem": <pct|None>}`. A `None` value yields an empty vector so
            the tool's `_instant_value` helper resolves it to "no data".

    Returns:
        An `AsyncMock` whose `query` inspects each PromQL string, extracts
        the `source` label and the metric family, and returns the matching
        value.
    """

    def _side_effect(promql: str) -> MetricResult:
        """Resolve one instant query to its fixture value.

        Parses the `source="<ns>/<name>"` label out of the PromQL and
        infers the metric family from a substring match: a CPU query
        (`cpu_summary_usage_cpu_usage`) returns the `"cpu"` entry, a memory
        query (`memory_pools_summary`) returns the `"mem"` entry. When no
        source matches, the metric is unrecognised, or the fixture value is
        `None`, an empty vector is returned so the tool treats it as "no
        data"; otherwise a single-sample vector labelled with `source` is
        returned.

        Args:
            promql: The PromQL expression passed to `PrometheusClient.query`.

        Returns:
            A vector `MetricResult` with one sample for the resolved value,
            or an empty vector when there is no data for this query.
        """
        match = re.search(r'source="([^"]+)"', promql)
        source = match.group(1) if match else ""
        entry = values.get(source, {})
        if "cpu_summary_usage_cpu_usage" in promql:
            value = entry.get("cpu")
        elif "memory_pools_summary" in promql:
            value = entry.get("mem")
        else:
            value = None
        if value is None:
            return _vector([])
        samples = [MetricSample(timestamp=0.0, value=value)]
        return _vector([MetricSeries(labels={"source": source}, samples=samples)])

    mock = AsyncMock(spec=PrometheusClient)
    mock.query = AsyncMock(side_effect=_side_effect)
    return mock


def _register(mock_k8s: AsyncMock, mock_prom: AsyncMock):
    """Register BNG tools with mocked clients and return the health-summary tool.

    Args:
        mock_k8s: Mock `KubernetesClient` instance.
        mock_prom: Mock `PrometheusClient` instance.

    Returns:
        The registered `bng_health_summary` tool object.
    """
    from mcp.server.fastmcp import FastMCP

    settings = Settings(k8s_namespace=TEST_NAMESPACE)
    mcp = FastMCP(name="test")
    with (
        patch("mcp_controller.bng.tools.KubernetesClient", return_value=mock_k8s),
        patch("mcp_controller.bng.tools.PrometheusClient", return_value=mock_prom),
    ):
        register_bng_tools(mcp, settings)
    return mcp._tool_manager._tools[TOOL_NAME]


# ---------------------------------------------------------------------------
# Per-device classification
# ---------------------------------------------------------------------------


class TestDeviceClassification:
    """Classification of a single device from its CPU/memory instant values."""

    async def test_all_green(self, mock_ctx: AsyncMock) -> None:
        # Scramble every gnmic/label/sdcio combination and call the tool for
        # each. A target is assessed only when it is a BNG (gnmic-enabled AND
        # role=bng); every other combination is excluded, so the expected
        # outcome depends on the combination rather than always being green.
        for bng_gnmic_label, gnmic_enabled, sdcio_enabled in [
            (True, True, True),
            (True, True, False),
            (True, False, True),
            (True, False, False),
            (False, True, True),
            (False, True, False),
            (False, False, True),
            (False, False, False),
        ]:
            k8s = _make_k8s(
                [
                    _device(
                        "bng1",
                        bng_gnmic_label=bng_gnmic_label,
                        gnmic_enabled=gnmic_enabled,
                        sdcio_enabled=sdcio_enabled,
                    )
                ]
            )
            prom = _make_health_prom({SRC1: {"cpu": 20.0, "mem": 50.0}})
            tool = _register(k8s, prom)

            result = await tool.fn(ctx=mock_ctx)

            logger.info(
                "bng_gnmic_label=%s gnmic_enabled=%s sdcio_enabled=%s",
                bng_gnmic_label,
                gnmic_enabled,
                sdcio_enabled,
            )
            logger.info("result=%s", result)

            is_bng = bng_gnmic_label and gnmic_enabled
            if is_bng:
                # Healthy CPU/memory on a kept device -> green.
                assert result.overall_status == "green"
                assert result.total_devices == 1
                assert result.status_counts == {"green": 1, "yellow": 0, "red": 0, "unknown": 0}
                device = result.devices[0]
                assert device.device == SRC1
                assert device.status == "green"
                assert device.available is True
                assert device.cpu_usage_pct == pytest.approx(20.0)
                assert device.memory_usage_pct == pytest.approx(50.0)
                assert device.reasons == []
            else:
                # Non-BNG target -> excluded from the summary entirely.
                assert result.total_devices == 0
                assert result.overall_status == "unknown"
                assert result.status_counts == {"green": 0, "yellow": 0, "red": 0, "unknown": 0}
                assert result.devices == []

    async def test_yellow_from_cpu(self, mock_ctx: AsyncMock) -> None:
        k8s = _make_k8s(
            [_device("bng1", bng_gnmic_label=True, gnmic_enabled=True, sdcio_enabled=False)]
        )
        prom = _make_health_prom({SRC1: {"cpu": 75.0, "mem": 50.0}})
        tool = _register(k8s, prom)

        result = await tool.fn(ctx=mock_ctx)

        assert result.overall_status == "yellow"
        assert result.status_counts["yellow"] == 1
        device = result.devices[0]
        assert device.status == "yellow"
        assert any("CPU usage 75.0% is yellow" in r for r in device.reasons)

    async def test_red_from_memory(self, mock_ctx: AsyncMock) -> None:
        k8s = _make_k8s([_bng_device("bng1")])
        prom = _make_health_prom({SRC1: {"cpu": 50.0, "mem": 90.0}})
        tool = _register(k8s, prom)

        result = await tool.fn(ctx=mock_ctx)

        assert result.overall_status == "red"
        assert result.status_counts["red"] == 1
        device = result.devices[0]
        assert device.status == "red"
        assert any("memory utilisation 90.0% is red" in r for r in device.reasons)

    async def test_unreachable_device_is_red_unavailable(self, mock_ctx: AsyncMock) -> None:
        # Neither CPU nor memory reports any sample.
        k8s = _make_k8s([_bng_device("bng1")])
        prom = _make_health_prom({SRC1: {"cpu": None, "mem": None}})
        tool = _register(k8s, prom)

        result = await tool.fn(ctx=mock_ctx)

        assert result.overall_status == "red"
        device = result.devices[0]
        assert device.status == "red"
        assert device.available is False
        assert device.cpu_usage_pct is None
        assert device.memory_usage_pct is None
        assert device.reasons == ["device is not reporting CPU or memory metrics"]

    async def test_partial_data_resolves_to_unknown(self, mock_ctx: AsyncMock) -> None:
        # CPU healthy but memory missing: the device is available, yet the
        # missing signal outranks green, so the device status is `unknown`.
        k8s = _make_k8s([_bng_device("bng1")])
        prom = _make_health_prom({SRC1: {"cpu": 20.0, "mem": None}})
        tool = _register(k8s, prom)

        result = await tool.fn(ctx=mock_ctx)

        device = result.devices[0]
        assert device.available is True
        assert device.status == "unknown"
        assert device.cpu_usage_pct == pytest.approx(20.0)
        assert device.memory_usage_pct is None
        assert result.overall_status == "unknown"

    async def test_boundary_values_are_inclusive(self, mock_ctx: AsyncMock) -> None:
        # Exactly on the CPU yellow boundary (70) and memory red boundary (85).
        k8s = _make_k8s([_bng_device("bng1")])
        prom = _make_health_prom({SRC1: {"cpu": 70.0, "mem": 85.0}})
        tool = _register(k8s, prom)

        result = await tool.fn(ctx=mock_ctx)

        # Memory red dominates CPU yellow.
        assert result.devices[0].status == "red"


# ---------------------------------------------------------------------------
# Device selection (BNG-only)
# ---------------------------------------------------------------------------


class TestDeviceSelection:
    """Only gnmic-enabled targets labelled `role=bng` enter the summary."""

    async def test_excludes_non_bng_labelled_device(self, mock_ctx: AsyncMock) -> None:
        # gnmic is enabled but the role=bng label is absent -> excluded.
        k8s = _make_k8s([_device("bng1", gnmic_enabled=True, bng_gnmic_label=False)])
        prom = _make_health_prom({SRC1: {"cpu": 20.0, "mem": 50.0}})
        tool = _register(k8s, prom)

        result = await tool.fn(ctx=mock_ctx)

        assert result.total_devices == 0
        assert result.devices == []
        prom.query.assert_not_awaited()

    async def test_excludes_gnmic_disabled_device(self, mock_ctx: AsyncMock) -> None:
        # role=bng label present but gnmic disabled -> excluded.
        k8s = _make_k8s([_device("bng1", gnmic_enabled=False, bng_gnmic_label=True)])
        prom = _make_health_prom({SRC1: {"cpu": 20.0, "mem": 50.0}})
        tool = _register(k8s, prom)

        result = await tool.fn(ctx=mock_ctx)

        assert result.total_devices == 0
        assert result.devices == []
        prom.query.assert_not_awaited()

    async def test_keeps_only_bng_devices_in_mixed_fleet(self, mock_ctx: AsyncMock) -> None:
        # Fleet mixes a qualifying BNG, a non-BNG, and a disabled BNG; only
        # the qualifying device is assessed.
        k8s = _make_k8s(
            [
                _bng_device("bng1"),
                _device("bng2", gnmic_enabled=True, bng_gnmic_label=False),
                _device("bng3", gnmic_enabled=False, bng_gnmic_label=True),
            ]
        )
        prom = _make_health_prom({SRC1: {"cpu": 20.0, "mem": 50.0}})
        tool = _register(k8s, prom)

        result = await tool.fn(ctx=mock_ctx)

        assert result.total_devices == 1
        assert [d.device for d in result.devices] == [SRC1]
        assert result.overall_status == "green"


# ---------------------------------------------------------------------------
# Fleet aggregation
# ---------------------------------------------------------------------------


class TestFleetAggregation:
    """Aggregation of the overall status and counts across multiple devices."""

    async def test_overall_is_worst_case(self, mock_ctx: AsyncMock) -> None:
        k8s = _make_k8s([_bng_device("bng1"), _bng_device("bng2")])
        prom = _make_health_prom(
            {
                SRC1: {"cpu": 10.0, "mem": 20.0},  # green
                SRC2: {"cpu": 95.0, "mem": 40.0},  # red (cpu)
            }
        )
        tool = _register(k8s, prom)

        result = await tool.fn(ctx=mock_ctx)

        assert result.total_devices == 2
        assert result.overall_status == "red"
        assert result.status_counts == {"green": 1, "yellow": 0, "red": 1, "unknown": 0}
        by_source = {d.device: d.status for d in result.devices}
        assert by_source == {SRC1: "green", SRC2: "red"}

    async def test_no_devices_yields_unknown(self, mock_ctx: AsyncMock) -> None:
        k8s = _make_k8s([])
        prom = _make_health_prom({})
        tool = _register(k8s, prom)

        result = await tool.fn(ctx=mock_ctx)

        assert result.total_devices == 0
        assert result.overall_status == "unknown"
        assert result.status_counts == {"green": 0, "yellow": 0, "red": 0, "unknown": 0}
        assert result.devices == []
        prom.query.assert_not_awaited()

    async def test_generated_at_is_timezone_aware(self, mock_ctx: AsyncMock) -> None:
        k8s = _make_k8s([_bng_device("bng1")])
        prom = _make_health_prom({SRC1: {"cpu": 20.0, "mem": 50.0}})
        tool = _register(k8s, prom)

        result = await tool.fn(ctx=mock_ctx)

        assert result.generated_at.tzinfo is not None


# ---------------------------------------------------------------------------
# Source-label resolution and PromQL shape
# ---------------------------------------------------------------------------


class TestQueryConstruction:
    """The `source` label and PromQL expressions issued per device."""

    async def test_uses_target_namespace(self, mock_ctx: AsyncMock) -> None:
        # A kept device's source uses its own metadata namespace.
        k8s = _make_k8s([_bng_device("bng1", namespace="nok")])
        prom = _make_health_prom({"nok/bng1": {"cpu": 20.0, "mem": 50.0}})
        tool = _register(k8s, prom)

        result = await tool.fn(ctx=mock_ctx)

        assert result.devices[0].device == "nok/bng1"
        queried = [call.args[0] for call in prom.query.await_args_list]
        assert len(queried) == 2
        assert all('source="nok/bng1"' in q for q in queried)
        assert any("state_system_cpu_summary_usage_cpu_usage" in q for q in queried)
        assert any("state_system_memory_pools_summary_total_in_use" in q for q in queried)
        assert any("state_system_memory_pools_summary_available_memory" in q for q in queried)

    async def test_falls_back_to_settings_namespace(self, mock_ctx: AsyncMock) -> None:
        # A kept device without a metadata namespace falls back to the
        # settings namespace for the Prometheus `source` label.
        k8s = _make_k8s([_bng_device("bng1", namespace="")])
        fallback_source = f"{TEST_NAMESPACE}/bng1"
        prom = _make_health_prom({fallback_source: {"cpu": 20.0, "mem": 50.0}})
        tool = _register(k8s, prom)

        result = await tool.fn(ctx=mock_ctx)

        assert result.devices[0].device == fallback_source
        queried = [call.args[0] for call in prom.query.await_args_list]
        assert all(f'source="{fallback_source}"' in q for q in queried)


# ---------------------------------------------------------------------------
# Error handling and resilience
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Kubernetes errors abort; Prometheus errors degrade gracefully."""

    async def test_k8s_error_raises_error_response(self, mock_ctx: AsyncMock) -> None:
        k8s = AsyncMock()
        k8s.list_network_device_targets = AsyncMock(side_effect=KubernetesClientError("api down"))
        prom = _make_health_prom({})
        tool = _register(k8s, prom)

        with pytest.raises(ErrorResponse) as exc_info:
            await tool.fn(ctx=mock_ctx)

        assert exc_info.value.error == "api_error"
        assert "api down" in exc_info.value.detail
        prom.query.assert_not_awaited()

    async def test_prometheus_failure_is_tolerated(self, mock_ctx: AsyncMock) -> None:
        # Every instant query fails: the device degrades to red/unavailable
        # but the summary is still produced (no exception propagates).
        k8s = _make_k8s([_bng_device("bng1"), _bng_device("bng2")])
        prom = AsyncMock(spec=PrometheusClient)
        prom.query = AsyncMock(side_effect=PrometheusClientError("connection refused"))
        tool = _register(k8s, prom)

        result = await tool.fn(ctx=mock_ctx)

        assert result.total_devices == 2
        assert result.overall_status == "red"
        assert result.status_counts["red"] == 2
        assert all(d.available is False for d in result.devices)


# ---------------------------------------------------------------------------
# Manifest contract
# ---------------------------------------------------------------------------


class TestManifestContract:
    """The tool must be declared in the manifest exactly as registered."""

    async def test_registered_and_declared_as_tool(self, mock_ctx: AsyncMock) -> None:
        from mcp_controller.bng.resources import BNG_MANIFEST

        declared = {
            cap.name: cap.kind for cap in BNG_MANIFEST.capabilities if cap.name == TOOL_NAME
        }
        assert declared == {TOOL_NAME: "tool"}

        k8s = _make_k8s([])
        prom = _make_health_prom({})
        tool = _register(k8s, prom)
        assert tool is not None
