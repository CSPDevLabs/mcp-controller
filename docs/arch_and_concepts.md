# MCP Controller Design Plan — Nokia BNG Observability

## Context

The mcp-controller project aims to provide an intelligent MCP server that exposes Nokia BNG state and availability metrics with engineering SME context. Metrics are collected via gNMIC into Prometheus, state data is streamed via gNMI (gnmic-state job), and logs flow to Loki. The aim is to address it with AI-facing interface.

The design must also support future controllers for other network functions (SeGW, SR-MPLS, SRv6, Traffic Engineering, Network Performance) with a composable dependency model.

---

## 1. MCP Exposure Pattern: Resources + Resource Templates + Tools + Prompts (Invenstigations Workflows)

**Recommended: Option C** — three-tier exposure.

| Layer | Purpose | Example |
|-------|---------|---------|
| **Resources** | Orientation & discovery — what this controller is, what it monitors | `bng://manifest`, `bng://targets`, `bng://health/summary` |
| **Resource Templates** (parameterized) | Data access — fetch metrics/logs/state by device and time range | `bng://metrics/cpu/{source}`, `bng://logs/syslog/{source}`, `bng://state/srrp/{source}` |
| **Tools** | Intelligence — analysis, correlation, capacity planning | `assess_bng_health`, `analyze_srrp_stability`, `forecast_capacity`, `correlate_events`, `compare_bng_pair` |
| **Prompts** (investigation workflows) | Structured troubleshooting workflows | `investigate_bng_issue`, `check_redundancy`, `capacity_review`, `subscriber_diagnostics` |

---

## 2. Static Resources (7)

| URI | Description | Implemented |
|-----|-------------|:-----------:|
| `bng://manifest` | Controller manifest: capabilities, version, dependencies | Yes |
| `bng://targets` | All monitored BNG targets (devices and hosts) — name, address, hostname, kind, namespace | Yes |
| `bng://targets/devices` | All `NetworkDeviceTarget` CRs with full spec and status | Yes |
| `bng://targets/hosts` | All `NetworkHostTarget` CRs with full spec and status | Yes |
| `bng://health/summary` | Real-time overall BNG health (aggregated from all metrics) | No |
| `bng://sme/threshold_policies` | Threshold policies (configurable) for BNG health | No |
| `bng://sme/correlation_rules` | Correlation rules (configurable) for BNG health | No |
| `bng://sme/alerts` | Active alerts (configurable) for BNG health, ideally it must configure necessary alerts in Prometheus Alertmanager | No |
| `bng://sme/recommended_actions` | Recommended actions (configurable) for BNG health, not sure about this one | No |

## 3. Resource Templates (10)

| URI Template | Description | Implemented |
|---|---|:-----------:|
| `bng://targets/devices/{namespace}` | `NetworkDeviceTarget` CRs filtered by Kubernetes namespace | Yes |
| `bng://targets/hosts/{namespace}` | `NetworkHostTarget` CRs filtered by Kubernetes namespace | Yes |
| `bng://metrics/cpu/{source}` | CPU usage (sample_period=60) | No |
| `bng://metrics/memory/{source}` | Memory utilization (in-use vs available) | No |
| `bng://metrics/subscribers/{source}` | PPP sessions, IPv4/IPv6 host counts (current + peak) | No |
| `bng://metrics/srrp/{source}` | SRRP stats: adv errors, discards, master/non-master transitions | No |
| `bng://metrics/resources/{source}` | FP resources: policers, queues, SAP instances, next-hop entries (as %) | No |
| `bng://metrics/traffic/{source}` | Port in/out octets and errors (rate-converted) | No |
| `bng://logs/syslog/{source}` | Recent syslog events from Loki | No |
| `bng://state/srrp/{source}` | SRRP oper-state changes from gNMI (gnmic-state job) | No |

## 4. Tools (8)

