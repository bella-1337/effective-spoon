# LiteLLM stack

A Docker Compose stack for running separate LiteLLM production and development
proxies behind Traefik, with PostgreSQL, Presidio, SearXNG, Xberg MCP,
development vLLM model servers, Prometheus, host/container exporters, and
OpenSearch with OpenSearch Dashboards as the default multi-user observability
platform for logs, metrics, LiteLLM usage, tracing, alerting, and audit trails.
Grafana/Loki and Langfuse remain available through optional profiles.

The `prod` and `dev` names below describe LiteLLM runtime profiles. The on-prem
deployment expects a certificate delivery process to place the configured TLS
certificate and private key on the host. The bundled `mkcert` workflow remains
available only for a private workstation or trusted development network.

## Prerequisites

- Docker Engine or Docker Desktop with Docker Compose v2.20.0 or newer
  (required for the top-level `include` model)
- Python 3.11 or newer for the local CI validation entrypoint
- A remote GPU host serving the D3-mini Decisions endpoint and, for the
  development profile, the BGE-M3 embedding endpoint, both over TLS with a
  certificate from the host's mkcert CA, deployed with the separate
  [`gpu-host/`](gpu-host/README.md) stack; see
  [Hardware sizing](#hardware-sizing)
- `GPU_HOST_CA_BUNDLE`: the public roots plus that mkcert CA, built with
  `./scripts/build-gpu-host-ca-bundle.py --root-ca rootCA.pem`
- About 4.5 GB of additional RAM for OpenSearch, OpenSearch Dashboards, and Data
  Prepper. On Linux hosts, OpenSearch requires `vm.max_map_count` of at least
  262144:

  ```sh
  sudo sysctl -w vm.max_map_count=262144
  echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-opensearch.conf
  ```
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) for the
  maintenance scripts
- A delivered TLS certificate and matching private key, or `mkcert` for local
  development only
- `curl` for the documented manual API and health checks
- Administrator access to install the local CA and update host resolution

Run all commands from this repository's root.

## Hardware sizing

Rough planning figures for two environments, each running every optional
profile (`grafana`, `langfuse`, `security`). They assume:

- about 100,000 production requests a day with 30–60 in flight at peak;
- prompts are not stored in spend logs, and completions run at OpenAI;
- one environment per host, because Traefik binds host ports `80`/`443` and
  node-exporter and cAdvisor read the whole host;
- D3-mini and BGE-M3 both run on a remote GPU host, so neither stack host
  needs a GPU.

Validate these figures with a load test before purchasing.

| Host | Runs | CPU | RAM | Disk | GPU |
| --- | --- | --- | --- | --- | --- |
| **Prod** | Shared services, `prod`, all optional profiles | 24 cores / 48 threads (16 / 32 minimum) | 128 GB (96 GB minimum) | 2 × 2 TB NVMe, RAID 1 | None |
| **Dev** | Shared services, `prod` and `dev`, all optional profiles | 12 cores / 24 threads (8 / 16 minimum) | 64 GB (48 GB minimum) | 2 × 1 TB NVMe, RAID 1 | None |
| **Remote GPU** | [`gpu-host/`](gpu-host/README.md): D3-mini for both environments, BGE-M3 for dev | 16 cores | 64 GB | 1 TB NVMe | 1 × 48 GB (L40S, RTX 6000 Ada, or A6000) shared by both models |

Cloud equivalents: prod `m7i.8xlarge` (32 vCPU, 128 GB), dev `m7i.4xlarge`
(16 vCPU, 64 GB), and `g6e.4xlarge` (one 48 GB L40S, 16 vCPU, 128 GB) for the
GPU host.

### Prod host budget

Planning peaks, not idle use. The core and prod profile idles at about 7 GiB.

| Block | Services | RAM | vCPU |
| --- | --- | --- | --- |
| Edge and data | Traefik, socket proxies, PostgreSQL, vector-postgres, SearXNG, Xberg sandbox | 7 GB | 4 |
| Presidio | Analyzer (3 GB limit) and anonymizer, 4 workers each | 3.5 GB | 4 |
| Metrics and logs | Prometheus, Alloy, exporters, cAdvisor, blackbox, opensearch-bridge | 4 GB | 2 |
| OpenSearch | OpenSearch (4 GB heap, 8 GB limit), Dashboards, Data Prepper | 12 GB | 5 |
| LiteLLM prod | 4 workers × (1 vCPU, 4 GB) | 16 GB | 4 |
| `grafana` | Grafana, Loki, alloy-loki | 3 GB | 1.5 |
| `langfuse` | Web, worker, PostgreSQL, ClickHouse, Redis, MinIO | 11 GB | 4 |
| `security` | Image scanner (bursty) | 2.5 GB | 2 |
| **Total** | | **~60 GB** | **~27** |

Add about 4 GB for the OS and Docker and at least 25 GB of page cache for
OpenSearch, PostgreSQL, and ClickHouse. That makes 96 GB the minimum. At
128 GB, LiteLLM can grow to 8 workers.

