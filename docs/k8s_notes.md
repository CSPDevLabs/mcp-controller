# K8s implementation

## pyproject.toml

kubernetes-asyncio>=33.3.0 (runtime, matches your kind 1.33 cluster)
datamodel-code-generator>=0.27.0 (dev, for the generation pipeline)

## src/mcp_controller/core/_k8s_base.py

_K8sModel — base class with alias_generator=to_camel, populate_by_name=True, extra="ignore". Used by --base-class in codegen so 
every generated class gets these behaviours automatically.

## scripts/gen_k8s_types.py — the generation pipeline

Extracts openAPIV3Schema from CRD YAMLs
Hoists nested objects to named $defs (the mechanism that controls class names — NetworkDeviceTargetSpec, SdcioSchema, SshJumpHost, etc.)
Injects ObjectMeta and the shared Reachability enum
Strips x-kubernetes-* extensions (invalid JSON Schema)
Calls datamodel-codegen, then strips the spurious Model(RootModel) wrapper
Re-run with python `scripts/gen_k8s_types.py` whenever a CRD schema changes

## src/mcp_controller/core/k8s_types.py — generated

`ObjectMeta`, `Reachability`, `NetworkDeviceTarget`/`Spec`/`Status`, `NetworkHostTarget`/`Spec`/`Status`, `Sdcio`, `Gnmic`, `SdcioSchema`, `Ssh`, `SshJumpHost`

## src/mcp_controller/core/kubernetes_client.py

`CRDDescriptor` — frozen dataclass; add one constant to support a new CRD
`KubernetesClient` — lazy connect, in-cluster/kubeconfig auto-detect, async with support
Generic `list_custom_objects` / `get_custom_object` (label + field selector)
Typed `list_network_device_targets`, `get_network_device_target`, `list_network_host_targets`, `get_network_host_target`
`KubernetesClientError` / `KubernetesNotFoundError` for clean error handling in resource/tool handlers