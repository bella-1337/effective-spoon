# LiteLLM Stack Architecture

## Purpose

This repository deploys a local, Docker Compose-based LiteLLM platform with:

- locally trusted HTTPS termination;
- separate production and development LiteLLM profiles;
- PII masking through Microsoft Presidio;
- PostgreSQL-backed LiteLLM state and spend records;
- optional Langfuse tracing backed by PostgreSQL, ClickHouse, Redis, and MinIO;
- D3-assisted coding routing, Qdrant-backed semantic caching, and
  pgvector-backed shared RAG;
- allowlisted Xberg MCP document extraction through an egress proxy;
- metrics scraped by Prometheus;
- OpenSearch and OpenSearch Dashboards (default) as the multi-user store and UI
  for container logs, Prometheus metrics, LiteLLM usage data, alerting, and
  security audit trails;
- optional Grafana, Loki, and a Loki-specific Alloy collector (`grafana`
  profile) for operators who still want the Grafana dashboards;
- optional Langfuse (`langfuse` profile) for LLM-specific tracing workflows
  beyond the default OpenSearch Trace Analytics pipeline;
- optional LiteLLM Lens (`lens` profile), embedded in the LiteLLM dashboard,
  for AI review of agent traces, backed by its own ClickHouse.

The bundled `mkcert` configuration is for a trusted local workstation or
private network. It is not a public Internet TLS solution.

## Topology

```text
                               +--------------------------+
                               | Browser / API client     |
                               +------------+-------------+
                                            | HTTPS :443
                               +------------v-------------+
                               | Traefik (TLS / routing)  |
                               +--+----------+---------+--+
                                  |          |         |
             +--------------------+          |         +-------------------------+
             |                               |                                   |
 +-----------v-----------+   +---------------v------------+   +------------------v------+
 | LiteLLM prod / dev    |   | OpenSearch Dashboards      |   | Grafana (optional,      |
 | profile-selected      |   | multi-user, tenants, RBAC  |   | grafana profile)        |
 +--+---------+------+---+   +---------------+------------+   +----+-------------+------+
    |         |      |                       | HTTPS (private CA)  |             |
    |  Presidio      | /metrics      +-------v--------+            |       +-----v----+
    |                |               | OpenSearch     |<--+        |       | Loki     |
 +--v-------------+  |               | security, ISM, |   |        |       +-----^----+
 | PostgreSQL     |  |               | alerting, SQL  |   |        |             |
 | prod + dev DBs |  |               +--^---------^---+   |        |       +-----+------+
 +--+-------------+  |                  |         |       |        |       | alloy-loki |
    |         +------v------+   +-------+------+  |  +----+------+ |       +------------+
    |         | Prometheus  |-->| opensearch-  |  |  | Data      | |
    |         | (all jobs)  |   | bridge       |  |  | Prepper   | |
    |         +------^------+   +-------^------+  |  +----^------+ |
    |                |                  |         |       | OTLP   |
    +----------------+------------------+         |  +----+------+ |
     exporters, cAdvisor, blackbox   read-only    |  | Alloy     | |
                                      SQL         |  | Docker    | |
                                                  |  | logs      | |
                                                  |  +-----------+ |
                                  Prometheus direct query (PPL) <---+
```

## Network Boundaries

Dedicated Docker networks separate external routing, internal service traffic,
Docker metadata access, image-security monitoring, and untrusted fetch egress.

| Network | Members | Purpose |
| --- | --- | --- |
| `edge` | Traefik, OpenSearch Dashboards, optional Grafana, Langfuse web/MinIO, and Lens, LiteLLM prod/dev | Services reachable through Traefik routes |
| `internal` | Trusted application, data, and observability services, including SearXNG | Databases, telemetry, Presidio, SearXNG (internal-only, reached through LiteLLM), and service-to-service calls |
| `xberg-sandbox` | Xberg, its DNS and egress proxies, LiteLLM prod/dev | Internal-only MCP traffic; Xberg has no direct route to `internal` or the Internet |
| `lens-storage` | Optional Lens and its ClickHouse store | Internal-only trace storage; no other service, including Prometheus, can reach Lens ClickHouse |
| `egress` | Xberg DNS and egress proxies | Internet route available only to the allowlist-only DNS resolver and the HTTPS-only destination-filtering proxy |
| `traefik-monitor` | Traefik and its Docker socket proxy | Read-only Docker routing metadata |
| `alloy-monitor` | Alloy and its Docker socket proxy | Read-only Docker discovery and log metadata |
| `security-monitor` | Image scanner and its Docker socket proxy | Isolated image and container inventory |

