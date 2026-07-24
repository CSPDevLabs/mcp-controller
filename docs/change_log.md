# Change Log

## 2026-07-24

### Add `bng_health_summary` tool

Added a fleet-wide health assessment tool. It lists every
`NetworkDeviceTarget` and, for each, runs CPU-usage and
memory-utilisation instant queries, classifies the device
`green`/`yellow`/`red` (`red` when unreachable, `unknown` when no data),
and reports an overall worst-case status. Exposed as a tool (not a
resource) so LLM clients can invoke it directly.

**Changes:**

- **`bng/types.py`** -- Added the `HealthStatus` literal and the
  `DeviceHealth` and `BngHealthSummary` response models.
- **`bng/tools.py`** -- Added the `bng_health_summary` tool plus
  module-level CPU/memory thresholds and helpers `_worst_status`,
  `_classify`, `_instant_value`, and `_assess_device_health`. Per-device
  Prometheus failures are swallowed to `unknown`/unavailable so one flaky
  device cannot break the summary; K8s listing errors still raise a typed
  `ErrorResponse`.
- **`bng/resources.py`** -- Registered `bng_health_summary` in
  `BNG_MANIFEST` as a `tool` capability (tags `OBSERVABILITY`, `STATUS`).
- **`tests/bng/test_bng_health_summary.py`** -- Added 14 tests covering
  per-device classification (green/yellow/red/unknown, boundary values,
  partial data), fleet worst-case aggregation and status counts, the
  empty-device and no-op query cases, `source`-label resolution (gnmic
  `role=bng` vs settings-namespace fallback), Kubernetes error
  propagation, Prometheus-failure tolerance, and the manifest contract.

### Fix `uv run pytest` — move dev toolchain into the default dependency group

`uv run pytest` failed with `Failed to spawn: pytest` on a freshly
resolved environment: the test/lint tools were declared only in the
PEP 621 `[project.optional-dependencies].dev` **extra**, which `uv run`
does not install unless `--extra dev` is passed. This was also
inconsistent with the `Dockerfile`, which excludes dev tooling via
`uv sync --no-dev` (a dependency-group flag).

**Changes:**

- **`pyproject.toml`** -- Moved the full dev/test toolchain (`black`,
  `flake8`, `flake8-bugbear`, `pytest`, `pytest-cov`, `pytest-asyncio`,
  `respx`, `datamodel-code-generator`, `ipython`) from
  `[project.optional-dependencies].dev` into `[dependency-groups].dev`,
  which uv installs by default. Removed the now-redundant
  optional-dependencies extra.
- **`uv.lock`** -- Regenerated to reflect the group move.
- **`docs/arch_and_concepts.md`, `docs/mcp-server-inspector-setup.md`,
  `scripts/gen_k8s_types.py`** -- Replaced the obsolete
  `pip install -e ".[dev]"` instructions with `uv sync`.

### Update `bng_device_unavailability_map` metrics

Broadened the availability signal from a single CPU metric to a
multi-metric presence check, and made device matching more robust.

**Changes:**

- **`bng/tools.py`** -- Reworked the `bng_device_unavailability_map`
  PromQL:
  - The `absent_over_time()` selector now spans three metrics via a
    `__name__=~` regex — `state_system_cpu_summary_usage_cpu_time`,
    `state_system_resource_usage_subscriber_next_hop_entries_total`, and
    `state_router_interface_statistics_ip_in_octets` — so a device is
    only flagged absent when none of these are reporting.
  - Device matching changed from an exact `source="…"` label to a
    `source=~".*<device>"` regex, wrapped in `label_replace(...)` to
    normalise the `source` label back to the fully-qualified
    `namespace/name` value.
  - Replaced the `(absent_over_time(...) == 1) OR (count … * 0)`
    expression with `label_replace(absent_over_time(...)) or count by
    (source) (...) * 0`, keeping the `1` (absent) / `0` (reporting)
    step semantics.
  - Updated the docstring to describe the CPU / next-hop /
    interface-octets coverage.

## 2026-05-18