| Tool | Input | What it does | Implemented |
|------|-------|--------------|:-----------:|
| `bng_targets_by_label` | `{label_selector: str}` | Return BNG targets filtered by a K8s label selector, e.g. `role=bng` or `env=prod,role=bng` | Yes |
| `read_bng_manifest` | — | Return the full BNG controller manifest (identity, capabilities, dependencies, data sources). Entry point for LLM discovery | Yes |
| `discover_bng_capabilities` | — | Discover all BNG capabilities grouped by kind (`resource`, `resource_template`, `tool`, `prompt`) with descriptions and tags | Yes |
| `read_bng_resource` | `{uri: str}` | Read any registered BNG resource by its `bng://` URI. Delegates to the server's resource registry — new resources are accessible automatically | Yes |
| `assess_bng_health` | `{source?: str}` | Full health assessment across all metric categories. Returns per-category green/yellow/red status, active alerts, recommended actions | No |
| `analyze_srrp_stability` | `{source: str, range?: str}` | SRRP redundancy analysis: flap frequency, adv error trends, correlation with CPU/memory. Returns stability score + failover risk | No |
| `forecast_capacity` | `{source: str, category: str}` | Capacity trend for policers/queues/SAP/next-hops. Linear regression to project exhaustion timeline | No |
| `correlate_events` | `{source: str, range?: str}` | Cross-correlates syslog, SRRP state changes, CPU spikes, subscriber drops within a time window | No |
| `compare_bng_pair` | `{source_a: str, source_b: str}` | Compares bng1 vs bng2 across all metrics. Highlights asymmetries (load skew, resource skew, SRRP role mismatch) | No |

## 5. Multi-Controller Data Model

### Architecture: Each module = a separate MCP controller (MCP server)

Each network function (BNG, SR-MPLS, SeGW, etc.) runs as its **own MCP server**. The MCP client  connects to multiple servers simultaneously.
Dependencies between controllers are declared at the **capability level** — referencing specific resources, tools, or prompts in other controllers. For example, BNG may depend on SR-MPLS for LSP state information, or SeGW for security context.

### Controller ID Convention

Controller identifiers follow a **reverse-DNS, Kubernetes-style** naming scheme:

```
<nf>.mcp.controllers.nok.dev
```

All valid IDs are registered as a `ControllerId` `StrEnum` in `core/controllers.py`. Using this enum as the field type in Pydantic models gives automatic validation — an unknown ID is rejected at model construction time, no custom validator required.

```python
# core/controllers.py
from enum import StrEnum

class ControllerId(StrEnum):
    BNG = "bng.mcp.controllers.nok.dev"
    # SR_MPLS = "sr-mpls.mcp.controllers.nok.dev"  # add when implemented
```

### Tag Enumerations

Tags are defined as `StrEnum`s in `core/tags.py` and split by scope:

```python
# core/tags.py
from enum import StrEnum

class CapabilityTag(StrEnum):
    """Tags for individual MCP capabilities (resources, tools, prompts)."""
    MANIFEST = "manifest"
    DISCOVERY = "discovery"
    TARGETS = "targets"
    HEALTH = "health"
    STATUS = "status"
    METRICS = "metrics"
    LOGS = "logs"

class ControllerTag(StrEnum):
    """Tags for classifying MCP controllers by NF domain."""
    BROADBAND = "broadband"
    SUBSCRIBER_MGMT = "subscriber-mgmt"
    SROS = "sros"
    NOKIA = "nokia"
    BNG = "bng"
    KUBERNETES = "kubernetes"
```

### Data Source Enumeration

Backend data sources are typed via `DataSource` `StrEnum` in `core/registry.py`:

```python
class DataSource(StrEnum):
    PROMETHEUS = "prometheus"
    LOKI = "loki"
    GNMI = "gnmi"
    NETCONF = "netconf"
    KUBERNETES = "kubernetes"
```

### Controller Manifest — Pydantic models

All string fields that represent a controlled vocabulary are now strongly typed enums. Pydantic validates membership automatically; values serialize as plain strings in JSON output.