Only Traefik publishes host ports `80` and `443`. OpenSearch, Data Prepper,
Prometheus, Loki, PostgreSQL, exporters, socket proxies, and the Xberg egress
proxy remain internal-only. `opensearch-certs` and `lens-preflight` run without
any network.

## Runtime Profiles

Shared services run without a profile. Select one or both LiteLLM proxies:

```sh
docker compose --profile prod up -d
docker compose --profile dev up -d
docker compose --profile prod --profile dev up -d
```

The root `docker-compose.yml` includes `compose.optional.yml`, which owns
Grafana/Loki, Langfuse, Lens, and image-security services and state. The core file
groups its services along the request path (edge, LiteLLM, guardrails and
tools, data stores, the dev model plane, observability); the optional file
groups them by profile. YAML anchors cannot cross the include boundary, so the
logging, image-label, and resource-tier fragments are duplicated, and the
maintenance contracts fail if the copies drift. Profiles remain
the runtime gate. The included module declares the three networks shared across
the file boundary once, because Compose include resources cannot be redeclared
by the parent. Optional services load operator values from `.env.optional` when
commands pass it after `.env`.

| Profile | Proxy | Database | Intended use |
| --- | --- | --- | --- |
| `prod` | `litellm-prod` | `litellm_prod` | Normal proxy configuration with Presidio PII guardrail |
| `dev` | `litellm-dev` | `litellm_dev` | Debug-oriented proxy configuration |
| `grafana` | `grafana`, `loki`, `alloy-loki` | read-only `grafana_ro` | Optional Grafana dashboards, Explore, and alert rules |
| `langfuse` | Langfuse web/worker, PostgreSQL, ClickHouse, Redis, MinIO | Langfuse-specific stores | Optional LLM trace UI, datasets, prompt management, and evaluations |
| `lens` | `lens`, `lens-clickhouse`, `lens-preflight` | Lens ClickHouse (`lens-clickhouse-data`) | Optional AI review of agent traces, findings, and datasets inside the LiteLLM dashboard; see [LiteLLM Lens](docs/lens.md) |
| `security` | Image scanner and read-only Docker socket proxy | Bind-mounted reports and state | Optional scheduled image inventory and vulnerability policy |

`postgres-init` is a required one-shot service, not redundant: it creates the
two LiteLLM databases on a fresh PostgreSQL volume. The optional
`grafana-db-init` job separately creates Grafana's read-only role.

Both LiteLLM proxies start through `litellm/modules/prepare_runtime_config.py`.
It keeps every optional integration off unless its profile's service resolves
on the Compose network: the Langfuse callbacks, and the LiteLLM Lens connection,
whose `LITELLM_LENS_*` variables it removes from the proxy process when Lens is
not running. An enabled integration with missing credentials fails closed.

## Request and PII Flow

1. A client connects to a configured hostname over HTTPS.
2. Traefik selects the matching LiteLLM, OpenSearch Dashboards, or optional
   Grafana, Langfuse, or Lens route. SearXNG has no route; it is reachable only through
   LiteLLM's authenticated `searxng-search` tool.
3. LiteLLM authenticates API requests with `LITELLM_MASTER_KEY`.
4. Requests for `coding-router` are classified by the remote authenticated D3-mini
   Decisions endpoint. LiteLLM selects a configured coding tier and falls back to
   `gpt-4o` if classification fails.
5. The production guardrail calls Presidio analyzer and anonymizer services.
6. LiteLLM stores request/spend telemetry in its profile-specific PostgreSQL
   database and emits Prometheus metrics.

Presidio uses internal service addresses:

```text
http://presidio-analyzer:3000
http://presidio-anonymizer:3000
```

Keep those addresses aligned with both the LiteLLM configuration and Prometheus
blackbox `/health` probes.

## Persistence

