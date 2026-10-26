# LiteLLM Stack Architecture

## Purpose

This repository deploys a local, Docker Compose-based LiteLLM platform with:

- locally trusted HTTPS termination;
- separate production and development LiteLLM profiles;
- PII masking through Microsoft Presidio;
- PostgreSQL-backed LiteLLM state and spend records;
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

Two Docker networks separate external routing from internal service traffic.

| Network | Members | Purpose |
| --- | --- | --- |
| `edge` | Traefik, Grafana, SearXNG, LiteLLM prod/dev | Services reachable through Traefik routes |
| `internal` | All services | Databases, observability, log collection, and service-to-service calls |

Only Traefik publishes host ports `80` and `443`. Prometheus, Loki, PostgreSQL,
and exporters remain internal-only.

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
| `grafana-data` | Grafana | Users, sessions, and provisioned dashboard state |
| `prometheus-data` | Prometheus | Metrics time series |
| `loki-data` | Loki | Project-scoped Docker log history |

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

Grafana Alloy discovers Docker containers through the Docker socket and sends only
the `litellm-stack` Compose project to Loki. Query logs in Grafana Explore:

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
