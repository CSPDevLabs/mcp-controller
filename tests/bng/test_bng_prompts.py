"""Tests for BNG MCP prompts (`bng-add-alert-rule`, `bng-get-alert-rules`)."""

from unittest.mock import AsyncMock, patch

import pytest

from mcp_controller.bng.prompts import (
    _extract_doc_links,
    _for_guidance,
    _format_prometheus_selector,
    register_bng_prompts,
)
from mcp_controller.config import Settings
from mcp_controller.core.k8s_types import Prometheus
from mcp_controller.core.kubernetes_client import KubernetesClientError
from mcp_controller.core.types import ErrorResponse

TEST_NAMESPACE = "test-ns"


def _make_mock_k8s(
    *,
    crd_schema: dict | None = None,
    prometheuses: list[Prometheus] | None = None,
) -> AsyncMock:
    """Build a mock `KubernetesClient` for prompt tests."""
    mock = AsyncMock()
    mock.get_prometheus_rule_crd_schema = AsyncMock(return_value=crd_schema or {})
    mock.list_prometheuses = AsyncMock(return_value=prometheuses or [])
    return mock


def _register_prompts(mock_k8s: AsyncMock, settings: Settings):
    from mcp.server.fastmcp import FastMCP

    patcher = patch("mcp_controller.bng.prompts.KubernetesClient", return_value=mock_k8s)
    patcher.start()
    try:
        mcp = FastMCP(name="test")
        register_bng_prompts(mcp, settings)
        return mcp._prompt_manager._prompts
    finally:
        patcher.stop()


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestExtractDocLinks:
    """Unit tests for `_extract_doc_links`."""

    def test_finds_urls_in_nested_descriptions(self) -> None:
        schema = {
            "description": "See https://prometheus.io/docs/prometheus/latest/querying/basics/",
            "properties": {
                "groups": {
                    "description": "https://github.com/prometheus-operator/prometheus-operator"
                }
            },
        }
        links = _extract_doc_links(schema)
        assert "https://prometheus.io/docs/prometheus/latest/querying/basics/" in links
        assert "https://github.com/prometheus-operator/prometheus-operator" in links

    def test_returns_empty_list_when_no_urls(self) -> None:
        assert _extract_doc_links({"description": "no links here"}) == []

    def test_deduplicates_and_sorts(self) -> None:
        schema = [
            {"description": "http://b.example.com"},
            {"description": "http://a.example.com"},
            {"description": "http://a.example.com"},
        ]
        assert _extract_doc_links(schema) == ["http://a.example.com", "http://b.example.com"]


class TestFormatPrometheusSelector:
    """Unit tests for `_format_prometheus_selector`."""

    def test_no_instance(self) -> None:
        text = _format_prometheus_selector(None)
        assert "No `Prometheus` instance found" in text

    def test_instance_with_no_rule_selector(self) -> None:
        prom = Prometheus.model_validate(
            {"metadata": {"name": "p", "namespace": "ns"}, "spec": {}}
        )
        text = _format_prometheus_selector(prom)
        assert "no `ruleSelector` set" in text
        assert "selects **no**" in text

    def test_instance_with_rule_selector_own_namespace_only(
        self, prometheuses: list[Prometheus]
    ) -> None:
        text = _format_prometheus_selector(prometheuses[0])
        assert "role=alert-rules" in text
        assert "restricted to its own namespace" in text
        assert "evaluationInterval=30s" in text
        assert "scrapeInterval=15s" in text


class TestForGuidance:
    """Unit tests for `_for_guidance`."""

    def test_no_instance(self) -> None:
        text = _for_guidance(None)
        assert "unknown" in text

    def test_reports_observed_interval(self, prometheuses: list[Prometheus]) -> None:
        text = _for_guidance(prometheuses[0])
        assert "30s" in text


# ---------------------------------------------------------------------------
# bng-add-alert-rule prompt tests
# ---------------------------------------------------------------------------


