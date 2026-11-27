<!-- human-version-of: ARCHITECTURE.md sha256: dc2b52bacf4e334697ac7cb1c8217639f67cce97beed3c848c64ae5440d11f07 -->

# LiteLLM Stack Architecture

This page describes the parts of the stack and how they connect: networks,
profiles, the request path, storage, and telemetry. Read it before you change
a service, or when you need to know where a request or a log line goes.

## Purpose

This repository deploys the Sovereign LLM Gateway: a Docker Compose-based
LiteLLM platform locked to one data-sovereignty region.

- `DATA_SOVEREIGNTY_REGION` selects the region.
- At startup, only the cloud models named in the environment are wired into
  LiteLLM, each through a provider location inside that region. See
  [Data sovereignty and regional models](docs/data-sovereignty.md).

The stack always provides:

- region-locked, environment-configured models from Amazon Bedrock, Azure
  OpenAI, and Google Vertex AI, with none required;
- locally trusted HTTPS termination;
- separate production and development LiteLLM profiles;
- PII masking through Microsoft Presidio in English, German, and French, with
  optional language and personal-data detection by a local model;
- PostgreSQL-backed LiteLLM state and spend records;
- D3-assisted coding routing, Qdrant-backed semantic caching, and
  pgvector-backed shared RAG;
- allowlisted Xberg MCP document extraction through an egress proxy;
- metrics scraped by Prometheus;
- OpenSearch and OpenSearch Dashboards (default) as the multi-user store and UI
  for container logs, Prometheus metrics, LiteLLM usage data, alerting, and
  security audit trails.

Optional profiles add:

- `grafana`: Grafana, Loki, and a Loki-specific Alloy collector, for operators
  who still want the Grafana dashboards;
- `langfuse`: Langfuse tracing backed by PostgreSQL, ClickHouse, Redis, and
  MinIO, for LLM-specific tracing workflows beyond the default OpenSearch
  Trace Analytics pipeline;
- `lens`: LiteLLM Lens, embedded in the LiteLLM dashboard, for AI review of
  agent traces, backed by its own ClickHouse;
- `d3`: on-host D3-mini on the stack host's NVIDIA GPU, in place of the remote
  GPU host, for `coding-router` decisions;
- `docs`: a documentation site at `DOCS_HOST`
  (`gateway-deployment-docs.${STACK_DOMAIN}`), served by `zensical serve` from
  the deployed revision.

!!! warning
    The bundled `mkcert` configuration is for a trusted local workstation or
    private network. It is not a public Internet TLS solution.

## Topology

Clients connect to Traefik over HTTPS on port 443. Traefik routes them to
LiteLLM, OpenSearch Dashboards, or the optional Grafana. The lower half of the
diagram shows how metrics, logs, and traces reach OpenSearch (and, with the
`grafana` profile, Loki).

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
| `edge` | Traefik, OpenSearch Dashboards, optional Grafana, Langfuse web/MinIO, Lens, and the documentation site, LiteLLM prod/dev | Services reachable through Traefik routes |
| `internal` | Trusted application, data, and observability services, including SearXNG | Databases, telemetry, Presidio, SearXNG (internal-only, reached through LiteLLM), and service-to-service calls |
| `xberg-sandbox` | Xberg, its DNS and egress proxies, LiteLLM prod/dev | Internal-only MCP traffic; Xberg has no direct route to `internal` or the Internet |
| `lens-storage` | Optional Lens and its ClickHouse store | Internal-only trace storage; no other service, including Prometheus, can reach Lens ClickHouse |
| `egress` | Xberg DNS and egress proxies | Internet route available only to the allowlist-only DNS resolver and the HTTPS-only destination-filtering proxy |
| `traefik-monitor` | Traefik and its Docker socket proxy | Read-only Docker routing metadata |
| `alloy-monitor` | Alloy and its Docker socket proxy | Read-only Docker discovery and log metadata |
| `security-monitor` | Image scanner and its Docker socket proxy | Isolated image and container inventory |

What is exposed on the host:

- Only Traefik publishes host ports, `80` and `443`.
- OpenSearch, Data Prepper, Prometheus, Loki, PostgreSQL, exporters, socket
  proxies, and the Xberg egress proxy remain internal-only.
- `opensearch-certs` and `lens-preflight` run without any network.

## Runtime Profiles

Shared services run without a profile. Select one or both LiteLLM proxies:

```sh
docker compose --profile prod up -d
docker compose --profile dev up -d
docker compose --profile prod --profile dev up -d
```

