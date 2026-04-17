"""Shared test fixtures."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp.server import Context

from mcp_controller.core.k8s_types import NetworkDeviceTarget, NetworkHostTarget

FIXTURES_DIR = Path(__file__).parent / "fixtures"
K8S_FIXTURES = FIXTURES_DIR / "kubernetes"


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


# ---------------------------------------------------------------------------
# MCP context mock
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ctx() -> AsyncMock:
    """Mock MCP `Context` with stubbed info/warning/error logging methods."""
    ctx = AsyncMock(spec=Context)
    return ctx
