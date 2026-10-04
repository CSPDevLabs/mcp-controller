# Prometheus Rule Creation via MCP Feature

> **Status: implemented** (BNG controller manifest v1.1.0). This document
> remains the design reference — see
> [arch_and_concepts.md §11 "Future Considerations"](../arch_and_concepts.md#11-future-considerations)
> for what shipped versus what was deferred (live PromQL validation, delete
> tool, GitOps write path, readiness probe integration, and
> `gen_k8s_types.py` seed support for the monitoring CRDs).

This document describes MCP feature to create PrometheusRules via MCP.
The feature is exposing necessary MCP tools in order to create PrometheusRules.
Keeping in mind that PrometheusRules are defined by a CRD, the feature is exposing the CRD schema and necessary tools to create PrometheusRules.
It must be accompanied by prompt to guide the user through the process of creating a PrometheusRule and supply necessary information to LLM in order to create the PrometheusRule w/o mistakes.
Prompt must supply actual CRD schema and reference the tools to use in order to create the PrometheusRule as well as retrieving necessary up to date information from the cluster and Internet resources on how to write PromQL queries.
Since CRDs supposed to be up to date descriptions of the fields, the feature must be able to retrieve the latest CRD schema and update the prompt with the latest information and links to the latest documentation.

See [Implementation Plan](#implementation-plan) for the design, and
[Lab cluster state](#lab-cluster-state) for the environment facts it relies on.

Everything below the introduction is a **captured snapshot** taken from the lab
cluster (prometheus-operator `v0.89.0`, per the CRD's
`operator.prometheus.io/version` annotation) and serves as design reference
only. The feature reads the CRD schema and the `Prometheus` selectors live from
the API server on every prompt render, so nothing here is authoritative at
runtime.

## PrometheusRule `kubectl explain` output

```bash
> kubectl explain prometheusrules --recursive=true --output=plaintext-openapiv2
```

```plaintext
KIND:     PrometheusRule
VERSION:  monitoring.coreos.com/v1

DESCRIPTION:
     The `PrometheusRule` custom resource definition (CRD) defines
     [alerting](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/)
     and
     [recording](https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/)
     rules to be evaluated by `Prometheus` or `ThanosRuler` objects.

     `Prometheus` and `ThanosRuler` objects select `PrometheusRule` objects
     using label and namespace selectors.

FIELDS:
   apiVersion   <string>
   kind <string>
   metadata     <Object>
      annotations       <map[string]string>
      creationTimestamp <string>
      deletionGracePeriodSeconds        <integer>
      deletionTimestamp <string>
      finalizers        <[]string>
      generateName      <string>
      generation        <integer>
      labels    <map[string]string>
      managedFields     <[]Object>
         apiVersion     <string>
         fieldsType     <string>
         fieldsV1       <map[string]>
         manager        <string>
         operation      <string>
         subresource    <string>
         time   <string>
      name      <string>
      namespace <string>
      ownerReferences   <[]Object>
         apiVersion     <string>
         blockOwnerDeletion     <boolean>
         controller     <boolean>
         kind   <string>
         name   <string>
         uid    <string>
      resourceVersion   <string>
      selfLink  <string>
      uid       <string>
   spec <Object>
      groups    <[]Object>
         interval       <string>
         labels <map[string]string>
         limit  <integer>
         name   <string>
         partial_response_strategy      <string>
         query_offset   <string>
         rules  <[]Object>
            alert       <string>
            annotations <map[string]string>
            expr        <>
            for <string>
            keep_firing_for     <string>
            labels      <map[string]string>
            record      <string>
   status       <Object>
      bindings  <[]Object>
         conditions     <[]Object>
            lastTransitionTime  <string>
            message     <string>
            observedGeneration  <integer>
            reason      <string>
            status      <string>
            type        <string>
         group  <string>
         name   <string>
         namespace      <string>
         resource       <string>
```

## PrometheusRule CRD

```bash
> kubectl get customresourcedefinitions.apiextensions.k8s.io prometheusrules.monitoring.coreos.com  -o yaml
```

```yaml
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  annotations:
    controller-gen.kubebuilder.io/version: v0.19.0
    operator.prometheus.io/version: 0.89.0
  creationTimestamp: "2026-07-30T13:10:42Z"
  generation: 1
  name: prometheusrules.monitoring.coreos.com
  resourceVersion: "1775"
  uid: ce8848ba-2ca6-445b-8096-40ec956596dd
spec:
  conversion:
    strategy: None
  group: monitoring.coreos.com
  names:
    categories:
    - prometheus-operator
    kind: PrometheusRule
    listKind: PrometheusRuleList
    plural: prometheusrules
    shortNames:
    - promrule
    singular: prometheusrule
  scope: Namespaced
  versions:
  - name: v1
    schema:
      openAPIV3Schema:
        description: |-
          The `PrometheusRule` custom resource definition (CRD) defines [alerting](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/) and [recording](https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/) rules to be evaluated by `Prometheus` or `ThanosRuler` objects.

          `Prometheus` and `ThanosRuler` objects select `PrometheusRule` objects using label and namespace selectors.
        properties:
          apiVersion:
            description: |-
              APIVersion defines the versioned schema of this representation of an object.
              Servers should convert recognized schemas to the latest internal value, and
              may reject unrecognized values.
              More info: https://git.k8s.io/community/contributors/devel/sig-architecture/api-conventions.md#resources
            type: string
          kind:
            description: |-
              Kind is a string value representing the REST resource this object represents.
              Servers may infer this from the endpoint the client submits requests to.
              Cannot be updated.
              In CamelCase.
              More info: https://git.k8s.io/community/contributors/devel/sig-architecture/api-conventions.md#types-kinds
            type: string
          metadata:
            type: object
          spec:
            description: spec defines the specification of desired alerting rule definitions
              for Prometheus.
            properties:
              groups:
                description: groups defines the content of Prometheus rule file
                items:
                  description: RuleGroup is a list of sequentially evaluated recording
                    and alerting rules.
                  properties:
                    interval:
                      description: interval defines how often rules in the group are
                        evaluated.
                      pattern: ^(0|(([0-9]+)y)?(([0-9]+)w)?(([0-9]+)d)?(([0-9]+)h)?(([0-9]+)m)?(([0-9]+)s)?(([0-9]+)ms)?)$
                      type: string
                    labels:
                      additionalProperties:
                        type: string
                      description: |-
                        labels define the labels to add or overwrite before storing the result for its rules.
                        The labels defined at the rule level take precedence.

                        It requires Prometheus >= 3.0.0.
                        The field is ignored for Thanos Ruler.
                      type: object
                    limit:
                      description: |-
                        limit defines the number of alerts an alerting rule and series a recording
                        rule can produce.
                        Limit is supported starting with Prometheus >= 2.31 and Thanos Ruler >= 0.24.
                      type: integer
                    name:
                      description: name defines the name of the rule group.
                      minLength: 1
                      type: string
                    partial_response_strategy:
                      description: |-
                        partial_response_strategy is only used by ThanosRuler and will
                        be ignored by Prometheus instances.
                        More info: https://github.com/thanos-io/thanos/blob/main/docs/components/rule.md#partial-response
                      pattern: ^(?i)(abort|warn)?$
                      type: string
                    query_offset:
                      description: |-
                        query_offset defines the offset the rule evaluation timestamp of this particular group by the specified duration into the past.

                        It requires Prometheus >= v2.53.0.
                        It is not supported for ThanosRuler.
                      pattern: ^(0|(([0-9]+)y)?(([0-9]+)w)?(([0-9]+)d)?(([0-9]+)h)?(([0-9]+)m)?(([0-9]+)s)?(([0-9]+)ms)?)$
                      type: string
                    rules:
                      description: rules defines the list of alerting and recording
                        rules.
                      items:
                        description: |-
                          Rule describes an alerting or recording rule
                          See Prometheus documentation: [alerting](https://www.prometheus.io/docs/prometheus/latest/configuration/alerting_rules/) or [recording](https://www.prometheus.io/docs/prometheus/latest/configuration/recording_rules/#recording-rules) rule
                        properties:
                          alert:
                            description: |-
                              alert defines the name of the alert. Must be a valid label value.
                              Only one of `record` and `alert` must be set.
                            type: string
                          annotations:
                            additionalProperties:
                              type: string
                            description: |-
                              annotations defines annotations to add to each alert.
                              Only valid for alerting rules.
                            type: object
                          expr:
                            anyOf:
                            - type: integer
                            - type: string
                            description: expr defines the PromQL expression to evaluate.
                            x-kubernetes-int-or-string: true
                          for:
                            description: for defines how alerts are considered firing
                              once they have been returned for this long.
                            pattern: ^(0|(([0-9]+)y)?(([0-9]+)w)?(([0-9]+)d)?(([0-9]+)h)?(([0-9]+)m)?(([0-9]+)s)?(([0-9]+)ms)?)$
                            type: string
                          keep_firing_for:
                            description: keep_firing_for defines how long an alert
                              will continue firing after the condition that triggered
                              it has cleared.
                            minLength: 1
                            pattern: ^(0|(([0-9]+)y)?(([0-9]+)w)?(([0-9]+)d)?(([0-9]+)h)?(([0-9]+)m)?(([0-9]+)s)?(([0-9]+)ms)?)$
                            type: string
                          labels:
                            additionalProperties:
                              type: string
                            description: labels defines labels to add or overwrite.
                            type: object
                          record:
                            description: |-
                              record defines the name of the time series to output to. Must be a valid metric name.
                              Only one of `record` and `alert` must be set.
                            type: string
                        required:
                        - expr
                        type: object
                      type: array
                  required:
                  - name
                  type: object
                type: array
                x-kubernetes-list-map-keys:
                - name
                x-kubernetes-list-type: map
            type: object
          status:
            description: |-
              status defines the status subresource. It is under active development and is updated only when the
              "StatusForConfigurationResources" feature gate is enabled.

              Most recent observed status of the PrometheusRule. Read-only.
              More info:
              https://github.com/kubernetes/community/blob/master/contributors/devel/sig-architecture/api-conventions.md#spec-and-status
            properties:
              bindings:
                description: bindings defines the list of workload resources (Prometheus,
                  PrometheusAgent, ThanosRuler or Alertmanager) which select the configuration
                  resource.
                items:
                  description: WorkloadBinding is a link between a configuration resource
                    and a workload resource.
                  properties:
                    conditions:
                      description: conditions defines the current state of the configuration
                        resource when bound to the referenced Workload object.
                      items:
                        description: ConfigResourceCondition describes the status
                          of configuration resources linked to Prometheus, PrometheusAgent,
                          Alertmanager or ThanosRuler.
                        properties:
                          lastTransitionTime:
                            description: lastTransitionTime defines the time of the
                              last update to the current status property.
                            format: date-time
                            type: string
                          message:
                            description: message defines the human-readable message
                              indicating details for the condition's last transition.
                            type: string
                          observedGeneration:
                            description: |-
                              observedGeneration defines the .metadata.generation that the
                              condition was set based upon. For instance, if `.metadata.generation` is
                              currently 12, but the `.status.conditions[].observedGeneration` is 9, the
                              condition is out of date with respect to the current state of the object.
                            format: int64
                            type: integer
                          reason:
                            description: reason for the condition's last transition.
                            type: string
                          status:
                            description: status of the condition.
                            minLength: 1
                            type: string
                          type:
                            description: |-
                              type of the condition being reported.
                              Currently, only "Accepted" is supported.
                            enum:
                            - Accepted
                            minLength: 1
                            type: string
                        required:
                        - lastTransitionTime
                        - status
                        - type
                        type: object
                      type: array
                      x-kubernetes-list-map-keys:
                      - type
                      x-kubernetes-list-type: map
                    group:
                      description: group defines the group of the referenced resource.
                      enum:
                      - monitoring.coreos.com
                      type: string
                    name:
                      description: name defines the name of the referenced object.
                      minLength: 1
                      type: string
                    namespace:
                      description: namespace defines the namespace of the referenced
                        object.
                      minLength: 1
                      type: string
                    resource:
                      description: resource defines the type of resource being referenced
                        (e.g. Prometheus, PrometheusAgent, ThanosRuler or Alertmanager).
                      enum:
                      - prometheuses
                      - prometheusagents
                      - thanosrulers
                      - alertmanagers
                      type: string
                  required:
                  - group
                  - name
                  - namespace
                  - resource
                  type: object
                type: array
                x-kubernetes-list-map-keys:
                - group
                - resource
                - name
                - namespace
                x-kubernetes-list-type: map
            type: object
        required:
        - spec
        type: object
    served: true
    storage: true
    subresources:
      status: {}
status:
  acceptedNames:
    categories:
    - prometheus-operator
    kind: PrometheusRule
    listKind: PrometheusRuleList
    plural: prometheusrules
    shortNames:
    - promrule
    singular: prometheusrule
  conditions:
  - lastTransitionTime: "2026-07-30T13:10:42Z"
    message: no conflicts found
    reason: NoConflicts
    status: "True"
    type: NamesAccepted
  - lastTransitionTime: "2026-07-30T13:10:42Z"
    message: the initial names have been accepted
    reason: InitialNamesAccepted
    status: "True"
    type: Established
  storedVersions:
  - v1
```

## Lab cluster state

Captured snapshots backing the environment facts the Implementation Plan relies
on. `evaluationInterval`, `ruleSelector` and `ruleNamespaceSelector` are fields
of the **`Prometheus`** CR, not of `PrometheusRule`, so none of them appear in
the `kubectl explain` or CRD output above. The operator version comes from the
CRD's `operator.prometheus.io/version: 0.89.0` annotation.

### Which Prometheus instances select rules

```bash
> kubectl get prometheuses.monitoring.coreos.com -A \
    -o custom-columns='NAMESPACE:.metadata.namespace,NAME:.metadata.name,EVAL_INTERVAL:.spec.evaluationInterval,SCRAPE_INTERVAL:.spec.scrapeInterval,RULE_SELECTOR:.spec.ruleSelector,RULE_NS_SELECTOR:.spec.ruleNamespaceSelector'
```

```plaintext
NAMESPACE   NAME             EVAL_INTERVAL   SCRAPE_INTERVAL   RULE_SELECTOR                            RULE_NS_SELECTOR
nok-bbm     bbm-prometheus   30s             30s               <none>                                   <none>
nok-bng     nok-bng          30s             30s               map[matchLabels:map[role:alert-rules]]   <none>
```

```bash
> kubectl get prometheus -n nok-bng nok-bng -o jsonpath='{.spec}'   # relevant fields only
```

```yaml
evaluationInterval: 30s
scrapeInterval: 30s
ruleSelector:
  matchLabels:
    role: alert-rules
```

Consequences:

- **`nok-bng/nok-bng` is the only instance that will ever evaluate a rule this
  feature creates.** `bbm-prometheus` has no `ruleSelector`, which in
  prometheus-operator semantics selects *no* `PrometheusRule` objects at all.
- A rule must carry `role=alert-rules` to be selected. Without it the object is
  accepted by the API server and then silently never evaluated.
- `ruleNamespaceSelector` is unset, so the operator restricts selection to the
  `Prometheus` object's own namespace — rules must live in `nok-bng`.
- `evaluationInterval: 30s` is the basis for the "`for` should span at least two
  evaluations" guidance (so `1m` minimum). `scrapeInterval: 30s` bounds
  meaningful `rate()` windows.

### Existing rules and their ownership

```bash
> kubectl get prometheusrules.monitoring.coreos.com -A --show-labels
```

```plaintext
NAMESPACE   NAME                     AGE   LABELS
nok-bng     system-resource-alerts   7d    kustomize.toolkit.fluxcd.io/name=prometheus,kustomize.toolkit.fluxcd.io/namespace=flux-system,role=alert-rules
```

The only existing rule is Flux-managed. Since writes delete-then-create, the
tool must refuse when the existing object carries Flux labels
(`kustomize.toolkit.fluxcd.io/*`) — deleting and recreating it would take it
over and cause GitOps drift.

### No PrometheusRule admission webhook

```bash
> kubectl get validatingwebhookconfigurations -o json \
    | jq -r '.items[] | .metadata.name as $n | .webhooks[] | .rules[]? | select(.resources[]? | test("prometheusrule")) | $n'
```

```plaintext
No resources found
```

The prometheus-operator's optional `prometheusrules` validating webhook is not
deployed, so there is no PromQL validation — the API server will accept a
syntactically broken expression. This is why the tool validates every `expr`
against live Prometheus itself.

## Implementation Plan

### MCP surface

Six additions, all inside the existing `bng/` controller. Declared in
[arch_and_concepts.md](../arch_and_concepts.md) §2, §3, §4 and §6.

| Capability | Kind | Description |
| --- | --- | --- |
| `bng://alerts/configured` | resource | All `PrometheusRule` objects across namespaces — name, namespace, labels, and their groups/rules |
| `bng://alerts/configured/{namespace}` | resource template | The same, filtered by Kubernetes namespace |
| `bng://metrics/available` | resource | Metric names available in the Prometheus instance (see §[Metric discovery](#metric-discovery)) |
| `create_prometheus_alert_rule` | tool | Apply a `PrometheusRule` manifest of alerting rules supplied as a string |
| `bng-add-alert-rule` | prompt | Authoring workflow — carries the live CRD schema, required labels, and a PromQL cheat sheet, points at `bng://metrics/available`, and directs the LLM to retrieve further documentation from upstream |
| `bng-get-alert-rules` | prompt | Review workflow — lists configured rules for all namespaces or one |

Scope is **alerting rules only**. The `PrometheusRule` CRD carries recording
rules too, so this has to be enforced rather than assumed: the read resources
report whatever is deployed, the prompt states that `record` must not be used,
and the tool rejects a manifest containing it.

### Metric discovery

Any metric in the Prometheus instance is fair game for a rule, so the list is
exposed as its own resource rather than being curated by hand or embedded in the
prompt. The prompt only references it — the LLM pulls it when it needs it.

`bng://metrics/available` is built from `/api/v1/label/__name__/values`, which
returns the authoritative set of metric names currently known to Prometheus.

Two deliberate choices, both driven by what the lab actually returns:

- **Names only, not `/api/v1/metadata`.** The resource notes instead that the metric type must be inferred from
  the name — `become_master`, `master_changes`, `*_errors`, `*_discards` are
  monotonic counters needing `rate()` or `increase()`, while the
  `state_*_usage_*` and `*_current_value` families are gauges.
- **Rule-generated series are called out.** They are listed separately from device telemetry, alongside the scrape meta metrics (`up`, `scrape_*`), so the LLM does not mistake them for a signal.

The LLM shall read `bng://metrics/available` as an allow-list.

### The prompts carry the knowledge

This is the core of the design: there are no schema, documentation, or example
resources. Everything the LLM needs is assembled into the prompt body at render
time. FastMCP injects `Context` into prompt functions, so a prompt handler can
read resources and call clients like any other handler.

`bng-add-alert-rule` (argument: `objective: str`)
assembles:

1. **Live CRD schema** — the `openAPIV3Schema` of the storage version of
   `prometheusrules.monitoring.coreos.com`, read from the API server on every
   render, pruned to the `spec` subtree (`status` and `metadata` are not
   authored). This satisfies the requirement that the prompt always reflects the
   current CRD rather than a vendored copy.
2. **Required labels and namespace** — read from `spec.ruleSelector` and
   `spec.ruleNamespaceSelector` of the `Prometheus` CRs in the cluster (see
   [Which Prometheus instances select rules](#which-prometheus-instances-select-rules)).
   This is load-bearing, not decoration: a rule created without the selected
   labels, or in the wrong namespace, is accepted by the API server and then
   **silently never evaluated**. The prompt states both explicitly, and skips
   instances whose `ruleSelector` is unset since those select nothing.
3. **Evaluation interval** — `spec.evaluationInterval` from the same CR, so the
   LLM picks a `for` value spanning at least two evaluations.
4. **Where to find metrics** — a pointer to `bng://metrics/available`, not the
   list itself, with an instruction to read it before writing an `expr` and
   never to invent a metric name. The prompt does carry the
   `source="<namespace>/<device>"` label convention, because that comes from
   `resolve_device_source` in `bng/common.py` and is not derivable from a list
   of metric names.
5. **Documentation — retrieved, never vendored.** No link registry is kept in
   the codebase. Two sources cover it:

   - **Links already in the CRD.** The operator embeds canonical documentation
     URLs in the schema's own `description` fields — the alerting-rules page on
     `prometheus.io` and the Kubernetes API conventions. These arrive from the
     cluster, are versioned with the installed operator, and are maintained
     upstream, so the prompt harvests them from the `description` fields and
     surfaces them.
   - **Everything else, fetched by the client.** PromQL function reference,
     alerting best practices, and the operator API reference are not in the
     CRD. The prompt instructs the LLM to
     retrieve them with whatever web capability its client has, naming the
     authoritative sources — `prometheus.io/docs` and
     `github.com/prometheus-operator/prometheus-operator` at the version from
     the `operator.prometheus.io/version` annotation — rather than giving deep
     links.
   Clients without web access are only partly covered, and the prompt must say
   so rather than imply otherwise. PromQL syntax, functions,
   and idiom are **not** available offline.

   To mitigate this somehow we are providing:

   - `bng://metrics/available` — which metric names exist.
   - `bng://alerts/configured` — the expressions of rules already deployed,
     which are working PromQL examples for these exact metrics.
   - **PromQL cheat sheet** - see below.

6. **PromQL cheat sheet** — a curated set of widely used expressions and
   functions carried in the prompt body:

    | Category | Function / Concept | Description | Example Query |
    | :--- | :--- | :--- | :--- |
    | **Selectors** | Exact Match | Filter by exact label value | `up{job="node_exporter"}` |
    | **Selectors** | Negative Match | Exclude exact label value | `up{job!="node_exporter"}` |
    | **Selectors** | Regex Match | Filter by regex matching | `container_memory_usage_bytes{pod=~"backend-.*"}` |
    | **Selectors** | Negative Regex | Exclude by regex matching | `http_requests_total{status!~"2.."}` |
    | **Time Modifiers** | Instant Vector | Current value of a time series | `metric_name` |
    | **Time Modifiers** | Range Vector | Values over a specific time window (`s`, `m`, `h`, `d`) | `metric_name[5m]` |
    | **Time Modifiers** | Time Shift | Shift the evaluation time to the past | `metric_name offset 1h` |
    | **Rates** | `rate()` | Per-second average rate of increase (handles resets) | `rate(gnmi_interface_in_octets_total[5m])` |
    | **Rates** | `irate()` | Per-second rate based on last two points (good for spikes) | `irate(node_cpu_seconds_total{mode="user"}[5m])` |
    | **Rates** | `increase()` | Total absolute increase over a time window | `increase(http_requests_total[1h])` |
    | **Aggregation** | `sum()` | Sum values over dimensions | `sum by (pod) (container_memory_working_set_bytes)` |
    | **Aggregation** | `count()` | Count the number of active series | `count by (instance) (up == 1)` |
    | **Aggregation** | `topk()` | Top N elements by value | `topk(5, sum by (path) (rate(fastapi_requests_duration_seconds_sum[5m])))` |
    | **Histograms** | `histogram_quantile()` | Calculate percentiles from buckets | `histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))` |
    | **Math** | Error Rate % | Calculate ratio using binary operators | `sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m])) * 100` |
    | **Math** | Memory Usage % | Arithmetic between vectors | `(node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / node_memory_MemTotal_bytes * 100` |
    | **Advanced** | `predict_linear()` | Predict future values based on trend | `predict_linear(node_filesystem_free_bytes[1h], 4 * 3600) < 0` |
    | **Advanced** | `changes()` | Count value changes over time | `changes(process_start_time_seconds[1h])` |
    | **Advanced** | Subqueries | Run range queries on results of an inner query | `quantile_over_time(0.99, rate(gnmi_packet_drops_total[5m])[1h:1m])` |

   Content is **derived from the PromQL this controller already runs** in
   `bng/tools.py`, so the patterns are proven against these metrics rather than
   invented, so provide as additional examples:

   | Pattern | Form | Note |
   | --- | --- | --- |
   | Label selector | `metric{source="ns/device"}`, `source=~".*bng1"` | The `source` convention from item 4 |
   | Gauge threshold | `state_system_cpu_summary_usage_cpu_usage > 80` | No `rate()` on a gauge |
   | Counter rate | `rate(counter[2m])`, `increase(counter[1h])` | Window ≥ 4× `scrapeInterval`; `30s` scrape means `2m` minimum |
   | Utilisation | `allocated / total * 100 > 90` | Guard `total` being 0 |
   | Aggregation | `sum by (source) (...)`, `max by (source, card, fp) (...)` | Keep `source` so alerts identify the device |
   | Absence | `absent_over_time(metric[10m])` | The device-down idiom already used by `bng_device_unavailability_map` |
   | Label rewrite | `label_replace(...)` | Normalising `source` back to `namespace/name` |
   | Trend | `predict_linear(metric[1h], 4*3600) > total` | Capacity-exhaustion forecasting |
   | Combining | `and`, `or`, `unless` | Suppressing an alert when another condition holds |
   | Annotation templating | `{{ $labels.source }}`, `{{ $value }}`, `{{ printf "%.1f" $value }}`, `{{ humanize $value }}` | `$labels`/`$value` are only valid in annotations and labels |

7. **Authoring rules** — every rule sets `alert` and never `record`; `for` at
   least 2× the evaluation interval; `severity` label plus `summary` and
   `description` annotations.
8. **The call to make** — emit a complete manifest and pass it to
   `create_prometheus_alert_rule`.

`bng-get-alert-rules` (argument: `namespace: str = ""`) is small: it directs the
LLM to read `bng://alerts/configured` when `namespace` is empty, or
`bng://alerts/configured/{namespace}` otherwise, and to report which rules are
selected by a Prometheus instance and which are inert because their labels do
not match.

When the API server is unreachable raises `ErrorResponse(error="kubernetes_unreachable")`
as MCP error response and update application readiness to `False`
(to be implmented later on together with liveness and startup probes).

### Tool contract

```python
async def create_prometheus_alert_rule(rule: str, ctx: Context) -> AlertRuleApplyResult
```

`rule` is a complete `PrometheusRule` manifest as YAML or JSON. Accepting a
string rather than a typed model keeps the tool schema trivial and puts the
whole CRD within reach — the prompt is what makes the LLM produce it correctly.

Steps, in order, aborting on the first failure:

1. Parse the string (`yaml.safe_load` handles both YAML and JSON) and check
   `apiVersion: monitoring.coreos.com/v1` and `kind: PrometheusRule`. Every rule
   must set `alert`; one setting `record` is rejected as out of scope. Nothing
   downstream would catch it — the CRD accepts both and the dry-run in step 4
   would pass.
2. Default `metadata.namespace` to `Settings.k8s_namespace` when absent.
3. Validate each `expr` against live Prometheus with an instant query in the future - now on hold
    A parse error must be reported as such rather than as a
   generic query failure, which needs `error_type` plumbed through
   `PrometheusClientError` (today the `errorType` from the response body is
   discarded).
4. Submit with `dryRun=All` and `fieldValidation=Strict`, so a misspelled field
   becomes an error instead of being silently dropped.
5. Create the object. `get_namespaced_custom_object()` first: if it does not
   exist, `create_namespaced_custom_object()`; if it does,
   `delete_namespaced_custom_object()` then `create_namespaced_custom_object()`.
   The tool always sends a complete object, so replacing by recreate is simpler
   than a patch and avoids server-side-apply merge semantics. Refuse when the
   existing object is [Flux-managed](#existing-rules-and-their-ownership) rather
   than deleting it.
6. Return `AlertRuleApplyResult`: name, namespace, groups/rules applied, whether
   the labels match a Prometheus `ruleSelector` (i.e. will it actually be
   evaluated), and any warnings.

Guarded by `Settings.alerts_write_enabled`, default `False`. With it off the
tool raises `ErrorResponse(error="write_disabled")` before touching the cluster;
both prompts and both resources keep working. Annotations: `readOnlyHint=False`,
`idempotentHint=True` — the first use of `ToolAnnotations` in this codebase.

### Open decisions

1. **Direct apply vs GitOps.** Flux already owns rules in `nok-bng`. An
   alternative to writing to the API server is for the tool to emit a reviewed
   YAML artifact or open a Gitea pull request (a Gitea service is present in the
   cluster) and let Flux apply it — safer and auditable, but not interactive.
   The two can coexist: direct apply for the lab, artifact emission for
   production.
2. **Whether the tool should inject the required labels** when the supplied
   manifest omits them, or reject the manifest and make the LLM correct it.
   Injecting guarantees the rule is evaluated; rejecting keeps the tool honest
   about applying exactly what it was given.
3. **Deletion.** Out of scope as specified, which means the LLM can create rules
   it cannot remove.