### How the Compose files are split

The root `docker-compose.yml` includes `compose.optional.yml`. The optional
file owns the services and state of Grafana/Loki, Langfuse, Lens, on-host
D3-mini, the documentation site, and image security.

- The core file groups its services along the request path: edge, LiteLLM,
  guardrails and tools, data stores, the dev model plane, observability.
- The optional file groups its services by profile.
- YAML anchors cannot cross the include boundary. So the logging, image-label,
  and resource-tier fragments are duplicated, and the maintenance contracts
  fail if the copies drift.
- Profiles remain the runtime gate.
- The included module declares the three networks shared across the file
  boundary once, because the parent cannot redeclare Compose include
  resources.
- Optional services load operator values from `.env.optional` when commands
  pass it after `.env`.

| Profile | Proxy | Database | Intended use |
| --- | --- | --- | --- |
| `prod` | `litellm-prod` | `litellm_prod` | Normal proxy configuration with Presidio PII guardrail |
| `dev` | `litellm-dev` | `litellm_dev` | Debug-oriented proxy configuration |
| `grafana` | `grafana`, `loki`, `alloy-loki` | read-only `grafana_ro` | Optional Grafana dashboards, Explore, and alert rules |
| `langfuse` | Langfuse web/worker, PostgreSQL, ClickHouse, Redis, MinIO | Langfuse-specific stores | Optional LLM trace UI, datasets, prompt management, and evaluations |
| `lens` | `lens`, `lens-clickhouse`, `lens-preflight` | Lens ClickHouse (`lens-clickhouse-data`) | Optional AI review of agent traces, findings, and datasets inside the LiteLLM dashboard; see [LiteLLM Lens](docs/lens.md) |
| `d3` | `d3-mini`, `d3-mini-download`, `d3-mini-preflight` | Model weights (`d3-mini-models`) | Optional D3-mini on this host's NVIDIA GPU, reached by both gateways on the internal `d3-local` network; see [On-host D3-mini](docs/d3-mini-on-host.md) |
| `docs` | `docs` | None; the sources are built into the image | Optional documentation site at `DOCS_HOST`: `zensical serve` on the deployed revision, non-root and read-only on the `edge` network only, behind Traefik's GET/HEAD-only route and `docs-headers` middleware, without sign-in; see [Documentation site](docs/documentation-site.md#serve-the-site-from-the-stack) |
| `security` | Image scanner and read-only Docker socket proxy | Bind-mounted reports and state | Optional scheduled image inventory and vulnerability policy |

`postgres-init` is a required one-shot service, not a redundant one: it creates
the two LiteLLM databases on a fresh PostgreSQL volume. The optional
`grafana-db-init` job separately creates Grafana's read-only role.

### What happens when a proxy starts

Both LiteLLM proxies start through `litellm/modules/prepare_runtime_config.py`.
It does the following:

1. It adds the cloud models named in the environment to the static model list.
   `litellm/modules/sovereign_models.py` builds them, locked to
   `DATA_SOVEREIGNTY_REGION`.
2. It refuses to start the proxy when a configured provider lacks a credential
   or its location falls outside the region.
3. It keeps every optional integration off unless its profile's service
   resolves on the Compose network. This covers:
    - the Langfuse callbacks;
    - the LiteLLM Lens connection. When Lens is not running, it removes the
      `LITELLM_LENS_*` variables from the proxy process.

    An enabled integration with missing credentials fails closed.
4. It refuses a `D3_DECISION_API_BASE` that is not an HTTPS base URL, except
   `http://d3-mini:8000` for the on-host `d3` profile.

## Request and PII Flow

1. A client connects to a configured hostname over HTTPS.
2. Traefik selects the matching route: LiteLLM, OpenSearch Dashboards, or the
   optional Grafana, Langfuse, Lens, or documentation route. SearXNG has no
   route; it is reachable only through LiteLLM's authenticated
   `searxng-search` tool.
3. LiteLLM authenticates API requests with `LITELLM_MASTER_KEY`.
4. Requests for `coding-router` are classified, then routed:
    - The classifier is the authenticated D3-mini Decisions endpoint (on the
      remote GPU host, or on this host with the `d3` profile). With
      `CODING_ROUTER_CLASSIFIER=heuristic`, LiteLLM's local heuristic
      classifies instead.
    - LiteLLM selects a model from the tier's `CODING_ROUTER_*_MODEL` list.
    - If D3-mini fails or times out, LiteLLM scores the request locally.
    - The router exists only when `CODING_ROUTER_DEFAULT_MODEL` is set.