| Prod data | Retention | Disk |
| --- | --- | --- |
| OpenSearch (single node; stay under the 85% watermark) | Logs 7 d, metrics and traces 15 d, audit 90 d, usage 180 d | 150–200 GB |
| PostgreSQL spend logs | 180 d | 50–100 GB |
| Langfuse ClickHouse and MinIO | Unbounded by default | 200–400 GB |
| Prometheus and Loki | 10 GB cap, 7 d | 30 GB |
| Images, build cache, container logs | — | 100 GB |
| Local backup staging, if `BACKUP_DIR` is on the host | 30 d | 100 GB |

### Resource limits

Every long-running service has a CPU and memory cap set from `.env`. Most use
one of four tiers (`RESOURCE_TINY_*`, `RESOURCE_SMALL_*`, `RESOURCE_MEDIUM_*`,
`RESOURCE_DATABASE_*`); LiteLLM and services that size a JVM heap or worker
pool have their own variables. The `.env.example` defaults suit the dev host.
On the prod host, set:

| Variable | Dev host (default) | Prod host |
| --- | --- | --- |
| `RESOURCE_TINY_CPU_LIMIT` / `_MEMORY_LIMIT` | `0.5` / `256m` | `0.5` / `256m` |
| `RESOURCE_SMALL_CPU_LIMIT` / `_MEMORY_LIMIT` | `1` / `1g` | `2` / `1g` |
| `RESOURCE_MEDIUM_CPU_LIMIT` / `_MEMORY_LIMIT` | `2` / `2g` | `4` / `4g` |
| `RESOURCE_DATABASE_CPU_LIMIT` / `_MEMORY_LIMIT` | `2` / `4g` | `6` / `10g` |
| `LITELLM_CPU_LIMIT` / `LITELLM_MEMORY_LIMIT` | `2` / `4g` | `4` / `16g` |
| `PRESIDIO_ANALYZER_CPU_LIMIT` / `_MEMORY_LIMIT` | `2` / `2g` | `4` / `3g` |
| `PRESIDIO_ANALYZER_WORKERS` / `PRESIDIO_ANONYMIZER_WORKERS` | `2` / `2` | `4` / `4` |
| `OPENSEARCH_JAVA_HEAP` / `OPENSEARCH_MEMORY_LIMIT` | `1g` / `3g` | `4g` / `8g` |
| `DATA_PREPPER_JAVA_OPTS` / `DATA_PREPPER_MEMORY_LIMIT` | `-Xms256m -Xmx512m` / `2g` | `-Xms1g -Xmx2g` / `3g` |

- **Each cap contains one runaway service.** On prod, all memory caps together
  come to about 112 GB, under the 128 GB host, so even every service at its cap
  cannot exhaust host memory. On the dev host they come to about 62 GB, which is
  nearly the whole 64 GB host.
- **CPU caps are oversubscribed on purpose.** CPU is shared, not lost, so a cap
  only stops one service from taking every core.
- **Database memory caps include the page cache.** A smaller cap costs cache
  hits, not crashes.
- **LiteLLM still runs one worker.** The prod row is sized for 4 workers, which
  need `--num_workers 4`, Redis so the per-key `rpm_limit` and
  `max_parallel_requests` are shared across workers, multi-process Prometheus
  metrics, and `proxy_batch_write_at: 60`. Until then, `2` / `8g` is enough.