class TestBngAddAlertRulePrompt:
    """Tests for the `bng-add-alert-rule` prompt."""

    async def test_renders_schema_and_selector_facts(
        self,
        mock_ctx: AsyncMock,
        prometheus_rule_crd_schema: dict,
        prometheuses: list[Prometheus],
    ) -> None:
        mock_k8s = _make_mock_k8s(
            crd_schema=prometheus_rule_crd_schema, prometheuses=prometheuses
        )
        settings = Settings(k8s_namespace=TEST_NAMESPACE)
        prompts = _register_prompts(mock_k8s, settings)

        messages = await prompts["bng-add-alert-rule"].fn(
            objective="alert on high memory", ctx=mock_ctx
        )

        assert len(messages) == 1
        text = messages[0].content.text
        assert "alert on high memory" in text
        assert f"`{TEST_NAMESPACE}`" in text
        assert "role=alert-rules" in text
        assert "system-resource-alerts" in text  # worked example
        assert "prometheus.io/docs" in text or "github.com" in text
        assert "create_prometheus_alert_rule" in text
        mock_k8s.list_prometheuses.assert_awaited_once_with(namespace=TEST_NAMESPACE)

    async def test_raises_kubernetes_unreachable_on_client_error(
        self, mock_ctx: AsyncMock
    ) -> None:
        mock_k8s = _make_mock_k8s()
        mock_k8s.get_prometheus_rule_crd_schema = AsyncMock(
            side_effect=KubernetesClientError("connection refused")
        )
        settings = Settings(k8s_namespace=TEST_NAMESPACE)
        prompts = _register_prompts(mock_k8s, settings)

        with pytest.raises(ErrorResponse) as exc_info:
            await prompts["bng-add-alert-rule"].fn(objective="x", ctx=mock_ctx)

        assert exc_info.value.error == "kubernetes_unreachable"


# ---------------------------------------------------------------------------
# bng-get-alert-rules prompt tests
# ---------------------------------------------------------------------------


class TestBngGetAlertRulesPrompt:
    """Tests for the `bng-get-alert-rules` prompt."""

    async def test_renders_selector_facts_all_namespaces(
        self, mock_ctx: AsyncMock, prometheuses: list[Prometheus]
    ) -> None:
        mock_k8s = _make_mock_k8s(prometheuses=prometheuses)
        settings = Settings(k8s_namespace=TEST_NAMESPACE)
        prompts = _register_prompts(mock_k8s, settings)

        messages = await prompts["bng-get-alert-rules"].fn(ctx=mock_ctx)

        text = messages[0].content.text
        assert "bng://alerts/configured" in text
        assert "role=alert-rules" in text
        assert "all namespaces" in text
        mock_k8s.list_prometheuses.assert_awaited_once_with(namespace=TEST_NAMESPACE)

    async def test_scopes_to_namespace_when_given(
        self, mock_ctx: AsyncMock, prometheuses: list[Prometheus]
    ) -> None:
        mock_k8s = _make_mock_k8s(prometheuses=prometheuses)
        settings = Settings(k8s_namespace=TEST_NAMESPACE)
        prompts = _register_prompts(mock_k8s, settings)

        messages = await prompts["bng-get-alert-rules"].fn(ctx=mock_ctx, namespace="nok-bng")

        text = messages[0].content.text
        assert "bng://alerts/configured/nok-bng" in text
        assert "namespace `nok-bng`" in text

    async def test_raises_kubernetes_unreachable_on_client_error(
        self, mock_ctx: AsyncMock
    ) -> None:
        mock_k8s = _make_mock_k8s()
        mock_k8s.list_prometheuses = AsyncMock(
            side_effect=KubernetesClientError("connection refused")
        )
        settings = Settings(k8s_namespace=TEST_NAMESPACE)
        prompts = _register_prompts(mock_k8s, settings)

        with pytest.raises(ErrorResponse) as exc_info:
            await prompts["bng-get-alert-rules"].fn(ctx=mock_ctx)

        assert exc_info.value.error == "kubernetes_unreachable"