### BNG resource-usage metric tools and shared query-window helper

Added a family of BNG metric tools covering subscriber-management session
counts and card/fp/mda resource allocation. Each tool reports current value
plus min/max over the lookback window, and — where a paired `_total` series
exists — current/min/max utilisation (%). Factored the duration parsing and
time-range resolution shared by every metric tool into a single helper.

**Changes:**

- **`bng/common.py`** -- Added `resolve_query_window(interval, step, device,
  start_time="")` returning a `QueryWindow` (`prom_interval`, `prom_step`,
  `start_dt`, `end_dt`). Wraps the two `parse_duration` calls and the
  `parse_start_time` call, mapping each failure to a typed `ErrorResponse`
  (`invalid_interval`, `invalid_step`, `invalid_start_time`).
- **`bng/types.py`** -- Added `QueryWindow` plus the response models for the
  new tools: `PppSessionsTotalEstablishedResult`, `SapInstancesAllocatedResult`,
  `IngressPolicersAllocatedResult`, `EgressPolicersAllocatedResult`,
  `IngressQueuesAllocatedResult`, `EgressQueuesAllocatedResult`, and
  `SubscriberNextHopEntriesAllocatedResult`. Per-slot/per-fp entries carry
  current and min/max counts (and utilisation % where applicable).
- **`bng/tools.py`** -- Added `ppp_sessions_total_established`,
  `sap_instances_allocated`, `ingress_policers_allocated`,
  `egress_policers_allocated`, `ingress_queues_allocated`,
  `egress_queues_allocated`, and `subscriber_next_hop_entries_allocated`.
  All call `resolve_query_window` for time handling and `verify_device_target`
  for device existence checks before issuing PromQL. Card/fp/mda filter
  arguments are optional and applied as PromQL label matchers when provided.
- **`bng/resources.py`** -- Registered the seven new tools in `BNG_MANIFEST`
  with `CapabilityTag.METRICS`/`SUBSCRIBER_MGMT`/`OBSERVABILITY`/`RESOURCES`
  tags as appropriate.

## 2026-04-17

### Restore green test suite — fix config defaults, controller-id aliasing, respx HTTP method

Brought `uv run pytest -q` back to green by aligning three independent source/test
mismatches. No behavioural changes to production code paths.

**Changes:**

- **`config.py`** -- `port` default changed from `8001` to `8000` and
  `log_level` default changed from `"DEBUG"` to `"INFO"` so the shipped
  defaults match the contract asserted by `test_defaults`.
- **`tests/core/test_config.py`** -- The four YAML tests called a non-existent
  `Settings.from_yaml(...)` classmethod. Rewrote them to use the existing
  `Settings(yaml_file=...)` constructor path, which already implements the
  same init-kwargs > YAML > env priority the tests assert via
  `settings_customise_sources`.
- **`core/controllers.py`** -- Added `ControllerId._missing_` to accept the
  short alias `"bng"` in addition to the canonical value
  `"bng.mcp.controllers.nok.dev"`. Input ergonomics only; serialisation still
  emits the canonical reverse-DNS form.
- **`tests/core/test_registry.py`** -- Updated the two assertions that expected
  the short form (`manifest.controller_id == ControllerId.BNG`,
  `data["controller_id"] == "bng.mcp.controllers.nok.dev"`). Replaced the
  `ControllerDependency(controller_id="sr-mpls", ...)` fixture with the
  existing `"bng"` id — the test validates model shape and capability refs,
  so the referenced controller identity is incidental.
- **`tests/core/test_prometheus_client.py`** -- Swapped `respx.get(...)` for
  `respx.post(...)` on four mocks. `PrometheusClient.query` and
  `query_range` send `POST` (Prometheus-idiomatic for queries, safer for
  long PromQL). Also aligned `test_query_error` to assert
  `PrometheusClientError` (the documented exception) instead of `RuntimeError`.

## 2026-04-15

### Add `bng_device_cpu_usage` tool