| Volume | Owner | Contents |
| --- | --- | --- |
| `pg-data` | PostgreSQL | LiteLLM production/development state and spend logs |
| `pgvector-data` | PostgreSQL + pgvector | Development shared knowledge chunks and embeddings |
| Qdrant tmpfs | Qdrant | Ephemeral development semantic-cache entries, capped at 1 GiB |
| `opensearch-data` | OpenSearch | Logs, metrics, usage copies, alert state, dashboards, troubleshooting notebooks, security index, audit log |
| `opensearch-ca-private` | `opensearch-certs` only | Private CA key that signs the cluster certificates |
| `opensearch-ca` | Dashboards, Data Prepper, bridge | Public CA certificate for verified TLS to OpenSearch |
| `opensearch-node-certs` | OpenSearch | Node certificate and key |
| `opensearch-admin-certs` | `opensearch-setup` | Security super-admin client certificate |
| `alloy-data` / `alloy-loki-data` | Alloy collectors | Docker log read positions and queues |
| `grafana-data` | Grafana (optional) | Users, sessions, and provisioned dashboard state |
| `prometheus-data` | Prometheus | Metrics time series |
| `prometheus-secrets` | Prometheus | Non-root-readable copy of the LiteLLM metrics credential |
| `loki-data` | Loki (optional) | Project-scoped Docker log history |
| `langfuse-postgres-data` | Langfuse PostgreSQL (optional) | Langfuse relational state |
| `langfuse-clickhouse-data` | ClickHouse (optional) | Langfuse trace and analytics data plus bounded 7/30-day incident diagnostics |
| `langfuse-clickhouse-logs` | ClickHouse (optional) | Warning-or-higher ClickHouse logs, rotated at 100 MB with three files per channel |
| `langfuse-minio-data` | MinIO (optional) | Langfuse events and media objects |
| `langfuse-redis-data` | Redis (optional) | Langfuse queues |
| `lens-clickhouse-data` | Lens ClickHouse (optional) | Lens agent traces, investigations, findings, datasets, sessions, recorded gateway costs, and Keeper coordination state |

The D3-mini model and its cache live on a separate GPU host, deployed by the
separate [`gpu-host/`](gpu-host/README.md) Compose project, and are not part of
this Compose project's persistence. That host must expose only authenticated
Decisions routes over verified TLS and should firewall access to the LiteLLM host.
Both LiteLLM containers mount `GPU_HOST_CA_BUNDLE`, the standard public roots plus
the GPU host's dedicated mkcert CA, as `SSL_CERT_FILE`, so private-PKI trust does
not break provider TLS. `scripts/build-gpu-host-ca-bundle.py` builds it.

The development BGE-M3 embedding model and its cache live on the same remote GPU
host, served by Text Embeddings Inference from the same `gpu-host/` project
behind the same TLS proxy. TEI's API key guards every compute route, and only
`litellm-dev` calls it. Keep the link on a private network and
firewall it to the LiteLLM dev host. See
[Remote BGE-M3 embeddings](docs/search-extraction-and-caching.md#remote-bge-m3-embeddings).

The PostgreSQL default database is `litellm_prod`; `postgres-init` creates
`litellm_dev`. Do not reintroduce an unused default `litellm` database.

## Observability Data Flow

OpenSearch is the default observability store. Grafana OSS cannot restrict
data sources or dashboards per user, while the OpenSearch security plugin
provides internal users, roles, document/field-level security, read-only
users, and Global/Private tenants without a commercial license.

### Metrics

Prometheus scrapes:

- LiteLLM prod and dev metrics using the master-key credential file;
- Traefik, PostgreSQL exporter, node exporter, and cAdvisor;
- Blackbox `/health` probes for Presidio and SearXNG;
- with the `lens` profile, a blackbox `/health/ready` probe of Lens (job
  `lens`), discovered through DNS so it disappears with the profile.

`opensearch-bridge` evaluates `opensearch/tools/metrics.yml` against Prometheus
every minute and stores the results in `litellm-metrics-*`:

- **expressions** reproduce every Prometheus panel of the repository's Grafana
  dashboards plus the core host, container, PostgreSQL, Traefik, probe, and
  security panels, including histogram quantiles;
- **raw** selectors mirror the LiteLLM, Traefik, PostgreSQL, blackbox,
  scrape-health, image-security, and backup series unchanged.

Every other series stays queryable through the `prometheus` direct-query data
source registered in OpenSearch (Observability → Metrics, PPL). Do not remove
exporters merely because a panel is currently empty.

### Request traces

LiteLLM's OpenTelemetry v2 integration exports request, guardrail, model-call,
and database spans over OTLP/gRPC to Data Prepper (`data-prepper:21890`), which
writes OpenSearch Trace Analytics indices (`otel-v1-apm-span-*` and
`otel-v1-apm-service-map`). When the `langfuse` profile is enabled, Langfuse
receives an additional copy through the `langfuse_otel` callback; otherwise the
runtime config removes that callback and its attribution hook. Message content
capture stays disabled, and only `litellm_operator` can read the OpenSearch span
indices. The proxies do not depend on Data Prepper at startup; spans are dropped
while it is unavailable.

LiteLLM Lens is not part of this pipeline. With the `lens` profile, agents send
their own OTLP traces directly to Lens over HTTPS, and the proxies export only
content-free spend records to Lens for cost matching.

### Alerting

The Grafana provisioning files under `grafana/alerting/` remain the single
source of alert rules. The bridge evaluates each rule's PromQL condition every
minute (`litellm-alert-state-*`), and `opensearch-setup` creates one OpenSearch
Alerting monitor per rule that fires only after the rule's complete `for`
duration, honoring `noDataState` and `execErrState`. Four additional monitors
fire when alert evaluation, metrics, container logs, or LiteLLM spans stop
arriving.

### Database telemetry

The bridge copies non-content LiteLLM spend, provider-error, and audit rows into
monthly `litellm-spend-*`, `litellm-provider-errors-*`, and `litellm-audit-*`
indices and refreshes user, team, and key snapshots in `litellm-entities`,
using the read-only `opensearch_ro` PostgreSQL role. Prompts, responses,
request metadata, client IPs, key hashes, and audit before/after values are
never copied. The optional Grafana profile still queries PostgreSQL directly
through `litellm-pg` and `litellm-pg-dev`.

Failed unknown-model requests are recorded in `LiteLLM_SpendLogs` with
`status = 'failure'`; they are not necessarily written to `LiteLLM_ErrorLogs`.

### Container logs

Grafana Alloy discovers Docker containers through the Docker API, keeps only the
current Compose project, joins multi-line entries (stack traces, pretty-printed
JSON), strips ANSI colours, redacts `q=`/`query=` URL parameters, and attaches a
normalized `level`. It ships stdout/stderr over OTLP to Data Prepper, which sets
`severityText`, parses Traefik's JSON access logs into `http.*` fields (client
addresses and headers are never logged), and writes `litellm-logs-*`. The optional `alloy-loki` collector sends the
same streams to Loki for Grafana. Both collectors reach Docker through the
POST-disabled `alloy-docker-proxy`. Query logs in OpenSearch Dashboards
(Discover, the **Container Logs** dashboard, or PPL), for example:

```text
log.attributes.compose_service:"litellm-prod" and body:error
source = litellm-logs-* | where `log.attributes.stream` = 'stderr' | stats count() by `log.attributes.compose_service`
```

With the Grafana profile, Loki uses the same labels:

```logql
{compose_project="litellm-stack", compose_service="litellm-prod"}
```

The project filter is a security boundary on shared Docker hosts. Do not remove
it or collect every Docker project.

## TLS and Secrets

Runtime secrets are intentionally ignored by Git:

- `.env`;
- the certificate and private-key paths configured by `TRAEFIK_TLS_CERT_FILE`
  and `TRAEFIK_TLS_KEY_FILE`;
- `litellm/secrets/litellm-master-key`.

OpenSearch uses a private CA generated by the `opensearch-certs` one-shot
service. Node and admin certificates are renewed automatically 30 days before
expiry when the service runs; HTTP and transport TLS are always enabled and all
clients verify the CA. Only the admin certificate holder (`opensearch-setup`)
can change the security configuration outside the Dashboards security UI.

The on-prem certificate delivery process owns the host-side TLS files. Keep the
hostnames in the delivered certificate, `.env`, DNS, and local name resolution
consistent. `mkcert` is only a local-development fallback. The Prometheus
credential file must contain only `LITELLM_MASTER_KEY`.

## Operational Checks

Before declaring a change complete:

```sh
docker compose --profile prod --profile dev config --quiet
docker compose --env-file .env --env-file .env.optional \
  --profile prod --profile langfuse config --quiet
docker compose --env-file .env --env-file .env.optional \
  --profile prod --profile lens config --quiet
docker compose --profile prod --profile dev up -d --wait --wait-timeout 150
```

Then verify:

```sh
eval "$(python3 scripts/maintenance.py stack-env)"  # hostnames from .env
curl --fail "https://$LITELLM_PROD_HOST/health/liveliness"
curl --fail "https://$LITELLM_DEV_HOST/health/liveliness"
curl --silent --output /dev/null --write-out '%{http_code}\n' \
  "https://$OPENSEARCH_DASHBOARDS_HOST/api/status"   # expect 401 without credentials
curl --silent --output /dev/null --write-out '%{http_code}\n' \
  -H "Host: search.$STACK_DOMAIN" "https://$LITELLM_PROD_HOST/"   # expect 404: SearXNG is internal-only
```

Check active Prometheus targets rather than historical series:

```sh
docker compose exec -T prometheus wget -qO- \
  'http://localhost:9090/api/v1/targets?state=active'
```

Historical series remain until retention expires after a target is renamed or
removed. They do not imply an active configuration problem.