5. The `eu-data-protection` policy runs two guardrails:
    - When `PII_ASSIST_API_BASE` is set, `pii-assist` runs first. It asks the
      local `pii-assist` model for the prompt's language and personal data,
      and masks what it finds. That model is served by your own vLLM server on
      the Docker host or a private network, never a cloud API.
    - `presidio-pii` then calls the Presidio analyzer, in English, German, or
      French, and the anonymizer.
    - With `PII_REVERSIBLE_MASKING=true`, both send numbered tokens such as
      `<PERSON_1>` to the provider instead of placeholders. `pii-restore` then
      puts the request's own values back into the response, after
      `presidio-pii` has masked it.
6. LiteLLM stores request/spend telemetry in its profile-specific PostgreSQL
   database and emits Prometheus metrics.

Presidio uses internal service addresses:

```text
http://presidio-analyzer:3000
http://presidio-anonymizer:3000
```

Keep those addresses aligned with both the LiteLLM configuration and the
Prometheus blackbox `/health` probes.

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

### Data on the remote GPU host

The D3-mini model and its cache live on a separate GPU host, deployed by the
separate [`gpu-host/`](gpu-host/README.md) Compose project. They are not part
of this Compose project's persistence.

- That host must expose only authenticated Decisions routes over verified TLS,
  and should firewall access to the LiteLLM host.
- Both LiteLLM containers mount `GPU_HOST_CA_BUNDLE` as `SSL_CERT_FILE`. The
  bundle is the standard public roots plus the GPU host's dedicated mkcert CA,
  so private-PKI trust does not break provider TLS.
  `scripts/build-gpu-host-ca-bundle.py` builds it.

The development BGE-M3 embedding model and its cache live on the same remote
GPU host. Text Embeddings Inference (TEI) serves it from the same `gpu-host/`
project, behind the same TLS proxy.

- TEI's API key guards every compute route.
- Only `litellm-dev` calls it.
- Keep the link on a private network and firewall it to the LiteLLM dev host.

See
[Remote BGE-M3 embeddings](docs/search-extraction-and-caching.md#remote-bge-m3-embeddings).

### PostgreSQL databases

The PostgreSQL default database is `litellm_prod`; `postgres-init` creates
`litellm_dev`. Do not reintroduce an unused default `litellm` database.

## Observability Data Flow

OpenSearch is the default observability store. The reason: Grafana OSS cannot
restrict data sources or dashboards per user. The OpenSearch security plugin
provides internal users, roles, document/field-level security, read-only
users, and Global/Private tenants without a commercial license.

### Metrics

Prometheus scrapes:

- LiteLLM prod and dev metrics, using the master-key credential file;
- Traefik, PostgreSQL exporter, node exporter, and cAdvisor;
- Blackbox `/health` probes for Presidio and SearXNG;
- with the `lens` profile, a blackbox `/health/ready` probe of Lens (job
  `lens`), discovered through DNS so it disappears with the profile.

Every minute, `opensearch-bridge` evaluates `opensearch/tools/metrics.yml`
against Prometheus and stores the results in `litellm-metrics-*`. The file has
two kinds of entries:

- Expressions reproduce every Prometheus panel of the repository's Grafana
  dashboards, plus the core host, container, PostgreSQL, Traefik, probe, and
  security panels, including histogram quantiles.
- Raw selectors mirror the LiteLLM, Traefik, PostgreSQL, blackbox,
  scrape-health, image-security, and backup series unchanged.

Every other series stays queryable through the `prometheus` direct-query data
source registered in OpenSearch (Observability → Metrics, PPL). Do not remove
exporters merely because a panel is currently empty.

### Request traces

LiteLLM's OpenTelemetry v2 integration exports request, guardrail, model-call,
and database spans:

- They go over OTLP/gRPC to Data Prepper (`data-prepper:21890`).
- Data Prepper writes the OpenSearch Trace Analytics indices
  (`otel-v1-apm-span-*` and `otel-v1-apm-service-map`).
- Request spans carry the `coding-router` decision as `litellm.routing.*`
  attributes.

With the `d3` profile, D3-mini's vLLM also sends one span per decision, as
service `d3-mini`, and Prometheus scrapes its `/metrics` (job `d3-mini`). Both
collectors reach it on the internal `d3-local` network.

With the `langfuse` profile, Langfuse receives an additional copy through the
`langfuse_otel` callback. Without it, the runtime config removes that callback
and its attribution hook.

Data protection and availability:

- Message content capture stays disabled.
- Only `litellm_operator` can read the OpenSearch span indices.
- The proxies do not depend on Data Prepper at startup. Spans are dropped
  while it is unavailable.

LiteLLM Lens is not part of this pipeline. With the `lens` profile, agents send
their own OTLP traces directly to Lens over HTTPS, and the proxies export only
content-free spend records to Lens for cost matching.

### Alerting

The Grafana provisioning files under `grafana/alerting/` remain the single
source of alert rules. OpenSearch alerting is built from them:

- Every minute, the bridge evaluates each rule's PromQL condition
  (`litellm-alert-state-*`).
- `opensearch-setup` creates one OpenSearch Alerting monitor per rule. It fires
  only after the rule's complete `for` duration, honoring `noDataState` and
  `execErrState`.
- Four additional monitors fire when alert evaluation, metrics, container
  logs, or LiteLLM spans stop arriving.

### Database telemetry

The bridge uses the read-only `opensearch_ro` PostgreSQL role to:

- copy non-content LiteLLM spend, provider-error, and audit rows into monthly
  `litellm-spend-*`, `litellm-provider-errors-*`, and `litellm-audit-*`
  indices;
- refresh user, team, and key snapshots in `litellm-entities`.

Prompts, responses, request metadata, client IPs, key hashes, and audit
before/after values are never copied. The optional Grafana profile still
queries PostgreSQL directly through `litellm-pg` and `litellm-pg-dev`.

Failed unknown-model requests are recorded in `LiteLLM_SpendLogs` with
`status = 'failure'`; they are not necessarily written to `LiteLLM_ErrorLogs`.

### Container logs

Grafana Alloy discovers Docker containers through the Docker API. Before it
ships anything, it:

1. keeps only the current Compose project;
2. joins multi-line entries (stack traces, pretty-printed JSON);
3. strips ANSI colours;
4. redacts `q=`/`query=` URL parameters;
5. attaches a normalized `level`.

It ships stdout/stderr over OTLP to Data Prepper. Data Prepper sets
`severityText`, parses Traefik's JSON access logs into `http.*` fields (client
addresses and headers are never logged), and writes `litellm-logs-*`.