```python
from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, Field
from mcp_controller.core.controllers import ControllerId
from mcp_controller.core.tags import CapabilityTag, ControllerTag

class ControllerCapability(BaseModel):
    """A capability this controller exposes."""
    name: str                          # "assess_bng_health" or "bng://metrics/cpu/{source}"
    kind: Literal["resource", "resource_template", "tool", "prompt"]
    description: str
    tags: list[CapabilityTag] = Field(default_factory=list)

class CapabilityRef(BaseModel):
    """Reference to a specific capability in another MCP controller."""
    kind: Literal["resource", "resource_template", "tool", "prompt"]
    name: str                          # URI for resources, name for tools/prompts

class ControllerDependency(BaseModel):
    """Dependency on another MCP controller, with fine-grained capability refs."""
    controller_id: ControllerId        # validated against ControllerId registry
    min_version: str
    required: bool = False             # hard (fail if missing) vs soft (degrade gracefully)
    reason: str                        # "BNG uplinks use MPLS transport"
    capability_refs: list[CapabilityRef] = Field(default_factory=list)

class ControllerManifest(BaseModel):
    controller_id: ControllerId        # e.g. ControllerId.BNG → "bng.mcp.controllers.nok.dev"
    version: str                       # semver
    display_name: str                  # "Nokia BNG Observability"
    network_function: str              # canonical NF name
    description: str
    capabilities: list[ControllerCapability] = Field(default_factory=list)
    dependencies: list[ControllerDependency] = Field(default_factory=list)
    data_sources: list[DataSource] = Field(default_factory=list)
    tags: list[ControllerTag] = Field(default_factory=list)
```

### Example: BNG manifest definition

```python
from mcp_controller.core.controllers import ControllerId
from mcp_controller.core.registry import ControllerCapability, ControllerManifest, DataSource
from mcp_controller.core.tags import CapabilityTag, ControllerTag

BNG_MANIFEST = ControllerManifest(
    controller_id=ControllerId.BNG,
    version="0.1.0",
    display_name="Nokia BNG Observability MCP Controller",
    network_function="bng",
    description="Intelligent MCP controller for Nokia SROS BNG state and availability.",
    capabilities=[
        ControllerCapability(
            name="bng://manifest",
            kind="resource",
            description="Controller manifest with capabilities and dependencies",
            tags=[CapabilityTag.MANIFEST, CapabilityTag.DISCOVERY],
        ),
        ControllerCapability(
            name="bng://health/summary",
            kind="resource",
            description="Real-time overall BNG health status",
            tags=[CapabilityTag.HEALTH, CapabilityTag.STATUS],
        ),
    ],
    data_sources=[DataSource.PROMETHEUS, DataSource.LOKI, DataSource.KUBERNETES],
    tags=[ControllerTag.BROADBAND, ControllerTag.SUBSCRIBER_MGMT, ControllerTag.SROS, ControllerTag.NOKIA, ControllerTag.BNG],
)
```

### Example: BNG declares dependency on SR-MPLS

```python
ControllerDependency(
    controller_id=ControllerId.SR_MPLS,   # "sr-mpls.mcp.controllers.nok.dev"
    min_version="0.1.0",
    required=False,
    reason="BNG uplinks use MPLS transport; LSP state affects subscriber reachability",
    capability_refs=[
        CapabilityRef(kind="resource_template", name="sr-mpls://metrics/lsp/{source}"),
        CapabilityRef(kind="tool", name="get_lsp_status"),
    ],
)
```

This enables the LLM to understand: "When investigating BNG connectivity issues, I should also check sr-mpls://metrics/lsp/{source} and call get_lsp_status on the SR-MPLS controller."

### Transport: HTTP (Streamable HTTP)

This controller uses **HTTP transport** (Streamable HTTP). The MCP server exposes an HTTP endpoint that MCP clients connect to.

### Controller Registry (`controller_registry.yaml`)

Describes how separate MCP controllers (each its own project/deployment) relate to each other:

```yaml
controllers:
  - id: bng
    version: "0.1.0"
    url: "http://bng-mcp-controller:8000/mcp"

  - id: sr-mpls
    version: "0.1.0"
    url: "http://sr-mpls-mcp-controller:8000/mcp"

  # Each is a separate project/deployment, connected via HTTP
```

### How cross-controller interaction works <- To be reviewed

1. **LLM-orchestrated** (primary): The LLM reads `bng://manifest`, sees dependencies with `capability_refs`, and knows to query the SR-MPLS controller's resources/tools when investigating cross-domain issues. The LLM is the orchestrator across multiple MCP servers.

2. **Programmatic** (future): A controller can embed an MCP client to call another controller's HTTP endpoint directly within a tool execution. For example,`assess_bng_health` fetches `sr-mpls://metrics/lsp/{source}` from the SR-MPLS controller if available.

Each controller exposes its manifest as a resource (`{id}://manifest`), enabling the LLM to traverse the full dependency graph across all connected controllers.

