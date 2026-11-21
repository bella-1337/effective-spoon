# Getting started

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

Follow steps 1–6 in order for a first deployment. Install the
[prerequisites](../README.md#prerequisites) first.

Shell variables used below:

```sh
export CHART=deploy/helm/litellm-stack
export NS=litellm
export RELEASE=litellm-stack
```

Name the release `litellm-stack`. The chart builds resource names from the
release name and drops the chart name when the release name already contains
it, so this gives short names such as `litellm-stack-postgres` and the Secret
`litellm-stack-secrets`. A release named `litellm` produces
`litellm-litellm-stack-postgres`.

## 1. Publish the in-house images

Four images are built from the upstream repository and are not published by any
vendor. Build and push them first, then point the chart at your registry. Follow
[In-house container images](../images/README.md); the chart cannot install
without them. `lens` is only needed when `lens.enabled` is true.

## 2. Prepare the namespace and secrets

```sh
kubectl create namespace "$NS"
```

Create the TLS Secret named by `ingress.tls.secretName` (default
`litellm-stack-tls`) in the same namespace, unless cert-manager will issue it:

```sh
kubectl create secret tls litellm-stack-tls \
  --cert=fullchain.pem --key=privkey.pem --namespace "$NS"
```

For a production release, create the credentials Secret yourself and set
`secrets.existingSecret`. It must contain every key listed under
`secrets.values` in `values.yaml`. See
[Secrets, TLS, and ingress](secrets-and-ingress.md#credentials).

For a trial release, leave `secrets.existingSecret` empty. The chart renders one
Secret and generates a value for every blank key. Four values still need real
input because they are credentials for external services:

| Key | Why |
| --- | --- |
| `OPENAI_API_KEY` | Generated as the placeholder `replace_with_your_openai_api_key` so requests fail closed. |
| `D3_DECISION_API_KEY` | Must match the key the remote GPU host expects. |
| `BGE_M3_API_KEY` | Must match the key the remote GPU host expects. Dev gateway only. |
| `LANGFUSE_INIT_USER_PASSWORD` | Blank means no initial Langfuse user is created. Langfuse only. |

## 3. Write a values file

Start from the defaults and override only what differs. This is a minimal
example with the production gateway, the default observability platform, and
SearXNG:

```yaml
global:
  domain: ai.corp.example
  imageRegistry: registry.inhouse.example
  imagePullSecrets:
    - inhouse-registry

secrets:
  values:
    OPENAI_API_KEY: ""      # set from a secret store, not this file

ingress:
  className: nginx
  tls:
    secretName: litellm-stack-tls
```

Hostnames default to `<service>.<global.domain>`: `llm`, `llm-dev`,
`opensearch`, `grafana`, `langfuse`, `langfuse-s3`, and `lens`. Override an
individual name with the component's `host` value or `ingress.hosts.*`. Every
host must be covered by the TLS certificate and resolve to the ingress.

Enable optional components explicitly. Nothing in the core depends on them:

| Component | Values |
| --- | --- |
| Development gateway | `litellmDev.enabled: true` |
| Seed data | `demo.enabled: true` (requires a gateway) |
| Xberg MCP sandbox | `xberg.enabled: true` |
| Grafana and Loki | `grafana.enabled: true` |
| Langfuse | `langfuse.enabled: true`, plus `langfuse.webHost` and `langfuse.s3Host` |
| LiteLLM Lens | `lens.enabled: true` |
| Image scanning | `security.enabled: true` |

The complete list is in the [values reference](values-reference.md).

## 4. Validate the chart

```sh
helm lint "$CHART" -f my-values.yaml
helm template "$RELEASE" "$CHART" --namespace "$NS" -f my-values.yaml > /dev/null
```

Rendering fails early on configuration the chart refuses to start:

- `demo.enabled` without an enabled gateway;
- a Lens service, admin, and gateway token that are not three different values;
- `xberg.egress.dns.enabled` without `xberg.egress.dns.clusterIP`;
- an enabled workload that no network-policy zone covers;
- a values key the schema does not know (`values.schema.json` rejects unknown
  top-level keys).

[Validation](validation.md) describes the deeper checks, including schema
validation of the rendered manifests.

## 5. Install

Install **without** `--wait`:

```sh
helm install "$RELEASE" "$CHART" \
  --namespace "$NS" -f my-values.yaml
```

The chart relies on post-install hook Jobs to finish the setup: the OpenSearch
certificates, the PostgreSQL databases and roles, and the OpenSearch cluster
configuration. `--wait` makes Helm wait for every workload to be ready before it
runs those hooks, and OpenSearch cannot become ready until the certificate Job
has published its Secret, so a release installed with `--wait` is expected to
time out. This has not been tested on a cluster. Without `--wait`, the hooks run
right after the resources are created, in the order below, and the pods that
were waiting for their output then start.

| Order | Job | Purpose |
| --- | --- | --- |
| −5, before install | `langfuse-preflight`, `lens-preflight` | Refuse to start a half-configured profile |
| 0 | `opensearch-certs` | Create or renew the CA and certificates, publish them as a Secret |
| 10 | `postgres-init` | Create the databases and roles |
| 15 | `pgvector-migrate`, `grafana-db-init` | Migrate the vector store, create the Grafana role |
| 18 | `pgvector-access-init` | Grant access to the vector store |
| 20 | `opensearch-setup` | Configure OpenSearch security, templates, and alerting |
| 25 | `langfuse-clickhouse-cleanup` | Trim ClickHouse system logs |
| 30 | `opensearch-dashboards-setup` | Configure Dashboards |
| 35 | `litellm-demo-data-*` | Seed demo data (first install only) |
| 40 | `image-security-monitor-initial` | First image scan (first install only) |

Gateway pods can restart a few times on a first install until `postgres-init`
has finished. Kubernetes restarts them automatically.

### Known limitation: preflight hooks and generated secrets

Helm runs pre-install hooks before it creates the chart's regular resources. The
Langfuse and Lens preflight Jobs read the credentials Secret, so on a first
install with `secrets.existingSecret` empty the Secret does not exist yet and the
Job pod cannot start. If you enable `langfuse` or `lens`, create the Secret
beforehand and set `secrets.existingSecret`. Upgrades are not affected because
the Secret already exists. This follows from Helm's hook ordering and has not
been tested on a cluster.

## 6. Verify the release

```sh
kubectl get pods,jobs --namespace "$NS"
kubectl rollout status deployment/"$RELEASE"-litellm-prod --namespace "$NS"
kubectl rollout status statefulset/"$RELEASE"-opensearch --namespace "$NS"
```

Every hook Job should show `Completed` while it runs; with the default
`jobs.hooks.deletePolicy`, a Job is removed when the next run of the same hook
starts, so completed Jobs stay visible until then.

Read the generated gateway key and check the gateway through the ingress:

```sh
LITELLM_MASTER_KEY=$(kubectl get secret "$RELEASE"-secrets --namespace "$NS" \
  -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d)

curl -sS https://llm.ai.corp.example/health/liveliness
curl -sS -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  https://llm.ai.corp.example/v1/models
```

Use `existingSecret`'s name instead when you supplied one. Open
`https://opensearch.ai.corp.example` for OpenSearch Dashboards.

If a pod stays `ContainerCreating`, describe it. A missing Secret or ConfigMap
named in the events points at a hook that has not finished or failed:

```sh
kubectl describe pod <pod> --namespace "$NS"
kubectl logs job/"$RELEASE"-opensearch-certs --namespace "$NS"
```

## Stop, restart, and remove

Scale a workload to zero to stop it without losing data, for example:

```sh
kubectl scale deployment/"$RELEASE"-litellm-prod --replicas=0 --namespace "$NS"
```

A later `helm upgrade` restores the replica count from the values. To remove the
release and what it leaves behind, see
[Upgrades, rollback, and removal](upgrades-and-rollback.md#removal).
