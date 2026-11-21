# Validation

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

Run these checks after any change to the chart or your values. None of them needs
a cluster. They do not replace an install on a test cluster.

```sh
export CHART=deploy/helm/litellm-stack
```

## 1. Lint

```sh
helm lint "$CHART"
helm lint "$CHART" -f my-values.yaml
```

`icon is recommended` is informational. Anything else should be fixed.
`values.schema.json` is applied here and by `helm template`; it rejects unknown
top-level keys.

## 2. Render the default and the full configuration

The default render covers the core. A second render with every optional component
enabled covers the rest, and catches templates that are only reached then.
Save this as `full.yaml`:

```yaml
litellmDev: {enabled: true, host: llm-dev.example.test}
demo: {enabled: true}
xberg:
  enabled: true
  egress:
    dns: {enabled: true, clusterIP: 10.96.0.53}
grafana: {enabled: true, host: grafana.example.test}
langfuse: {enabled: true, webHost: langfuse.example.test, s3Host: s3.example.test}
lens: {enabled: true, host: lens.example.test}
security: {enabled: true}
prometheus:
  nodeExporter: {enabled: true}
  kubernetesTargets: {enabled: true}
qdrant:
  persistence: {enabled: true}
```

```sh
helm template t "$CHART" --namespace stack > default.yaml
helm template t "$CHART" --namespace stack -f full.yaml > full-render.yaml
```

`xberg.egress.dns.clusterIP` is set only to satisfy the guard that requires it.
In a real release, install once and read the address from the `xberg-dns` Service.

## 3. Validate the manifests against the Kubernetes API

[`kubeconform`](https://github.com/yannh/kubeconform) checks every rendered
object against the API schema for a Kubernetes version:

```sh
kubeconform -strict -summary -kubernetes-version 1.30.0 default.yaml full-render.yaml
```

Use the minimum version you support (`kubeVersion` in `Chart.yaml` is 1.27).

## 4. Check duplicate keys in values

Helm silently keeps the last of two identical keys in a mapping, which hides
mistakes such as a repeated secret name:

```sh
uvx yamllint -d '{rules: {key-duplicates: enable}}' \
  deploy/helm/litellm-stack/values.yaml
```

## 5. Check references between objects

Linting and schema validation do not notice a Deployment that mounts a ConfigMap
the chart never renders. That kind of mistake only shows up as a pod stuck in
`ContainerCreating`. For each rendered pod template, confirm that:

- every ConfigMap, Secret, and PersistentVolumeClaim it mounts or reads exists in
  the render;
- every `secretKeyRef` key exists in the rendered Secret;
- every Service selector matches a pod, and every `targetPort` is exposed by it;
- every Ingress backend names a Service that exists;
- every ServiceAccount exists.

Run this on both renders. One reference is expected to be missing: the
`<release>-opensearch-certs` Secret, which the post-install certificate hook Job
creates at runtime. Anything else is a bug.

A past run of this check found a `grafana-alerting` ConfigMap rendered only when
Grafana was enabled while the OpenSearch setup Job also mounted it; see the
[changelog](../CHANGELOG.md).

## 6. Compare bundled files with upstream

The files under `files/` come from the upstream repository at the commit in
`Chart.yaml`. Compare them with a checkout of that commit when you change one
(clone it into your home directory and delete it afterwards, not under a
nearly-full root filesystem):

```sh
diff -r deploy/helm/litellm-stack/files/litellm /path/to/effective-spoon/litellm
```

The two Alloy files are expected to differ. They discover Kubernetes pods
instead of Docker containers and use `POD_NAMESPACE` and `RELEASE_NAME` for the
namespace and release filters.

## What these checks do not cover

- That images exist at the registry and digest the chart names.
- Admission controllers, PodSecurity levels, and your ingress controller's
  annotation policy.
- Runtime behaviour, including the hook order and the OpenSearch certificate
  hand-off. Install into a scratch namespace to test those.
