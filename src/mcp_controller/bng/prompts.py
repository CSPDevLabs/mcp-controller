"""BNG MCP Prompt definitions."""

import json
import logging
import re

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Context
from mcp.types import PromptMessage, TextContent

from mcp_controller.bng.resources import BNG_MANIFEST
from mcp_controller.config import Settings
from mcp_controller.core.k8s_types import Prometheus
from mcp_controller.core.kubernetes_client import KubernetesClient, KubernetesClientError
from mcp_controller.core.mock import mock_intercept
from mcp_controller.core.types import ErrorResponse

logger = logging.getLogger(__name__)


# Canonical worked example, shared with the create-tool docstring, fixtures,
# and tests — see docs/feat/prometheus_rules.md#canonical-rule-example.
_CANONICAL_RULE_EXAMPLE = """\
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: system-resource-alerts
  namespace: nok-bng
  labels:
    role: alert-rules # Must match the ruleSelector above
spec:
  groups:
  - name: system-usage
    rules:
    - alert: HighCpuUsage
      expr: state_system_cpu_summary_usage_cpu_usage > 12
      for: 1m
      labels:
        severity: warning
      annotations:
        summary: "High CPU usage on {{ $labels.source }}"
        description: "CPU usage is at {{ $value }}% (threshold 12%)"

    - alert: HighMemoryUsage
      expr: (state_system_memory_pools_summary_total_in_use
        / state_system_memory_pools_summary_available_memory) * 100 > 2
      for: 1m
      labels:
        severity: warning
      annotations:
        summary: "High Memory usage on {{ $labels.source }}"
        description: "Memory utilization is at {{ $value }}% (threshold 2%)"
"""

# Curated PromQL reference, derived from docs/feat/prometheus_rules.md and the
# patterns this controller already runs in bng/tools.py.
_PROMQL_CHEAT_SHEET = """\
| Category | Function | Description | Example |
| :--- | :--- | :--- | :--- |
| Selectors | Exact match | Filter by exact label value | `up{job="x"}` |
| Selectors | Negative match | Exclude exact label value | `up{job!="x"}` |
| Selectors | Regex match | Filter by regex | `mem_bytes{pod=~"backend-.*"}` |
| Selectors | Negative regex | Exclude by regex | `requests_total{status!~"2.."}` |
| Time | Instant vector | Current value of a series | `metric_name` |
| Time | Range vector | Values over a window | `metric_name[5m]` |
| Time | Time shift | Shift eval time into the past | `metric_name offset 1h` |
| Rates | `rate()` | Per-sec avg rate (handles resets) | `rate(counter_total[5m])` |
| Rates | `irate()` | Per-sec rate, last 2 points | `irate(counter_total[5m])` |
| Rates | `increase()` | Total increase over a window | `increase(counter_total[1h])` |
| Aggregation | `sum()` | Sum over dimensions | `sum by (source) (metric_name)` |
| Aggregation | `count()` | Count active series | `count by (source) (up == 1)` |
| Aggregation | `topk()` | Top N elements by value | `topk(5, sum by (source) (m))` |
| Histograms | `histogram_quantile()` | Percentile from buckets | `histogram_quantile(.95, b[5m])` |
| Math | Error rate % | Ratio via binary operators | `rate(err[5m]) / rate(req[5m]) * 100` |
| Math | Utilisation % | Arithmetic between vectors | `allocated / total * 100` |
| Advanced | `predict_linear()` | Predict future values | `predict_linear(m[1h], 4 * 3600) < 0` |
| Advanced | `changes()` | Count value changes | `changes(process_start_time_seconds[1h])` |
| Advanced | Subqueries | Range query over a query | `quantile_over_time(0.99, m[1h:1m])` |

Patterns already used by this controller's own tools (`bng/tools.py`),
proven against these exact BNG metrics:

| Pattern | Form | Note |
| --- | --- | --- |
| Label selector | `metric{source="ns/device"}` | The `source` convention — see §3 below |
| Gauge threshold | `state_..._cpu_usage > 80` | No `rate()` on a gauge |
| Counter rate | `rate(counter[2m])` | Window ≥ 4× `scrapeInterval` |
| Utilisation | `allocated / total * 100 > 90` | Guard `total` being 0 |
| Aggregation | `sum by (source) (...)` | Keep `source` to identify the device |
| Absence | `absent_over_time(metric[10m])` | Device-down idiom used in `bng/tools.py` |
| Label rewrite | `label_replace(...)` | Normalise `source` to `namespace/name` |
| Trend | `predict_linear(m[1h], 4*3600) > total` | Capacity-exhaustion forecasting |
| Combining | `and`, `or`, `unless` | Suppress an alert when another condition holds |
| Annotations | `{{ $labels.source }}`, `{{ $value }}` | Only valid in `labels`/`annotations` |
"""

