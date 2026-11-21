# Values reference

[← Back to the README](../README.md#documentation).

This page maps each top-level key in
[`values.yaml`](https://github.com/bella-1337/symmetrical-engine/blob/main/deploy/helm/litellm-stack/values.yaml) to what it controls. The file
itself is the authority on defaults and carries comments for each block;
[`values.schema.json`](https://github.com/bella-1337/symmetrical-engine/blob/main/deploy/helm/litellm-stack/values.schema.json) rejects unknown
top-level keys, so a misspelled key fails `helm lint` instead of being ignored.

Resource values (`resources`, `persistence.size`) are the chart's starting
points, derived from the upstream Compose resource tiers. Size them for your load
using the upstream
[hardware sizing guide](https://github.com/bella-1337/effective-spoon/blob/master/docs/hardware-sizing.md),
and load-test before relying on them.

## Conventions

| Term | Meaning |
| --- | --- |
| Core | Rendered by default; other components assume it. |
| Optional | Off unless its `enabled` value is `true`. Nothing in the core depends on it. |
| Empty string | Use the documented fallback (a derived hostname, the cluster default StorageClass, the image's own registry). |

## Global settings

| Key | Default | Controls |
| --- | --- | --- |
| `global.imageRegistry` | `""` | Prefix for every image, including third-party ones. |
| `global.imagePullSecrets` | `[]` | Pull Secrets added to every pod and to the image scanner. |
| `global.imagePullPolicy` | `IfNotPresent` | Pull policy for every container. |
| `global.storageClass` | `""` | StorageClass for every volume. A component's own `storageClass` wins. |
| `global.domain` | `example.test` | Base of every default hostname, `<service>.<domain>`. |
| `global.timezone` | `Europe/Berlin` | Not used by any template yet; changing it has no effect on the rendered manifests. |
| `global.nodeSelector`, `tolerations`, `affinity` | empty | Scheduling defaults for every workload. |
| `nameOverride`, `fullnameOverride` | `""` | Resource naming. |

## Images

`useDigest` (default `true`) makes the chart reference an image by digest when
one is set. `images.<name>` entries hold `registry`, `repository`, `tag`, and
`digest`. The four in-house images (`opensearchTools`, `pgvector`, `lens`,
`imageSecurityMonitor`) ship with an empty `digest` and need your registry; see
[In-house container images](../images/README.md). Every other entry is a pinned
third-party image to mirror if your cluster cannot reach its registry.

## Secrets and ingress

| Key | Default | Controls |
| --- | --- | --- |
| `secrets.existingSecret` | `""` | Use your own Secret and ignore the rest of this block. |
| `secrets.generateMissing` | `true` | Generate blank values on first install. |
| `secrets.values.*` | `""` | One entry per credential. |
| `ingress.enabled` | `true` | Render the Ingress. |
| `ingress.className` | `nginx` | Ingress class. |
| `ingress.annotations` | ingress-nginx set | Security headers, rate limits, body size. |
| `ingress.tls.enabled`, `ingress.tls.secretName` | `true`, `litellm-stack-tls` | Certificate Secret for every host. |
| `ingress.hosts.*` | `""` | Per-service hostname override. |

Details: [Secrets, TLS, and ingress](secrets-and-ingress.md).

## Gateways

| Key | Default | Controls |
| --- | --- | --- |
| `litellmProd.enabled` | `true` | Production gateway (core). |
| `litellmProd.replicaCount` | `2` | Replicas. |
| `litellmProd.autoscaling.*` | off | HorizontalPodAutoscaler (`minReplicas`, `maxReplicas`, CPU target). |
| `litellmProd.podDisruptionBudget.*` | on, `minAvailable: 1` | Disruption budget. |
| `litellmProd.topologySpread` | `true` | Spread replicas across nodes. |
| `litellmProd.d3DecisionApiBase` | `""` | Remote GPU host decisions endpoint. |
| `litellmProd.branding.*` | on | Admin UI company name and assets. |
| `litellmProd.storeModelInDB`, `useV2MigrationResolver`, `otelV2`, `otelTraceExporter`, `otelCaptureMessageContent`, `forwardedAllowIps`, `requestTimeout` | see file | Proxy behaviour, mirroring upstream `.env.example`. |
| `litellmDev.enabled` | `false` | Development gateway; also renders the vector store, connector, and semantic cache. |
| `litellmDev.bgeM3ApiBase` | `""` | Remote GPU host embeddings endpoint. |
| `demo.enabled` | `false` | One-shot seed Jobs. Requires a gateway. |

## Guardrails and tools

| Key | Default | Controls |
| --- | --- | --- |
| `presidio.analyzer.*`, `presidio.anonymizer.*` | 1 replica each | Core PII masking services: replicas, workers, NLP engine, languages, resources. |
| `searxng.enabled` | `true` | Search service for the gateways. |
| `xberg.enabled` | `false` | Document-extraction MCP sandbox. |
| `xberg.egress.proxy.*` | on | Allow-listing Squid proxy. The only path out of the sandbox. |
| `xberg.egress.dns.*` | off | Optional DNS pinning. Needs `clusterIP`, set after a first install. |

## Data stores

| Key | Default | Controls |
| --- | --- | --- |
| `postgres.persistence.*` | `20Gi`, optional `existingClaim` | LiteLLM database volume. One instance serves the prod and dev databases. |
| `postgres.roi.*` | see file | ROI scenario values seeded by `postgres-init`. |
| `pgvector.persistence.*`, `pgvector.connector.*` | `20Gi`, 1 replica | Vector store and its connector (dev gateway). |
| `qdrant.persistence.*` | off | In-memory semantic cache unless persistence is enabled. |

## Observability

| Key | Default | Controls |
| --- | --- | --- |
| `prometheus.enabled` | `true` | Metrics; `retentionTime` `15d`, `retentionSize` `10GB`. |
| `prometheus.exporters.*` | `postgres`, `pgvector`, `blackbox` on | Exporters to deploy. |
| `prometheus.nodeExporter.enabled` | `false` | Optional node-exporter DaemonSet. |
| `prometheus.kubernetesTargets.enabled` | `false` | Scrape kubelet and kube-state-metrics if present. |
| `alloy.enabled` | `true` | Pod log collector sending to Data Prepper. |
| `opensearch.*` | enabled, 1 replica, `30Gi` | Cluster size, heap, retention in days, bridge interval, certificate validity and renewal window, alert channel. TLS and the security plugin are never disabled. |
| `opensearchDashboards.enabled`, `dataPrepper.enabled` | `true` | Default observability UI and trace pipeline. |
| `grafana.enabled` | `false` | Grafana with Loki; `grafana.loki.*` sets retention and volume. |
| `langfuse.enabled` | `false` | Langfuse with its PostgreSQL, ClickHouse, MinIO, and Redis. |
| `lens.enabled` | `false` | LiteLLM Lens with its own ClickHouse. |
| `security.enabled` | `false` | Scheduled Trivy scan: `schedule`, `severity`, `enforce`, report persistence. |

## Platform

| Key | Default | Controls |
| --- | --- | --- |
| `networkPolicies.enabled` | `true` | Default deny plus per-zone policies. See [Architecture](architecture.md#network-policies). |
| `jobs.hooks.enabled` | `true` | Not used by any template yet; setting it to `false` does not disable the hook Jobs, which the install depends on. |
| `jobs.hooks.deletePolicy` | `before-hook-creation` | Helm hook delete policy for every hook Job. |