New MCP tool that returns a CPU usage time series for a BNG device over a
lookback window. Executes a range query on
`state_system_cpu_summary_usage_cpu_usage{cpu_sample_period="60",source="<device>"}`
and returns summary statistics (avg, min, max) alongside the full raw matrix.
The `device` is matched via the Prometheus `source` label which aligns with
the `bng://targets/devices` resource.

**Changes:**

- **`bng/types.py`** -- Added `DeviceCpuUsageResult` Pydantic model with
  `device`, `interval`, `step`, `sample_count`, `avg_cpu_usage`,
  `min_cpu_usage`, `max_cpu_usage`, and the raw `MetricResult`.
- **`bng/tools.py`** -- Added `bng_device_cpu_usage(device, interval, step, ctx)`.
  Validates inputs via `parse_duration`, executes the range query on
  `prom.query_range`, computes stats across all samples, and returns a
  structured JSON response. On failure returns an `ErrorResponse` with
  `invalid_interval`, `invalid_step`, `prometheus_error`, or
  `unexpected_result_type`.
- **`bng/resources.py`** -- Registered `bng_device_cpu_usage` in
  `BNG_MANIFEST` with `CapabilityTag.HEALTH` and `CapabilityTag.METRICS`.

### Extract `parse_duration` with step validation and Prometheus-unit normalisation

The duration-parsing logic that was inlined in `bng_device_unavailability_map`
was duplicated when `bng_device_cpu_usage` landed. Extracted into a shared
helper. While refactoring, two additional gaps were closed:

- Only `interval` was validated -- `step` was passed straight through to
  Prometheus, so a bad step surfaced as an opaque Prometheus error.
- The parser accepted human-friendly aliases (`"30sec"`, `"5min"`,
  `"2hours"`) but passed them unchanged into PromQL range selectors and the
  `query_range` `step` param, which Prometheus rejects (only `s`, `m`, `h`,
  `d`, `w`, `y` are valid suffixes).

**Changes:**

- **`bng/common.py`** -- Added `_DURATION_UNITS` (alias -> `timedelta` kwarg)
  and `_PROMETHEUS_SUFFIX` (alias -> canonical Prometheus suffix).
  `parse_duration(value)` now returns `tuple[timedelta, str]` where the second
  element is the value re-emitted in Prometheus-compatible shorthand
  (`"30sec"` -> `"30s"`, `"2hours"` -> `"2h"`). Raises `ValueError` on
  unparseable input.
- **`bng/tools.py`** -- `bng_device_unavailability_map` and
  `bng_device_cpu_usage` both call `parse_duration` twice (once for
  `interval`, once for `step`). The normalised strings are used when building
  PromQL (e.g. `[{prom_interval}]` in the `absent_over_time` selector) and
  when calling `prom.query_range(step=prom_step)`. New `invalid_step` error
  category covers malformed step input.
- **`bng/tools.py`** -- Removed the now-unused `timedelta` import.

### Add `bng_device_memory_usage` tool

New MCP tool that returns memory utilisation (%) time series for a BNG device.
Computes utilisation as
`sum by(source)(in_use) / on(source) (sum by(source)(in_use) + sum by(source)(available)) * 100`
where `in_use` is `state_system_memory_pools_summary_total_in_use` and
`available` is `state_system_memory_pools_summary_available_memory`. Follows
the same shape as `bng_device_cpu_usage`.

**Changes:**

- **`bng/types.py`** -- Added `DeviceMemoryUsageResult` with
  `avg_memory_usage_pct`, `min_memory_usage_pct`, `max_memory_usage_pct`
  (all percentages) plus the usual `device`/`interval`/`step`/`sample_count`
  and raw `MetricResult`.
- **`bng/tools.py`** -- Added `bng_device_memory_usage(device, interval, step, ctx)`.
  Shares the `parse_duration` validation/normalisation path with the other
  device tools and returns the same error categories.
- **`bng/resources.py`** -- Registered `bng_device_memory_usage` in
  `BNG_MANIFEST` with `CapabilityTag.HEALTH` and `CapabilityTag.METRICS`.

### Unit tests for CPU and memory metric tools

