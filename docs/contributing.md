# Contributing — Adding an NF Controller

This guide covers the conventions and patterns for adding a new network function
(NF) controller to the mcp-controller project.  Read
[arch_and_concepts.md](arch_and_concepts.md) for the overall design first.

---

## Project layout

```
src/mcp_controller/
    core/                   # Shared framework — every controller depends on this
        types.py            # MetricResult, LogResult, MCPErrorResponse, ...
        controllers.py      # ControllerId StrEnum
        tags.py             # CapabilityTag, ControllerTag StrEnums
        registry.py         # ControllerManifest, ControllerCapability, DataSource
        kubernetes_client.py
        prometheus_client.py
        loki_client.py
        k8s_types.py        # Generated CRD Pydantic models
    bng/                    # Reference NF controller
        types.py            # ErrorResponse, TargetSummary, DeviceUnavailabilityResult
        common.py           # Shared helpers (_handle_k8s_error, _target_summary, ...)
        resources.py        # BNG_MANIFEST + register_bng_resources()
        tools.py            # register_bng_tools()
    <nf>/                   # Your new controller follows the same structure
        types.py
        common.py
        resources.py
        tools.py
    config.py               # Settings (pydantic-settings)
    server.py               # create_server() — wires all controllers
```

---

## Step-by-step checklist

### 1. Register a controller ID

Add an entry to the `ControllerId` StrEnum in `core/controllers.py`:

```python
class ControllerId(StrEnum):
    BNG = "bng.mcp.controllers.nok.dev"
    SR_MPLS = "sr-mpls.mcp.controllers.nok.dev"   # <-- new
```

Convention: `<nf>.mcp.controllers.nok.dev` (reverse-DNS, Kubernetes-style).

### 2. Add tags (if needed)

If your controller introduces new capability or controller tags, add them to
the StrEnums in `core/tags.py`.  Prefer reusing existing tags when possible.

### 3. Create the NF package

```
src/mcp_controller/<nf>/
    __init__.py             # """Nokia <NF> MCP controller module."""
    types.py                # Response models (see "Type system" below)
    common.py               # Shared helpers
    resources.py            # Manifest + register_<nf>_resources()
    tools.py                # register_<nf>_tools()
```

### 4. Define the manifest

In `<nf>/resources.py`, declare a `<NF>_MANIFEST` using `ControllerManifest`.
Every resource, resource template, tool, and prompt the controller exposes
**must** have a matching `ControllerCapability` entry.  The contract test
(step 8) enforces this.

### 5. Implement resources, tools, and prompts

Follow the registration pattern — each file exports a single registration
function that accepts `(mcp: FastMCP, settings: Settings)`:

```python
def register_<nf>_resources(mcp: FastMCP, settings: Settings) -> None: ...
def register_<nf>_tools(mcp: FastMCP, settings: Settings) -> None: ...
```

### 6. Wire into the server

In `server.py`, import and call your registration functions inside
`create_server()`:

```python
from mcp_controller.<nf>.resources import register_<nf>_resources
from mcp_controller.<nf>.tools import register_<nf>_tools

def create_server(settings: Settings) -> FastMCP:
    mcp = FastMCP(...)
    # existing controllers
    register_bng_resources(mcp, settings)
    register_bng_tools(mcp, settings)
    # new controller
    register_<nf>_resources(mcp, settings)
    register_<nf>_tools(mcp, settings)
    return mcp
```

### 7. Add tests

```
tests/<nf>/
    __init__.py
    test_<nf>_resources.py
    test_<nf>_tools.py
```

### 8. Add a contract test

Create `tests/test_<nf>_contract.py` that asserts the capabilities declared in
the manifest exactly match the resources/tools/prompts registered on the MCP
server.  See the Testing section in [CLAUDE.md](../CLAUDE.md) for details.

---

## Naming conventions

### MCP tool names

Prefix every tool with the NF short name in snake_case:

```
bng_targets_by_label        # good — clear domain ownership
bng_device_unavailability_map
assess_bng_health

targets_by_label            # bad  — ambiguous when multiple controllers coexist
device_unavailability_map
```

This follows MCP ecosystem consensus
([github/github-mcp-server#333](https://github.com/github/github-mcp-server/issues/333),
[docker/mcp-gateway#186](https://github.com/docker/mcp-gateway/issues/186),
[SEP-986](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/986)):
prefixed tool names group related functions, prevent collisions, and help the
LLM with instructions like "use `bng_*` tools for BNG operations."

Keep the full tool name under **60 characters** (Cursor limit; Claude allows 64).

### Resource URIs

Use a custom `<nf>://` scheme:

```
bng://manifest
bng://targets
bng://metrics/cpu/{source}
```

### Pydantic response models (internal types)

**Do not prefix** type names with the NF name.  The Python module path is the
namespace:

```python
# good — module path provides the scope
from mcp_controller.bng.types import TargetSummary
from mcp_controller.sr_mpls.types import TargetSummary

# bad — redundant with module path
from mcp_controller.bng.types import BNGTargetSummary
```

`ErrorResponse` is shared across all NF controllers and lives in
`core/types.py`:

```python
from mcp_controller.core.types import ErrorResponse
```

The `MCPErrorResponse` base (also in `core/types.py`) uses the `MCP` prefix
to signal it's the framework-level base class that `ErrorResponse` extends.

---

## Type system

### Error responses

Every tool and resource handler returns structured JSON on failure.
The hierarchy:

```text
core/types.py   MCPErrorResponse   (base: error + detail)
core/types.py   ErrorResponse      (shared subclass: adds uri, device, interval)
```

`MCPErrorResponse` provides the two mandatory fields:

| Field    | Type   | Purpose                                        |
|----------|--------|------------------------------------------------|
| `error`  | `str`  | Machine-readable category, e.g. `"not_found"`  |
| `detail` | `str`  | Human-readable description                     |

`ErrorResponse` extends `MCPErrorResponse` with optional context fields
common to NF controllers — `uri`, `device`, and `interval`:

```python
# core/types.py
class ErrorResponse(MCPErrorResponse):
    uri: str | None = None
    device: str | None = None
    interval: str | None = None
```

`MCPErrorResponse` inherits from both `BaseModel` and `Exception`.  In tool
handlers, **raise** `ErrorResponse` instead of returning it.  FastMCP's
exception chain (`Tool.run` → `ToolError` → low-level handler) automatically
produces a `CallToolResult(isError=True)` with the JSON payload preserved in
the text content:

```python
from mcp_controller.core.types import ErrorResponse

raise ErrorResponse(
    error="not_found", detail="...", uri=uri,
)
```

The wire format includes a FastMCP-added prefix that gives the LLM context
about which tool failed:

```text
Error executing tool bng_device_cpu_usage: {
  "error": "invalid_interval",
  "detail": "Cannot parse interval '5xyz'",
  "device": "test-ns/bng-01",
  "interval": "5xyz"
}
```

Resource handlers still return the serialized JSON string directly
because the MCP resource protocol does not support `isError`.

### Success responses

Define a Pydantic model for every non-trivial tool result.  Avoid returning
ad-hoc `json.dumps(dict)` — models give you validation, a documented schema,
and consistent serialization:

```python
# bng/types.py
class DeviceUnavailabilityResult(BaseModel):
    device: str
    interval: str
    step: str
    unavailable_steps: int
    currently_available: bool | None = None
    result: MetricResult | None = None
```

```python
# in the tool handler
return DeviceUnavailabilityResult(
    device=device, interval=interval, step=step, ...
).model_dump_json(indent=2)
```

### Target summaries and shared domain types

Reusable data shapes that appear in multiple resources or tools belong in
`<nf>/types.py` as Pydantic models.  Helper functions in `<nf>/common.py` build
them from raw K8s or Prometheus data:

```python
# bng/types.py
class TargetSummary(BaseModel):
    name: str | None = None
    address: str | None = None
    hostname: str | None = None
    kind: str | None = None
    namespace: str | None = None

# bng/common.py
def _target_summary(target: NetworkDeviceTarget | NetworkHostTarget) -> TargetSummary:
    return TargetSummary(
        name=target.metadata.name if target.metadata else None,
        ...
    )
```

---

## Using core clients

### Client instantiation

Clients are instantiated in the registration functions using values from
`Settings`.  Do not construct clients at module level or as globals:

```python
def register_<nf>_tools(mcp: FastMCP, settings: Settings) -> None:
    k8s = KubernetesClient(namespace=settings.k8s_namespace)
    prom = PrometheusClient(
        base_url=settings.prometheus_url,
        verify=not settings.tls_skip_verify,
    )
    # tool handlers close over k8s and prom
```

### Available clients

| Client              | Module                          | Data source  | Result types                        |
|---------------------|---------------------------------|--------------|-------------------------------------|
| `KubernetesClient`  | `core/kubernetes_client.py`     | K8s CRDs     | `NetworkDeviceTarget`, `NetworkHostTarget` |
| `PrometheusClient`  | `core/prometheus_client.py`     | Prometheus   | `MetricResult`, `MetricSeries`      |
| `LokiClient`        | `core/loki_client.py`           | Loki         | `LogResult`, `LogEntry`             |

### Exception handling

**No raw exception may escape a tool or resource handler.**  An unhandled
exception surfaces as an opaque MCP error to the LLM, losing all diagnostic
context.  Exceptions must be caught and converted into a structured
`ErrorResponse` JSON at one of two levels:

#### Layer 1 — Core clients wrap infrastructure exceptions

Each core client translates third-party / library exceptions (`httpx`,
`kubernetes_asyncio`, etc.) into its own typed hierarchy.  Callers never see
`httpx.TimeoutException` or `ApiException` — only the client's own classes:

```
KubernetesClient                            PrometheusClient
    KubernetesClientError (base)                PrometheusClientError (base)
    ├── KubernetesNotFoundError (404)           ├── PrometheusConnectionError
    ├── KubernetesPermissionError (403)         ├── PrometheusTimeoutError
    ├── KubernetesBadRequestError (400)         └── PrometheusHTTPError
    ├── KubernetesNamespaceNotFoundError
    └── KubernetesSchemaError               LokiClient
                                                (to be defined)
```

When adding a new core client or extending an existing one, follow the same
pattern — catch every library exception and re-raise as a dedicated subclass
with a human-readable message and the original exception chained via `from`:

```python
# core client — wrap ALL library exceptions
try:
    response = await client.post("/api/v1/query", data=params)
    response.raise_for_status()
except httpx.TimeoutException as exc:
    raise PrometheusTimeoutError(f"Prometheus query timed out: {exc}") from exc
except httpx.NetworkError as exc:
    raise PrometheusConnectionError(
        f"Cannot reach Prometheus at {self.base_url}: {exc}"
    ) from exc
except httpx.HTTPStatusError as exc:
    raise PrometheusHTTPError(
        f"Prometheus returned HTTP {exc.response.status_code}: {exc}",
        status_code=exc.response.status_code,
    ) from exc
except httpx.HTTPError as exc:
    raise PrometheusClientError(f"Prometheus request failed: {exc}") from exc
```

**Rules for core clients:**

- Every public method must document its exceptions in the `Raises:` docstring.
- Never let a third-party exception propagate uncaught — always wrap it.
- Always chain the original exception with `from exc` for traceback context.
- Map HTTP status codes to semantically named subclasses (404 -> `NotFoundError`,
  403 -> `PermissionError`, etc.) so handlers can differentiate.

#### Layer 2 — NF handlers catch client exceptions and raise ErrorResponse

Tool handlers catch the client's base exception and **raise** an
`ErrorResponse`.  Because `ErrorResponse` inherits from `Exception`,
FastMCP's `Tool.run` catches it, wraps it in `ToolError`, and the
low-level handler produces `CallToolResult(isError=True)` automatically.
This is the **last line of defense** — nothing may escape past this point:

```python
# Tool handler — catch and raise ErrorResponse
try:
    result = await prom.query_range(promql, start=start, end=end, step=step)
except PrometheusClientError as exc:
    logger.error("bng_device_unavailability_map failed: %s", exc)
    raise ErrorResponse(
        error="prometheus_error", detail=str(exc), device=device,
    ) from exc
```

For K8s errors in tools, use `_raise_k8s_error` in `<nf>/common.py`
which classifies the error category and raises `ErrorResponse`:

```python
# Tool handler — delegate to shared helper
try:
    devices = await k8s.list_network_device_targets(label_selector=label_selector)
except KubernetesClientError as exc:
    await _raise_k8s_error("bng://targets/by-label", exc, ctx)
```

Resource handlers use `_handle_k8s_error` which returns a JSON string
(the resource protocol does not support `isError`):

```python
# Resource handler — returns JSON string
try:
    devices = await k8s.list_network_device_targets()
except KubernetesClientError as exc:
    return await _handle_k8s_error("bng://targets", exc, ctx)
```

**Rules for NF handlers:**

- Catch the **base** client exception class (e.g. `KubernetesClientError`,
  `PrometheusClientError`), not individual subclasses — unless you need
  different error categories per subclass (as `_raise_k8s_error` does).
- Always `logger.error(...)` before raising — the `ErrorResponse` goes to
  the LLM, the log goes to operators.
- If a handler performs multiple client calls, each call site must be wrapped
  or the entire block must be covered by a single `except`.
- In tool handlers, always **raise** `ErrorResponse` — FastMCP sets the MCP
  `isError=True` flag automatically.  Resource handlers return JSON strings
  directly.
- Validate inputs (e.g. interval parsing) and raise `ErrorResponse` for
  invalid input — do not raise `ValueError`.
- Always chain the original exception with `from exc`.
- If you add a new error category, choose a machine-readable string consistent
  with existing ones: `not_found`, `bad_request`, `permission_denied`,
  `prometheus_error`, `invalid_interval`, etc.

#### Summary: two-layer exception contract

```text
┌─────────────────────────────────────────────────────────────────┐
│  LLM / MCP Client                                               │
│  Receives: CallToolResult(isError=True) or success result       │
├─────────────────────────────────────────────────────────────────┤
│  FastMCP (Tool.run → ToolError → low-level handler)             │
│  Catches: Exception  →  CallToolResult(isError=True)            │
├─────────────────────────────────────────────────────────────────┤
│  NF tool handler (tools.py)                  LAYER 2            │
│  Catches: ClientError  →  raise ErrorResponse(...)              │
│  Nothing escapes uncaught; structured JSON in exception message  │
├─────────────────────────────────────────────────────────────────┤
│  Core client (prometheus_client.py, etc.)    LAYER 1            │
│  Catches: httpx.*, ApiException, etc.                           │
│  Re-raises as: PrometheusClientError, KubernetesClientError     │
├─────────────────────────────────────────────────────────────────┤
│  Third-party libraries (httpx, kubernetes_asyncio)              │
└─────────────────────────────────────────────────────────────────┘
```

---

## Context parameter

Use bare `Context` in `@mcp.resource` and `@mcp.tool` decorated functions:

```python
# good
async def my_tool(param: str, ctx: Context) -> str: ...

# bad — pydantic validate_call loses private attributes
async def my_tool(param: str, ctx: Context[Any, Any, Any]) -> str: ...
```

See the note in [CLAUDE.md](../CLAUDE.md) for the technical reason.