_URL_RE = re.compile(r"https?://[^\s)\]\"']+")


def _extract_doc_links(schema: object, found: set[str] | None = None) -> list[str]:
    """Harvest documentation URLs embedded in a CRD schema's `description` fields.

    The operator embeds canonical documentation links (e.g. the
    prometheus.io alerting-rules page) directly in the OpenAPI schema.
    Harvesting them keeps the prompt's links versioned with the installed
    operator instead of a vendored, driftable copy.

    Args:
        schema: A (sub-)tree of the CRD's `openAPIV3Schema`, as returned by
            `KubernetesClient.get_prometheus_rule_crd_schema()`.
        found:  Internal accumulator for the recursive walk; leave as `None`.

    Returns:
        Sorted list of distinct URLs found in any `description` string.
    """
    if found is None:
        found = set()
    if isinstance(schema, dict):
        description = schema.get("description")
        if isinstance(description, str):
            found.update(_URL_RE.findall(description))
        for value in schema.values():
            _extract_doc_links(value, found)
    elif isinstance(schema, list):
        for item in schema:
            _extract_doc_links(item, found)
    return sorted(found)


def _format_prometheus_selector(prom: Prometheus | None) -> str:
    """Render the configured namespace's `Prometheus` rule-selection facts.

    Args:
        prom: The single `Prometheus` CR from `Settings.k8s_namespace`, or
            `None` when none is present.

    Returns:
        A short markdown paragraph describing required labels, namespace
        scope, and evaluation/scrape intervals. An instance with no
        `ruleSelector` is called out as selecting nothing, matching
        prometheus-operator semantics.
    """
    if prom is None:
        return (
            "No `Prometheus` instance found in the controller's configured "
            "namespace — a rule created now will not be evaluated by anything."
        )

    namespace = prom.metadata.namespace if prom.metadata and prom.metadata.namespace else "?"
    name = prom.metadata.name if prom.metadata and prom.metadata.name else "?"
    spec = prom.spec

    if spec is None or spec.rule_selector is None or not spec.rule_selector.match_labels:
        return (
            f"`{namespace}/{name}` has no `ruleSelector` set — it selects **no** "
            "`PrometheusRule` objects at all. A rule cannot target this instance."
        )

    required_labels = ", ".join(f"`{k}={v}`" for k, v in spec.rule_selector.match_labels.items())
    if spec.rule_namespace_selector is None:
        namespace_note = (
            f"restricted to its own namespace (`{namespace}`) — the rule must live there"
        )
    else:
        namespace_note = "scoped by `ruleNamespaceSelector` (not limited to its own namespace)"
    eval_interval = spec.evaluation_interval or "unknown"
    scrape_interval = spec.scrape_interval or "unknown"
    return (
        f"`{namespace}/{name}` requires labels {required_labels}; namespace "
        f"{namespace_note}; `evaluationInterval={eval_interval}`, "
        f"`scrapeInterval={scrape_interval}`."
    )


def _for_guidance(prom: Prometheus | None) -> str:
    """Recommend a minimum `for` value from the instance's evaluation interval.

    Args:
        prom: The single `Prometheus` CR from `Settings.k8s_namespace`, or
            `None` when none is present / has no evaluation interval.

    Returns:
        A short guidance sentence naming the observed evaluation interval,
        or a safe default when none is known.
    """
    interval = (
        prom.spec.evaluation_interval
        if prom is not None
        and prom.spec is not None
        and prom.spec.rule_selector is not None
        and prom.spec.rule_selector.match_labels
        and prom.spec.evaluation_interval
        else None
    )
    if not interval:
        return "evaluation interval unknown — default to at least `1m`"
    return f"the instance evaluates every {interval} — set `for` to at least double that"


async def _prometheus_in_namespace(k8s: KubernetesClient, namespace: str) -> Prometheus | None:
    """Return the first `Prometheus` CR in `namespace`, or `None`.

    The controller is wired to one Prometheus (`Settings.k8s_namespace` /
    `Settings.prometheus_url`); cluster-wide enumeration is unnecessary.

    Args:
        k8s:       Kubernetes client.
        namespace: Namespace to list in (the controller's configured one).

    Returns:
        The first `Prometheus` found, or `None` if the namespace has none.
    """
    prometheuses = await k8s.list_prometheuses(namespace=namespace)
    if not prometheuses:
        return None
    if len(prometheuses) > 1:
        names = ", ".join(
            (p.metadata.name if p.metadata and p.metadata.name else "?") for p in prometheuses
        )
        logger.warning(
            "multiple Prometheus instances in namespace %s (%s); using the first",
            namespace,
            names,
        )
    return prometheuses[0]