The optional `alloy-loki` collector sends the same streams to Loki for
Grafana. Both collectors reach Docker through the POST-disabled
`alloy-docker-proxy`.

Query logs in OpenSearch Dashboards (Discover, the **Container Logs**
dashboard, or PPL), for example:

```text
log.attributes.compose_service:"litellm-prod" and body:error
source = litellm-logs-* | where `log.attributes.stream` = 'stderr' | stats count() by `log.attributes.compose_service`
```

With the Grafana profile, Loki uses the same labels:

```logql
{compose_project="litellm-stack", compose_service="litellm-prod"}
```

!!! warning
    The project filter is a security boundary on shared Docker hosts. Do not
    remove it or collect every Docker project.

## TLS and Secrets

Runtime secrets are intentionally ignored by Git:

- `.env`;
- the certificate and private-key paths configured by `TRAEFIK_TLS_CERT_FILE`
  and `TRAEFIK_TLS_KEY_FILE`;
- `litellm/secrets/litellm-master-key`.

OpenSearch uses a private CA generated by the `opensearch-certs` one-shot
service:

- When the service runs, it renews node and admin certificates automatically
  30 days before expiry.
- HTTP and transport TLS are always enabled, and all clients verify the CA.
- Only the admin certificate holder (`opensearch-setup`) can change the
  security configuration outside the Dashboards security UI.

The on-prem certificate delivery process owns the host-side TLS files.

- Keep the hostnames in the delivered certificate, `.env`, DNS, and local name
  resolution consistent.
- `mkcert` is only a local-development fallback.
- The Prometheus credential file must contain only `LITELLM_MASTER_KEY`.

## Operational Checks

Before declaring a change complete, render the configurations and start the
stack:

```sh
docker compose --profile prod --profile dev config --quiet
docker compose --env-file .env --env-file .env.optional \
  --profile prod --profile langfuse config --quiet
docker compose --env-file .env --env-file .env.optional \
  --profile prod --profile lens config --quiet
docker compose --env-file .env --env-file .env.optional \
  --profile prod --profile d3 config --quiet
docker compose --env-file .env --env-file .env.optional \
  --profile prod --profile docs config --quiet
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

After a target is renamed or removed, its historical series remain until
retention expires. They do not imply an active configuration problem.
