"""Shared test fixtures."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp.server import Context

from mcp_controller.core.k8s_types import (
    NetworkDeviceTarget,
    NetworkHostTarget,
    Prometheus,
    PrometheusRule,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
K8S_FIXTURES = FIXTURES_DIR / "kubernetes"
PROMETHEUS_FIXTURES = FIXTURES_DIR / "prometheus"


# ---------------------------------------------------------------------------
# Raw JSON fixture data
# ---------------------------------------------------------------------------


@pytest.fixture
def raw_device_targets() -> list[dict]:
    """Raw JSON dicts for `NetworkDeviceTarget` items."""
    return json.loads((K8S_FIXTURES / "network_device_targets.json").read_text())


@pytest.fixture
def raw_host_targets() -> list[dict]:
    """Raw JSON dicts for `NetworkHostTarget` items."""
    return json.loads((K8S_FIXTURES / "network_host_targets.json").read_text())


@pytest.fixture
def raw_prometheus_rules() -> list[dict]:
    """Raw JSON dicts for `PrometheusRule` items.

    Contains the canonical `system-resource-alerts` example (no Flux
    labels) and a Flux-managed `network-baseline-alerts` example, both
    in the `nok-bng` namespace with the `role: alert-rules` label.
    """
    return json.loads((K8S_FIXTURES / "prometheus_rules.json").read_text())


@pytest.fixture
def raw_prometheuses() -> list[dict]:
    """Raw JSON dicts for `Prometheus` items.

    A single instance whose `ruleSelector` requires `role: alert-rules`
    and has no `ruleNamespaceSelector` (own-namespace-only selection).
    """
    return json.loads((K8S_FIXTURES / "prometheuses.json").read_text())


@pytest.fixture
def prometheus_rule_crd_schema() -> dict:
    """Raw `spec` OpenAPI schema snippet for the `PrometheusRule` CRD.

    Mirrors what `KubernetesClient.get_prometheus_rule_crd_schema()`
    returns — includes `description` fields with embedded documentation
    URLs, so `_extract_doc_links` has something to harvest.
    """
    return json.loads((K8S_FIXTURES / "prometheusrule_crd_schema.json").read_text())


@pytest.fixture
def raw_metric_names_response() -> dict:
    """Raw Prometheus `/api/v1/label/__name__/values` response body."""
    return json.loads((PROMETHEUS_FIXTURES / "metric_names.json").read_text())


# ---------------------------------------------------------------------------
# Validated Pydantic model fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def device_targets(raw_device_targets: list[dict]) -> list[NetworkDeviceTarget]:
    """Validated `NetworkDeviceTarget` models parsed from fixture data."""
    return [NetworkDeviceTarget.model_validate(item) for item in raw_device_targets]


@pytest.fixture
def host_targets(raw_host_targets: list[dict]) -> list[NetworkHostTarget]:
    """Validated `NetworkHostTarget` models parsed from fixture data."""
    return [NetworkHostTarget.model_validate(item) for item in raw_host_targets]


@pytest.fixture
def prometheus_rules(raw_prometheus_rules: list[dict]) -> list[PrometheusRule]:
    """Validated `PrometheusRule` models parsed from fixture data."""
    return [PrometheusRule.model_validate(item) for item in raw_prometheus_rules]


@pytest.fixture
def prometheuses(raw_prometheuses: list[dict]) -> list[Prometheus]:
    """Validated `Prometheus` models parsed from fixture data."""
    return [Prometheus.model_validate(item) for item in raw_prometheuses]


# ---------------------------------------------------------------------------
# MCP context mock
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ctx() -> AsyncMock:
    """Mock MCP `Context` with stubbed info/warning/error logging methods."""
    ctx = AsyncMock(spec=Context)
    return ctx