def register_bng_prompts(mcp: FastMCP, settings: Settings) -> None:
    """Register BNG prompts on the MCP server.

    Args:
        mcp:      The FastMCP server instance.
        settings: Application settings.

    Returns:
        None
    """
    k8s = KubernetesClient(namespace=settings.k8s_namespace)

    @mcp.prompt(
        name="bng-overview",
        description=(
            "Return the BNG controller manifest and capability summary. "
            "Use this prompt at session start to discover what the BNG "
            "controller provides before calling any tools or resources."
        ),
    )
    async def bng_overview() -> list[PromptMessage]:
        """Provide the BNG manifest and grouped capabilities for session bootstrap."""
        logger.info("Generating bng-overview prompt")

        manifest_json = BNG_MANIFEST.model_dump_json(indent=2)

        grouped: dict[str, list[dict]] = {}
        for cap in BNG_MANIFEST.capabilities:
            grouped.setdefault(cap.kind, []).append(
                {
                    "name": cap.name,
                    "description": cap.description,
                    "tags": [str(t) for t in cap.tags],
                }
            )
        capabilities_json = json.dumps(grouped, indent=2)

        text = (
            "# BNG Controller — Session Context\n\n"
            "## Manifest\n"
            f"```json\n{manifest_json}\n```\n\n"
            "## Available Capabilities\n"
            f"```json\n{capabilities_json}\n```\n\n"
            "Use the capabilities above to decide which resources to read "
            "or tools to call. Metric tools (`bng_device_cpu_usage`, "
            "`bng_device_memory_usage`, `bng_device_unavailability_map`) "
            "accept an optional `start_time` (ISO 8601) for historical queries "
            "and return quantile statistics (p80, p95, p98) where applicable."
        )

        return [PromptMessage(role="user", content=TextContent(type="text", text=text))]

    @mcp.prompt(
        name="bng-add-alert-rule",
        description=(
            "Guided authoring workflow for a new Prometheus alerting rule — "
            "assembles the live CRD schema, ruleSelector/namespace facts, a "
            "PromQL cheat sheet, and a worked example, then tells the LLM to "
            "call `create_prometheus_alert_rule`."
        ),
    )
    @mock_intercept(settings)
    async def bng_add_alert_rule(objective: str, ctx: Context) -> list[PromptMessage]:
        """Assemble a live, cluster-aware prompt for authoring a new alert rule.

        Reads the current `PrometheusRule` CRD `spec` schema and the
        `Prometheus` instance in `Settings.k8s_namespace` from the API
        server on every render, so the guidance never drifts from what
        the cluster actually enforces. The new rule is always targeted at
        that same configured namespace.

        Args:
            objective: What the new alert should detect, in the caller's
                own words, e.g. `"message when a BNG device's memory exceeds 90%"`.
            ctx: MCP request context.

        Returns:
            A single `PromptMessage` with the full authoring guide.

        Raises:
            ErrorResponse: `kubernetes_unreachable` if the CRD schema or
                `Prometheus` instance cannot be read from the API server.
        """
        logger.info("Generating bng-add-alert-rule prompt: objective=%r", objective)
        await ctx.info("Assembling alert-rule authoring guide from live cluster state")

        try:
            schema = await k8s.get_prometheus_rule_crd_schema()
            prometheus = await _prometheus_in_namespace(k8s, settings.k8s_namespace)
        except KubernetesClientError as exc:
            logger.error("bng-add-alert-rule: %s", exc)
            await ctx.error(f"bng-add-alert-rule: {exc}")
            raise ErrorResponse(error="kubernetes_unreachable", detail=str(exc)) from exc

        doc_links = _extract_doc_links(schema)
        doc_links_block = (
            "\n".join(f"- {url}" for url in doc_links)
            if doc_links
            else "(no documentation links found in the current CRD schema)"
        )

        sections = [
            "# BNG Controller — Add Alert Rule",
            f"## Objective\n{objective}",
            f"## Target namespace\n`{settings.k8s_namespace}` "
            "(the controller's configured namespace — set this on "
            "`metadata.namespace`, or omit it and `create_prometheus_alert_rule` "
            "defaults to the same value)",
            "## 1. PrometheusRule `spec` schema (live, from the cluster)\n"
            "The following is the current `openAPIV3Schema.properties.spec` of the "
            "`prometheusrules.monitoring.coreos.com` CRD, read from the API server "
            "on every render. It is the source of truth for field names and types "
            "— do not rely on memory.\n\n"
            f"```json\n{json.dumps(schema, indent=2)}\n```",
            "## 2. Which Prometheus instance will evaluate this rule\n"
            f"The controller targets the `Prometheus` in `{settings.k8s_namespace}`:\n\n"
            + _format_prometheus_selector(prometheus)
            + "\n\n**Important**: a rule created without "
            "the required labels, or outside an allowed namespace, is accepted by "
            "the API server and then silently never evaluated.",
            "## 3. Metrics\n"
            "Read `bng://metrics/available` before writing an `expr` — never invent "
            "a metric name; use only names it returns. Device metrics use the "
            '`source="<namespace>/<device>"` label convention — discover valid '
            f"sources by reading `bng://targets/devices/{settings.k8s_namespace}` "
            "(or `bng://targets/devices` for all namespaces); this label is not "
            "derivable from the metric name list.\n\n"
            "To see how existing rules express these metrics, read "
            "`bng://alerts/configured` — it shows the PromQL already deployed.",
            "## 4. Further documentation\n"
            f"Links found in the current CRD schema:\n{doc_links_block}\n\n"
            "For anything not covered above (PromQL function reference, alerting "
            "best practices, the `prometheus-operator` API reference), fetch it "
            "from the web if your client supports it — start from "
            "https://prometheus.io/docs/prometheus/latest/querying/basics/ and "
            "https://github.com/prometheus-operator/prometheus-operator. Clients "
            "without web access only have the schema, cheat sheet, and worked "
            "example in this prompt.",
            f"## 5. PromQL cheat sheet\n{_PROMQL_CHEAT_SHEET}\n",
            "## 6. Authoring rules\n"
            "- Set `alert`, never `record` — this tool only accepts alerting rules.\n"
            f"- Set `for` to at least twice the evaluation interval "
            f"({_for_guidance(prometheus)}).\n"
            "- Give every rule a `severity` label (`warning` or `critical`) and "
            "`summary` / `description` annotations so operators know what fired "
            "and why. In those annotations you can insert live values with Go "
            "templates — for example `{{ $labels.source }}` for the device, "
            "`{{ $value }}` for the threshold breach, or "
            '`{{ printf "%.1f" $value }}` for a rounded number.\n'
            "- Apply labels exactly as required by the selector above — this tool "
            "does not add missing labels; a mismatch is reported back as a "
            "warning instead.",
            f"## 7. Worked example\n```yaml\n{_CANONICAL_RULE_EXAMPLE}```",
            "## 8. Next step\n"
            "Once the manifest is ready, call `create_prometheus_alert_rule` with "
            "it as a single YAML string.",
        ]

        text = "\n\n".join(sections)
        return [PromptMessage(role="user", content=TextContent(type="text", text=text))]

    @mcp.prompt(
        name="bng-get-alert-rules",
        description=(
            "Review workflow — points the LLM at configured PrometheusRule "
            "objects and explains how to tell active rules from ones that "
            "are deployed but never evaluated."
        ),
    )
    @mock_intercept(settings)
    async def bng_get_alert_rules(ctx: Context, namespace: str = "") -> list[PromptMessage]:
        """Point the LLM at configured alert rules and how to judge if they're live.

        Args:
            ctx: MCP request context.
            namespace: When set, review only this namespace's rules via
                `bng://alerts/configured/{namespace}`; otherwise review all
                namespaces via `bng://alerts/configured`.

        Returns:
            A single `PromptMessage` with the review instructions.

        Raises:
            ErrorResponse: `kubernetes_unreachable` if the `Prometheus`
                instance in `Settings.k8s_namespace` cannot be read.
        """
        logger.info("Generating bng-get-alert-rules prompt: namespace=%r", namespace or "*")
        await ctx.info("Assembling alert-rule review guide")

        try:
            prometheus = await _prometheus_in_namespace(k8s, settings.k8s_namespace)
        except KubernetesClientError as exc:
            logger.error("bng-get-alert-rules: %s", exc)
            await ctx.error(f"bng-get-alert-rules: {exc}")
            raise ErrorResponse(error="kubernetes_unreachable", detail=str(exc)) from exc

        uri = f"bng://alerts/configured/{namespace}" if namespace else "bng://alerts/configured"
        scope = f"namespace `{namespace}`" if namespace else "all namespaces"

        text = (
            "# BNG Controller — Review Alert Rules\n\n"
            f"Read `{uri}` to list the `PrometheusRule` objects deployed in "
            f"{scope}.\n\n"
            f"## How the Prometheus in `{settings.k8s_namespace}` selects rules\n"
            f"{_format_prometheus_selector(prometheus)}\n\n"
            "## What to report, per rule\n"
            "- Whether it is an alerting rule (`alert`) or a recording rule "
            "(`record`) — this controller only creates alerting rules, but the "
            "resource reports whatever is actually deployed.\n"
            "- Its labels, and whether they satisfy the instance's "
            "`ruleSelector` above — a rule whose labels don't match is deployed "
            "but **never evaluated**.\n"
            "- Whether its namespace is one the matching instance's "
            "`ruleNamespaceSelector` actually covers.\n\n"
            "Group the answer into rules that are actively evaluated versus "
            "rules that are present but inert (label or namespace mismatch)."
        )
        return [PromptMessage(role="user", content=TextContent(type="text", text=text))]