**Changes:**

- **`tests/fixtures/prometheus/`** -- New directory with three JSON fixtures:
  `cpu_usage_range.json` (5-sample matrix), `memory_usage_range.json`
  (5-sample matrix), and `empty_range.json` (empty matrix for no-data cases).
- **`tests/bng/test_bng_metric_tools.py`** -- New test module (13 tests).
  Helpers `_parse_prom_fixture`, `_make_mock_prom`, and `_register_and_get_tool`
  patch `PrometheusClient` and `KubernetesClient` at the factory level per the
  CLAUDE.md guideline.
  - `TestBngDeviceCpuUsage` (6 tests) -- summary stats from fixture data,
    null stats on empty result, `invalid_interval` / `invalid_step` guards,
    `prometheus_error` on client failure, and step normalisation
    (`"30sec"` reaching Prometheus as `"30s"`).
  - `TestBngDeviceMemoryUsage` (7 tests) -- same 6 cases plus a PromQL
    assertion that both memory metrics and the `source="bng-01"` label are
    present in the composed query.

### Record/replay mock mode for offline MCP server runs

Added a record/replay mock system so the MCP server can be exercised end-to-end
without a live Kubernetes, Prometheus, or Loki backend. A single decorator
wraps every resource, tool, and completion handler; in normal mode it is a
no-op (zero runtime overhead), in record mode it persists handler
args + responses to JSON, and in replay mode it serves those responses
directly without calling any backend.

**Changes:**

- **`core/mock.py`** -- New module exposing `mock_intercept(settings)` and
  `MockDataNotFoundError`. The decorator inspects `settings.mock` and
  `settings.mock_data_record`:
  - Neither flag set -> returns the original function unwrapped.
  - `mock_data_record=True` -> calls the handler, serialises args (excluding
    `ctx`/`context`) plus the result into
    `tests/mocks/data/<function_name>.json`. Same args upsert the existing
    entry; different args append a new one.
  - `mock=True` -> short-circuits before the handler body, looks up the entry
    whose `args` match the current call, and returns its `mock_data`. Raises
    `MockDataNotFoundError` when no match is found.
  Helpers `_extract_args`, `_serialize_result`, `_mock_save`, `_mock_lookup`
  handle Pydantic `BaseModel` inputs and outputs via `model_dump(mode="json")`.
- **`config.py`** -- Added `mock_data_record: bool = False`
  (`MCP_MOCK_DATA_RECORD` env var). Existing `mock` field is now wired up.
- **`__main__.py`** -- Added mutually exclusive CLI flags `--mock` and
  `--mock-data-record`; both propagate into `Settings`.
- **`server.py`** -- Emits a `WARNING` log line when either mock mode is
  active so it is obvious the server is not hitting live backends.
- **`bng/resources.py`** -- Applied `@mock_intercept(settings)` to all 8
  resource and resource-template handlers (`bng_manifest`, `bng_namespaces`,
  `bng_targets`, `bng_target_devices`, `bng_target_hosts`,
  `bng_health_summary`, `bng_target_devices_by_ns`, `bng_target_hosts_by_ns`).
- **`bng/tools.py`** -- Applied `@mock_intercept(settings)` to all 8 tool
  handlers (`bng_targets_by_label`, `read_bng_manifest`,
  `discover_bng_capabilities`, `read_bng_resource`,
  `bng_device_unavailability_map`, `bng_device_cpu_usage`,
  `bng_device_memory_usage`, `bng_device_hosts_stats`).
- **`bng/completions.py`** -- Applied `@mock_intercept(settings)` to
  `handle_completion`.
- **`tests/mocks/__init__.py`, `tests/mocks/data/.gitkeep`** -- New
  package/directory layout. Recorded fixture files land in
  `tests/mocks/data/<function_name>.json`.
