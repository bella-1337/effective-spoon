# LiteLLM Stack Architecture

## Purpose

This repository deploys a local, Docker Compose-based LiteLLM platform with:

- locally trusted HTTPS termination;
- separate production and development LiteLLM profiles;
- PII masking through Microsoft Presidio;
- PostgreSQL-backed LiteLLM state and spend records;
- Langfuse tracing backed by PostgreSQL, ClickHouse, Redis, and MinIO;
- development semantic routing, Qdrant-backed semantic caching, and
  pgvector-backed shared RAG;
- allowlisted Xberg MCP document extraction through an egress proxy;
- metrics in Prometheus and dashboards in Grafana;
- Docker container logs in Loki, collected by Grafana Alloy.

The bundled `mkcert` configuration is for a trusted local workstation or
private network. It is not a public Internet TLS solution.

## Topology

```text
                              +--------------------------+
                              | Browser / API client     |
                              +------------+-------------+
                                           |
                                      HTTPS :443
                                           |
                              +------------v-------------+
                              | Traefik                  |
                              | mkcert TLS / routing     |
                              +---+-----------+----------+
                                  |           |
                    +-------------+           +---------------------+
                    |                                                   |
       +------------v-------------+                         +-----------v-----------+
       | LiteLLM prod / dev       |                         | Grafana               |
       | profile-selected proxies |                         | dashboards / Explore  |
       +--+------------+----------+                         +-----+-----------+-----+
          |            |                                          |           |
          |            |                                          |           |
          |     +------v-------+                         +--------v--+   +----v-----+
          |     | Presidio     |                         | Prometheus |   | Loki     |
          |     | analyzer +   |                         | metrics    |   | logs     |
          |     | anonymizer   |                         +------------+   +----+-----+
          |     +--------------+                                             |
          |                                                                   |
       +--v----------------+                                      +-----------v-----+
       | PostgreSQL        |                                      | Grafana Alloy    |
       | prod + dev DBs    |                                      | Docker log intake |
       +-------------------+                                      +------------------+
```

## Network Boundaries

Dedicated Docker networks separate external routing, internal service traffic,
Docker metadata access, image-security monitoring, and untrusted fetch egress.

| Network | Members | Purpose |
| --- | --- | --- |
| `edge` | Traefik, Grafana, SearXNG, Langfuse web/MinIO, LiteLLM prod/dev, semantic-router Envoy | Services reachable through Traefik routes |
| `internal` | Trusted application, data, and observability services | Databases, telemetry, Presidio, and service-to-service calls |
| `xberg-sandbox` | Xberg, its egress proxy, LiteLLM prod/dev | Internal-only MCP traffic; Xberg has no direct route to `internal` or the Internet |
| `egress` | Xberg egress proxy | Internet route available only to the destination-filtering proxy |
| `traefik-monitor` | Traefik and its Docker socket proxy | Read-only Docker routing metadata |
| `alloy-monitor` | Alloy and its Docker socket proxy | Read-only Docker discovery and log metadata |
| `security-monitor` | Image scanner and its Docker socket proxy | Isolated image and container inventory |

Only Traefik publishes host ports `80` and `443`. Prometheus, Loki, PostgreSQL,
exporters, socket proxies, and the Xberg egress proxy remain internal-only.

## Runtime Profiles

Shared services run without a profile. Select one or both LiteLLM proxies:

```sh
docker compose --profile prod up -d
docker compose --profile dev up -d
docker compose --profile prod --profile dev up -d
```

| Profile | Proxy | Database | Intended use |
| --- | --- | --- | --- |
| `prod` | `litellm-prod` | `litellm_prod` | Normal proxy configuration with Presidio PII guardrail |
| `dev` | `litellm-dev` | `litellm_dev` | Debug-oriented proxy configuration |

`postgres-init` is a required one-shot service, not redundant: it creates the
two LiteLLM databases on a fresh PostgreSQL volume.

## Request and PII Flow

1. A client connects to a configured hostname over HTTPS.
2. Traefik selects the matching LiteLLM, Grafana, or SearXNG route.
3. LiteLLM authenticates API requests with `LITELLM_MASTER_KEY`.
4. The production guardrail calls Presidio analyzer and anonymizer services.
5. LiteLLM stores request/spend telemetry in its profile-specific PostgreSQL
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
| `grafana-data` | Grafana | Users, sessions, and provisioned dashboard state |
| `prometheus-data` | Prometheus | Metrics time series |
| `prometheus-secrets` | Prometheus | Non-root-readable copy of the LiteLLM metrics credential |
| `loki-data` | Loki | Project-scoped Docker log history |
| `langfuse-postgres-data` | Langfuse PostgreSQL | Langfuse relational state |
| `langfuse-clickhouse-data` | ClickHouse | Langfuse trace and analytics data |
| `langfuse-clickhouse-logs` | ClickHouse | ClickHouse service logs |
| `langfuse-minio-data` | MinIO | Langfuse events and media objects |
| `langfuse-redis-data` | Redis | Langfuse queues |
| `bge-m3-vllm-cache` | vLLM | Downloaded BGE-M3 model artifacts |

The PostgreSQL default database is `litellm_prod`; `postgres-init` creates
`litellm_dev`. Do not reintroduce an unused default `litellm` database.

## Observability Data Flow

### Metrics

Prometheus scrapes:

- LiteLLM prod and dev metrics using the master-key credential file;
- Traefik, PostgreSQL exporter, node exporter, and cAdvisor;
- Blackbox `/health` probes for Presidio and SearXNG.

Grafana uses the `prom` datasource for the LiteLLM stack dashboard. Do not
remove exporters merely because a panel is currently empty: metric activity
depends on workload and downloaded dashboards may rely on those jobs.

### Database telemetry

Grafana queries the `litellm-pg` and `litellm-pg-dev` datasources for LiteLLM
spend data. Failed unknown-model requests are recorded in
`LiteLLM_SpendLogs` with `status = 'failure'`; they are not necessarily written
to `LiteLLM_ErrorLogs`.

### Container logs

Grafana Alloy discovers Docker containers through the Docker API and sends only
the `litellm-stack` Compose project to Loki. Alloy and Traefik reach Docker
through dedicated POST-disabled socket proxies rather than direct socket mounts.
Query logs in Grafana Explore:

```logql
{compose_project="litellm-stack"}
{compose_project="litellm-stack", compose_service="litellm-prod"}
```

The project filter is a security boundary on shared Docker hosts. Do not remove
it or collect every Docker project.

## TLS and Secrets

Runtime secrets are intentionally ignored by Git:

- `.env`;
- `traefik/certs/litellm-stack.pem`;
- `traefik/certs/litellm-stack-key.pem`;
- `litellm/secrets/litellm-master-key`.

Generate the certificate locally with `mkcert` and keep the hostnames in the
certificate, `.env`, and local name resolution consistent. The Prometheus
credential file must contain only `LITELLM_MASTER_KEY`.

## Operational Checks

Before declaring a change complete:

```sh
docker compose --profile prod --profile dev config --quiet
docker compose --profile prod --profile dev up -d --wait --wait-timeout 150
```

Then verify:

```sh
curl --fail https://llm.example.test/health/liveliness
curl --fail https://llm-dev.example.test/health/liveliness
curl --fail https://grafana.example.test/api/health
curl --fail 'https://search.example.test/search?q=LiteLLM&format=json'
```

Check active Prometheus targets rather than historical series:

```sh
docker compose exec -T prometheus wget -qO- \
  'http://localhost:9090/api/v1/targets?state=active'
```

Historical series remain until retention expires after a target is renamed or
removed. They do not imply an active configuration problem.
