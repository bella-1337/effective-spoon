# Architecture

[← Back to the README](../README.md#documentation).

The chart ports the upstream
[Docker Compose stack](https://github.com/bella-1337/effective-spoon/blob/master/ARCHITECTURE.md)
to Kubernetes without changing what the stack does. This page records what is
the same, what changed, and where the port is weaker than the original.

## Components

| Area | Workloads | Enabled by |
| --- | --- | --- |
| Gateways | `litellm-prod` (2 replicas), `litellm-dev` (1) | `litellmProd.enabled` (default), `litellmDev.enabled` |
| Guardrails and tools | `presidio-analyzer`, `presidio-anonymizer`, `searxng`, `xberg` with `xberg-egress-proxy` and optional `xberg-dns` | Presidio always; `searxng.enabled` (default); `xberg.enabled` |
| Data | `postgres`, `vector-postgres`, `litellm-pgvector`, `qdrant` | PostgreSQL always; the vector store with the dev gateway |
| Metrics | `prometheus`, `postgres-exporter`, `pgvector-exporter`, `blackbox`, optional `node-exporter` | `prometheus.enabled` |
| Logs and traces | `alloy`, `data-prepper`, `opensearch`, `opensearch-dashboards`, `opensearch-bridge` | `alloy.enabled`, `opensearch.enabled`, `dataPrepper.enabled`, `opensearchDashboards.enabled` |
| Grafana profile | `grafana`, `loki`, `alloy-loki` | `grafana.enabled` |
| Langfuse profile | `langfuse-web`, `langfuse-worker`, `langfuse-postgres`, `langfuse-clickhouse`, `langfuse-minio`, `langfuse-redis` | `langfuse.enabled` |
| Lens profile | `lens`, `lens-clickhouse` | `lens.enabled` |
| Security profile | `image-security-monitor` CronJob | `security.enabled` |

Optional components never become dependencies of the core. The gateways start
without Langfuse and Lens, and enable those integrations only when the profile
and its credentials are present.

## What changed from Compose

| Compose | Kubernetes |
| --- | --- |
| Traefik with security-header and rate-limit middleware | One `Ingress`; headers and limits are annotations ([details](secrets-and-ingress.md#annotations)) |
| Docker socket proxies | Removed. Alloy reads pod logs with a Role limited to `get`, `list`, `watch` on pods and `get` on `pods/log` |
| `.env` and `.env.optional` | `values.yaml` and one Secret ([details](secrets-and-ingress.md#credentials)) |
| Named volumes | StatefulSet `volumeClaimTemplates`, or `existingClaim` where offered |
| One-shot init containers | Helm hook Jobs, ordered by weight |
| Shared certificate volumes | One certificate Secret published by a hook Job |
| Compose networks | `NetworkPolicy` per trust zone, below |
| cAdvisor | Not deployed; the kubelet already exposes it |
| Image-security monitor reading the Docker socket | A CronJob that scans the images this release renders |
| Build at `docker compose build` | Images pre-built and pushed ([guide](../images/README.md)) |

The Alloy log filters keep the original label names, including `compose_service`,
so the upstream OpenSearch dashboards and saved searches still work.

## Network policies

Compose isolated services with internal networks. A Kubernetes namespace is flat,
so the chart applies a default deny to every pod of the release and then one
policy per trust zone, each listing exactly the flows it needs. Examples:
Presidio accepts connections only from the gateways and the monitoring pods;
Data Prepper reaches only OpenSearch; the Xberg sandbox may reach only its
egress proxy.

The zone table in `templates/networkpolicies.yaml` is the single source of truth.
The render **fails** when an enabled workload is in no zone, so a new component
cannot be added without deciding who may talk to it. Set
`networkPolicies.enabled: false` only to debug.

The policies need a CNI that enforces `NetworkPolicy` (Calico, Cilium, and similar).
On one that does not, they are accepted and silently ignored.

## Hook order

Hook Jobs run before and after the workloads are created, ordered by weight; the
full table is in [Getting started](getting-started.md#5-install). In short:
certificates, then databases and roles, then OpenSearch configuration, then
Dashboards, then optional seed data and the first image scan.

## Known limitations

These follow from the design and have not been tested on a cluster.

- **Single-node data stores.** PostgreSQL, OpenSearch (`opensearch.replicas: 1`),
  and the other stateful services run as one pod, as in the Compose stack. There
  is no failover. Only the production gateway is replicated, with a
  PodDisruptionBudget and optional autoscaling.
- **Install without `--wait`.** The certificate Job is a post-install hook that
  OpenSearch depends on; see [Getting started](getting-started.md#5-install).
- **Preflight hooks and generated secrets.** The Langfuse and Lens preflight Jobs
  need the Secret before Helm has created it on a first install; see
  [Getting started](getting-started.md#known-limitation-preflight-hooks-and-generated-secrets).
- **Xberg DNS pinning.** The optional resolver needs its Service address, which
  exists only after a first install, so `xberg.egress.dns.clusterIP` is set in a
  second step. The NetworkPolicy plus the allow-listing proxy is the primary
  control.
- **Node settings.** The chart cannot set `vm.max_map_count`; see the
  [prerequisites](../README.md#prerequisites).
- **Compose-only maintenance scripts.** The upstream `maintenance.py`,
  `backup-databases.py`, and `restore-databases.py` target Docker Compose and do
  not run against this chart.