The full list of which service uses which tier is in the
[environment variable reference](docs/environment-variables.md#resource-limits).

### Dev host budget

The dev host is the only environment besides prod, so it runs the `prod`
profile with one worker as a pre-production check, alongside `dev`. It uses
the default resource limits.

| Block | RAM | vCPU |
| --- | --- | --- |
| Edge, data, and Presidio (2 workers each) | 7.5 GB | 5 |
| Metrics and logs | 3 GB | 1.5 |
| OpenSearch and Data Prepper defaults | 6 GB | 3 |
| LiteLLM prod and dev, 1 worker each | 8 GB | 2 |
| Qdrant (2 GB limit plus 1 GB `tmpfs`) and litellm-pgvector | 3.5 GB | 1.5 |
| `grafana`, `langfuse`, `security` | 11.5 GB | 5 |
| **Total** | **~40 GB** | **~18** |

BGE-M3 runs on the remote GPU host, so the dev host needs no GPU.

### Remote GPU host

One 48 GB GPU serves both models through the separate
[`gpu-host/`](gpu-host/README.md) Compose project:

| Model | Engine | Endpoint | GPU memory (typical / worst) |
| --- | --- | --- | --- |
| D3-mini | vLLM Semantic Router model runtime | HTTPS on 8443, prod and dev | ~12 / ~18 GiB at the 8,192-token input cap |
| BGE-M3 | Hugging Face Text Embeddings Inference | HTTPS on 9443, dev only | ~2–3 / ~4 GiB |

One Nginx proxy terminates TLS for both with a certificate from the host's
dedicated mkcert CA; the LiteLLM hosts trust that CA through
`GPU_HOST_CA_BUNDLE`.

- **About 23 GiB at worst** leaves more than 20 GB free. Neither engine reserves
  memory up front, so no per-process fractions are needed.
- **D3-mini is on prod's request path.** `coding-router` calls it on every
  request; if it fails or takes longer than 15 seconds, routing falls back to
  `gpt-4o`. Keep the host on the same site network and firewalled to both
  LiteLLM hosts.
- **BGE-M3** has its own API key, which guards every compute route. Keep its
  port firewalled to the dev host. Promoting the semantic cache or RAG to prod
  adds prod load to this GPU.
- **Dev and prod share D3-mini.** A dev load test can slow prod routing. With
  the headroom on a 48 GB card, a second D3-mini instance for dev fits too.

## Getting started

Follow the [getting-started guide](docs/getting-started.md) for a first
deployment:

1. [Configure the environment](docs/getting-started.md#1-configure-the-environment)
2. [Deliver TLS certificates](docs/getting-started.md#2-deliver-tls-certificates)
3. [Resolve the hostnames](docs/getting-started.md#3-resolve-the-hostnames)
4. [Fetch Grafana dashboards](docs/getting-started.md#4-fetch-grafana-dashboards-optional-grafana-profile-only)
   (optional `grafana` profile only)
5. [Configure Prometheus metrics authentication](docs/getting-started.md#5-configure-prometheus-metrics-authentication)
6. [Validate configuration](docs/getting-started.md#6-validate-configuration)
7. [Start a profile](docs/getting-started.md#7-start-a-profile)
8. [Verify the stack](docs/getting-started.md#8-verify-the-stack)

To stop, restart, or remove the stack, see
[Stop, restart, and remove](docs/getting-started.md#stop-restart-and-remove).

## Documentation

### Guides

| Guide | Covers |
| --- | --- |
| [Getting started](docs/getting-started.md) | Environment files, `STACK_DOMAIN` hostnames, TLS certificates, host resolution, Prometheus authentication, validation, starting and verifying profiles, stopping and removing the stack |
| [Development and delivery workflow](docs/development-workflow.md) | Development container, local and GitHub Actions CI, release delivery |
| [Corporate branding and virtual-key management](docs/branding-and-key-management.md) | Admin UI branding, Enterprise virtual-key rotation, key email notifications |
| [Observability](docs/observability.md) | OpenSearch users, roles, tenants, alerting and recovery; optional Langfuse and Grafana profiles; demonstration data and telemetry stores |
| [Search, document extraction, and caching](docs/search-extraction-and-caching.md) | SearXNG search and engine terms of use, Xberg MCP document extraction, development semantic response cache |
| [Presidio PII masking and guardrails](docs/presidio-guardrails.md) | Presidio masking and changing, adding, disabling, and verifying LiteLLM guardrails |
| [Edge and abuse protection](docs/edge-and-abuse-protection.md) | TLS policy, HSTS, per-client rate limits, per-key defaults, Xberg sandbox egress and DNS, prompt-injection screening, and residual risks |
| [EU technical-control baseline](docs/eu-technical-controls.md) | Modular policy configuration, data minimisation and retention, integrity and transport, deployment decisions outside this repository |
| [Databases and pgvector storage](docs/databases-and-pgvector.md) | LiteLLM schema verification, pgvector store, opt-in Xberg auto-indexing |
| [Maintenance runtime](docs/maintenance-runtime.md) | Installing `uv` for the maintenance scripts and why they use Python |
| [Image security and supply-chain evidence](docs/image-security.md) | Trivy and CISA KEV image scanning, exceptions, alerts, scheduling, SBOMs, licence evidence, OCI image labels |
| [Remote GPU host](gpu-host/README.md) | D3-mini and BGE-M3 on one shared GPU: engine choice, memory budget, deployment, network security |
| [Back up and restore the databases](docs/backup-and-restore.md) | Database backups, restore, and the game-day preflight |
| [Validate and roll back a LiteLLM image upgrade](docs/upgrades-and-rollback.md) | Maintenance entry point, upgrade compatibility checks, regression suite, rollback |

### Reference

| Document | Covers |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | Stack components and their interactions |
| [Diagrams](diagrams/README.md) | Animated architecture and flow diagrams, including clients and external services, generated from Mermaid sources |
| [Environment variable reference](docs/environment-variables.md) | Every variable, its format, default, classification, rotation, and change impact |
| [Third-party licence register](docs/third-party-licences.md) | Licences of the deployed images and external services |
| [Development-to-production promotion register](docs/dev-to-prod-promotion-register.md) | Development-only capabilities and their production promotion status |
| [Operations game day](OPERATIONS-GAME-DAY.md) | Quarterly failure and recovery exercise |
| [DDoS, MITM, and prompt-injection probe (2026-11-01)](docs/security-probe-2026-11-01.md) | Live probe evidence, findings, applied fixes, and residual risks |

## Security notes

- Keep `.env` and everything under `traefik/certs/` private.
- Rotate any credential that is accidentally exposed.
- The Docker socket mounted by Traefik is security-sensitive; restrict host
  access.
- Do not expose the `dev` profile or its logs to untrusted users.
- Keep image digests immutable in deployed revisions and review updates rather
  than returning to floating tags.