## 6. Prompts (Investigation Workflows) <- To be reviewed

MCP Prompts guide the LLM through structured diagnostic workflows using resources and tools.

| Prompt | Arguments | What it provides |
| ------ | --------- | ---------------- |
| `investigate_bng_issue` | `source: str, symptom: str` | Structured troubleshooting workflow: "You are a Nokia BNG SME. Investigate {symptom} on {source}. Start by reading `bng://health/summary`, then check CPU/memory, SRRP state, syslog events. Use `correlate_events` to find root cause." |
| `check_redundancy` | `source: str` | SRRP redundancy diagnostic: "Analyze SRRP state for {source}. Read `bng://metrics/srrp/{source}` and `bng://logs/srrp-events/{source}`. Check for flapping, adv errors, asymmetric roles. Use `analyze_srrp_stability` and `compare_bng_pair`." |
| `capacity_review` | `source: str` | Resource capacity assessment: "Review capacity for {source}. Check FP policers, queues, SAP instances, next-hop entries via `bng://metrics/resources/{source}`. Flag anything >70%. Use `forecast_capacity` for trend projections." |
| `subscriber_diagnostics` | `source: str` | Subscriber health check: "Examine subscriber state on {source}. Read `bng://metrics/subscribers/{source}` for session counts and host allocation. Cross-reference with CPU, memory, and FP resource pressure." |

Prompts live in `bng/prompts.py` and are registered on the MCP server alongside resources and tools.

---

## 7. BNG SME Intelligence (`sme.py`)

### Threshold Policies

| Metric | Green | Yellow | Red | Context |
|--------|-------|--------|-----|---------|
| CPU usage | 0-60% | 60-80% | 80-100% | >80% can delay SRRP adverts and subscriber auth |
| Memory | 0-70% | 70-85% | 85-100% | Exhaustion blocks session establishment |
| SRRP flap rate | 0-1/hr | 1-5/hr | >5/hr | Flapping causes subscriber micro-interruptions |
| FP policer util | 0-70% | 70-90% | 90-100% | Exhaustion prevents new subscriber QoS instantiation |
| Next-hop util | 0-70% | 70-85% | 85-100% | Exhaustion blocks new subscriber route installation |

### Correlation Rules

- **Failover risk**: high CPU + SRRP adv errors -> Means of potential unplanned failover?
- **Capacity saturation**: FP resources approaching limits + subscriber growth -> need rebalancing.

## 8. Mock Mode (Record/Replay)

The server supports a **record/replay** mock system so that others can run the MCP
server and exercise all tools and resources without a live Kubernetes, Prometheus,
or Loki backend.

### How it works

The `mock_intercept(settings)` decorator in `src/mcp_controller/core/mock.py` wraps
every resource, tool, and completion handler.  In normal mode the decorator is a
**no-op** — the unwrapped function is returned directly with zero runtime overhead.

| Mode | Flag / env var | Behaviour |
|------|----------------|-----------|
| Normal | — | Decorator is a no-op; real backends are called |
| Record | `--mock-data-record` / `MCP_MOCK_DATA_RECORD=true` | Calls real backend, saves args + result to `tests/mocks/data/<handler>.json` |
| Replay | `--mock` / `MCP_MOCK=true` | Returns pre-recorded response; handler body never executes, no backend connections |

### Data format

`tests/mocks/data/<function_name>.json` — a JSON array; each element is one
recorded call:

```json
[
  {
    "args": {
      "device": "nok-bng/clab-sros-bngt-bng1",
      "interval": "1h",
      "step": "60s",
      "start_time": ""
    },
    "response_type": "DeviceCpuUsageResult",
    "mock_data": { "device": "nok-bng/clab-sros-bngt-bng1", "avg_cpu_usage": 14.2 }
  }
]
```

`args` acts as the lookup key (excludes `ctx` / `context`).  Same args on a
subsequent record call overwrites the existing entry (upsert).

### Workflow

```bash
# 1. Record: run against live lab, exercise tools via MCP Inspector or Claude Desktop
./uv-run.sh --mock-data-record --config lab.yaml

# 2. Replay: run without any backends
./uv-run.sh  --mock

# Env var alternative
MCP_MOCK=true python -m mcp_controller
MCP_MOCK_DATA_RECORD=true python -m mcp_controller --config lab.yaml
```

