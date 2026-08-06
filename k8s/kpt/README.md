# mcp-controller kpt package

Deploy with kpt (NetOpsKube-style):

```bash
# Preview rendered resources
kpt fn render k8s/kpt --output unwrap

# Apply
kpt live init k8s/kpt   # once
kpt live apply k8s/kpt
```

Tune values in `apply-setters.yaml` (image, namespace, Prometheus/Loki URLs), then re-render.

`ingress.yaml` is marked `local-config` and is not applied unless you remove that annotation.