- **`tests/core/test_mock.py`** -- New test module (21 tests):
  - `_data_dir` path resolution.
  - `_extract_args` -- ctx exclusion, defaults, Pydantic serialisation.
  - `_serialize_result` -- Pydantic models, lists of models, strings, dicts.
  - `_mock_save` / `_mock_lookup` -- file creation, upsert-on-same-args,
    append-on-different-args, missing-file error, missing-args error.
  - `mock_intercept` -- no-op in normal mode, calls-and-saves in record mode,
    short-circuits without calling the real handler in replay mode, raises
    `MockDataNotFoundError` for unrecorded args, excludes positional `ctx`.
- **`tests/test_mock_mode.py`** -- New integration module (5 tests) covering
  end-to-end record/replay for zero-arg resources, parameterised tools,
  error paths, and upsert semantics.
- **`docs/arch_and_concepts.md`** -- New section 8 "Mock Mode (Record/Replay)"
  describing the decorator design, data format, CLI/env flags, workflow, and
  test coverage.

## 2026-04-08

### Device target name normalisation

Normalised how device names are specified in BNG metric tools. gNMIc targets
use `namespace/name` as the Prometheus `source` label (e.g.
`nok-bng/clab-sros-bngt-bng1`), but callers may omit the namespace prefix.

**Changes:**

- **`bng/common.py`** -- Added `resolve_device_source(device, default_namespace)`.
  Prepends the default K8s namespace when the caller supplies a bare device
  name (no `/`); returns the value unchanged when a namespace prefix is
  already present.
- **`bng/tools.py`** -- `bng_device_cpu_usage`, `bng_device_memory_usage`, and
  `bng_device_unavailability_map` now call `resolve_device_source` at the top
  of every invocation so both `"clab-sros-bngt-bng1"` and
  `"nok-bng/clab-sros-bngt-bng1"` resolve to the same PromQL query.
- **`tests/bng/test_bng_metric_tools.py`** -- Updated assertions to expect the
  resolved `namespace/device` form in response payloads and PromQL strings.

### Device existence verification against K8s targets

Before querying Prometheus, all three device metric tools now verify that the
requested device actually exists as a `NetworkDeviceTarget` CR. This prevents
silent empty-result queries for mistyped device names.

**Changes:**

- **`bng/common.py`** -- Added `verify_device_target(device_source, mcp)`.
  Reads the static `bng://targets/devices` MCP resource, parses the JSON
  target list, and checks that a target with the expected `metadata.name` and
  `metadata.namespace` is present. Raises `KubernetesNotFoundError` on
  mismatch, `KubernetesClientError` on backend failures.
- **`bng/tools.py`** -- `bng_device_cpu_usage`, `bng_device_memory_usage`, and
  `bng_device_unavailability_map` call `verify_device_target` after name
  resolution. On failure a structured `device_not_found` error is returned and
  Prometheus is never called.
- **`tests/bng/test_device_verification.py`** -- New test module (11 tests):
  - `TestResolveDeviceSource` -- namespace prepending, passthrough, slash
    handling.
  - `TestVerifyDeviceTarget` -- device found, unknown device, namespace
    mismatch, K8s backend error.
  - `TestToolDeviceVerification` -- end-to-end: all 3 tools return
    `device_not_found` for unknown devices; CPU tool passes through for
    existing devices.
- **`tests/bng/test_bng_metric_tools.py`** -- Added `_bypass_device_verification`
  autouse fixture so metric-focused tests remain isolated from K8s
  verification.

### Extract `compute_sample_stats` into `bng/common.py`

The identical avg/min/max computation block was duplicated in
`bng_device_cpu_usage` and `bng_device_memory_usage`. Extracted into a
reusable helper so future metric tools (SRRP, FP resources, subscribers)
can share the same logic.

**Changes:**

- **`bng/common.py`** -- Added frozen dataclass `SampleStats` (count, avg,
  min, max) and `compute_sample_stats(result: MetricResult) -> SampleStats`.
  Flattens all sample values across series and returns aggregated statistics
  (`None` fields when no samples exist).
- **`bng/tools.py`** -- `bng_device_cpu_usage` and `bng_device_memory_usage`
  replaced their inline stat blocks with a single `compute_sample_stats()`
  call.