### Tests

- `tests/core/test_mock.py` — unit tests for the decorator internals
  (`_extract_args`, `_mock_save`, `_mock_lookup`, and `mock_intercept` in all modes).
- `tests/test_mock_mode.py` — integration tests verifying end-to-end record/replay
  behaviour for resource and tool handlers.

## 9. Verification

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Start HTTP server
./uv-run.sh

Note URL and open your browser.

```bash
❯ npx --yes @modelcontextprotocol/inspector@latest
Starting MCP inspector...
⚙️ Proxy server listening on localhost:6277
🔑 Session token: 4ff2026a147228e37cf05c3e1bfacfe311c6e1124a52eb3b1d432fba79b9704a
   Use this token to authenticate requests or set DANGEROUSLY_OMIT_AUTH=true to disable auth

🚀 MCP Inspector is up and running at:
   http://localhost:6274/?MCP_PROXY_AUTH_TOKEN=4ff2026a147228e37cf05c3e1bfacfe311c6e1124a52eb3b1d432fba79b9704a

🌐 Opening browser...
New StreamableHttp connection request
Query parameters: {"url":"http://localhost:8088/mcp","transportType":"streamable-http"}
Created StreamableHttp client transport
Client <-> Proxy  sessionId: 46979d02-c1f0-437e-b226-b7fb239d4668
Proxy  <-> Server sessionId: d13bd5b939af478a82852e294b5a659f
Received POST message for sessionId 46979d02-c1f0-437e-b226-b7fb239d4668
Received GET message for sessionId 46979d02-c1f0-437e-b226-b7fb239d4668
```

## 9. MCP Inspector

The MCP Inspector is the official tool for testing MCP servers. It provides a web UI to browse resources, call tools, test prompts, and inspect protocol messages.

```bash
npx --yes @modelcontextprotocol/inspector@latest
```

## 10. LLM Client Bootstrap Flow

When an MCP client connects to this controller, the LLM obtains controller knowledge through a specific sequence. Understanding this flow is important for writing effective tool descriptions and `instructions` text — resources alone are not visible to the LLM, so the manifest must be reachable via a tool or prompt.

### The 5 steps

1. **Client → Server `initialize`** — the client opens the transport and sends the `initialize` request. The server replies with `InitializeResult` containing the protocol version, server capabilities, and the `instructions` field set in `create_server()`. See the [`InitializeResult` schema](https://modelcontextprotocol.io/specification/draft/schema#initializeresult).

2. **Client prepends `instructions` to the LLM system prompt** — per MCP guidance, clients *should* surface the `instructions` string to the LLM as part of its system prompt. The exact mechanism is implementation-defined and not every client honors it, so the text must be written as a concise bootstrap hint, not a contract. See [Server Instructions: Giving LLMs a user manual for your server](https://blog.modelcontextprotocol.io/posts/2025-11-03-using-server-instructions/).

3. **Client calls `tools/list`** — the full set of registered tools is returned with names, descriptions, and JSON input schemas. The LLM sees these alongside the system prompt and can invoke any of them. Resources are **not** directly visible to the LLM at this stage; only tools are.

4. **LLM calls `read_bng_manifest`** (or the `bng-overview` prompt) — following the hint in `instructions`, the LLM issues its first tool call. The response is the full `ControllerManifest` JSON: capabilities, dependencies, data sources, and tags.

5. **LLM now has complete controller knowledge** — armed with the manifest, the LLM knows which resources exist (including those not directly callable), which tools are available, and where SME context lives. Subsequent calls become informed decisions rather than guesses.

### Why both a tool and a prompt

- **`read_bng_manifest` (tool)** — universally callable. Every MCP client supports tools, and the LLM can invoke it directly without user interaction. This is the reliable bootstrap path.
- **`bng-overview` (prompt)** — surfaced by prompt-aware clients (e.g. Claude Desktop slash commands) for explicit user-initiated bootstrap. Complements the tool path.

Both are declared in `BNG_MANIFEST` so they appear in capability discovery.

---

## 11. Future Considerations

- MCP client may be able to generate appropriate UI for the user based on the received LLM reply / requested tools to use.
- UI elements are driven by pydamtic models and schemas.
