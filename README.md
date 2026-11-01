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
- An NVIDIA GPU and NVIDIA Container Toolkit for the development profile's
  BGE-M3 vLLM service
- Up to 7 GB of available RAM for the CPU-hosted vLLM Semantic Router
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
| [EU technical-control baseline](docs/eu-technical-controls.md) | Modular policy configuration, data minimisation and retention, integrity and transport, deployment decisions outside this repository |
| [Databases and pgvector storage](docs/databases-and-pgvector.md) | LiteLLM schema verification, pgvector store, opt-in Xberg auto-indexing |
| [Maintenance runtime](docs/maintenance-runtime.md) | Installing `uv` for the maintenance scripts and why they use Python |
| [Image security and supply-chain evidence](docs/image-security.md) | Trivy and CISA KEV image scanning, exceptions, alerts, scheduling, SBOMs, licence evidence, OCI image labels |
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

## Security notes

- Keep `.env` and everything under `traefik/certs/` private.
- Rotate any credential that is accidentally exposed.
- The Docker socket mounted by Traefik is security-sensitive; restrict host
  access.
- Do not expose the `dev` profile or its logs to untrusted users.
- Keep image digests immutable in deployed revisions and review updates rather
  than returning to floating tags.
