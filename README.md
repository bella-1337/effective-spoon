# LiteLLM stack

<!-- badges:start -->
[![CI](https://github.com/bella-1337/effective-spoon/actions/workflows/validate.yml/badge.svg)](https://github.com/bella-1337/effective-spoon/actions/workflows/validate.yml)
[![Supply chain](https://github.com/bella-1337/effective-spoon/actions/workflows/supply-chain.yml/badge.svg)](https://github.com/bella-1337/effective-spoon/actions/workflows/supply-chain.yml)
[![Documentation](https://github.com/bella-1337/effective-spoon/actions/workflows/docs.yml/badge.svg)](https://github.com/bella-1337/effective-spoon/actions/workflows/docs.yml)
[![Release](https://github.com/bella-1337/effective-spoon/actions/workflows/release.yml/badge.svg)](https://github.com/bella-1337/effective-spoon/actions/workflows/release.yml)
[![Dev container](https://github.com/bella-1337/effective-spoon/actions/workflows/devcontainer.yml/badge.svg)](https://github.com/bella-1337/effective-spoon/actions/workflows/devcontainer.yml)
[![Latest release](https://img.shields.io/github/v/release/bella-1337/effective-spoon?sort=semver)](https://github.com/bella-1337/effective-spoon/releases/latest)
[![Docs site](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://bella-1337.github.io/effective-spoon/)
[![Dependabot](https://img.shields.io/badge/dependabot-enabled-brightgreen?logo=dependabot)](https://github.com/bella-1337/effective-spoon/blob/master/.github/dependabot.yml)
<!-- badges:end -->

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
  [Hardware sizing](docs/hardware-sizing.md#remote-gpu-host)
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

Run each environment on its own host. D3-mini and BGE-M3 run on a separate
remote GPU host, so neither stack host needs a GPU. For about 100,000
production requests a day with every optional profile, plan on roughly
128 GB RAM for prod, 64 GB for dev, and one 48 GB GPU for the remote host.
The [hardware sizing guide](docs/hardware-sizing.md) has the details:

- [Host specifications](docs/hardware-sizing.md) and cloud equivalents
- [Prod host budget](docs/hardware-sizing.md#prod-host-budget) of memory, CPU,
  and disk
- [Resource limits](docs/hardware-sizing.md#resource-limits) to set on the
  prod host
- [Dev host budget](docs/hardware-sizing.md#dev-host-budget)
- [Remote GPU host](docs/hardware-sizing.md#remote-gpu-host) memory budget and
  network placement

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

The guides below are also published as a versioned documentation site on
GitHub Pages or GitLab Pages, with a version selector for each release and
the unreleased `dev` branch. Preview it with `./scripts/build-docs.py serve`;
see [Documentation site](docs/documentation-site.md).

### Guides

| Guide | Covers |
| --- | --- |
| [Getting started](docs/getting-started.md) | Environment files, `STACK_DOMAIN` hostnames, TLS certificates, host resolution, Prometheus authentication, validation, starting and verifying profiles, stopping and removing the stack |
| [Hardware sizing](docs/hardware-sizing.md) | Prod, dev, and remote GPU host specifications, memory, CPU, and disk budgets, prod resource limits |
| [Development and delivery workflow](docs/development-workflow.md) | Development container, local, GitHub Actions, and GitLab CI checks, release delivery |
| [Documentation site](docs/documentation-site.md) | Local preview, versions and the dev switch, GitHub Pages and GitLab Pages publishing |
| [Corporate branding and virtual-key management](docs/branding-and-key-management.md) | Admin UI branding, Enterprise virtual-key rotation, key email notifications |
| [Observability](docs/observability.md) | OpenSearch users, roles, tenants, alerting and recovery; optional Langfuse and Grafana profiles; demonstration data and telemetry stores |
| [Search, document extraction, and caching](docs/search-extraction-and-caching.md) | SearXNG search and engine terms of use, Xberg MCP document extraction, development semantic response cache |
| [Presidio PII masking and guardrails](docs/presidio-guardrails.md) | Presidio masking and changing, adding, disabling, and verifying LiteLLM guardrails |
| [Edge and abuse protection](docs/edge-and-abuse-protection.md) | TLS policy, HSTS, per-client rate limits, per-key defaults, Xberg sandbox egress and DNS, prompt-injection screening, and residual risks |
| [EU technical-control baseline](docs/eu-technical-controls.md) | Modular policy configuration, data minimisation and retention, integrity and transport, deployment decisions outside this repository |
| [Databases and pgvector storage](docs/databases-and-pgvector.md) | LiteLLM schema verification, pgvector store, opt-in Xberg auto-indexing |
| [Maintenance runtime](docs/maintenance-runtime.md) | Installing `uv` for the maintenance scripts, why they use Python, and where their logs are kept for review |
| [Image security and supply-chain evidence](docs/image-security.md) | Trivy and CISA KEV image scanning, exceptions, alerts, scheduling, SBOMs, licence evidence, OCI image labels |
| [Remote GPU host](gpu-host/README.md) | D3-mini and BGE-M3 on one shared GPU: engine choice, memory budget, deployment, network security |
| [Back up and restore the databases](docs/backup-and-restore.md) | Database backups, restore, and the game-day preflight |
| [Validate and roll back a LiteLLM image upgrade](docs/upgrades-and-rollback.md) | Maintenance entry point, upgrade compatibility checks, regression suite, rollback |

### Reference

| Document | Covers |
| --- | --- |
| [Changelog](CHANGELOG.md) | Notable changes in each release |
| [Architecture](ARCHITECTURE.md) | Stack components and their interactions |
| [Diagrams](diagrams/README.md) | Animated architecture diagrams and flow sequence diagrams, including clients and external services, generated from Mermaid sources |
| [Environment variable reference](docs/environment-variables.md) | Every variable, its format, default, classification, rotation, and change impact |
| [Third-party licence register](docs/third-party-licences.md) | Licences of the deployed images and external services |
| [Development-to-production promotion register](docs/dev-to-prod-promotion-register.md) | Development-only capabilities and their production promotion status |
| [Operations game day](OPERATIONS-GAME-DAY.md) | Quarterly failure and recovery exercise |
| [DDoS, MITM, and prompt-injection probe (2026-11-01)](docs/security-probe-2026-11-01.md) | Live probe evidence, findings, applied fixes, and residual risks |

## Security notes

- Report vulnerabilities privately as described in the
  [security policy](https://github.com/bella-1337/effective-spoon/blob/master/.github/SECURITY.md).
- Keep `.env` and everything under `traefik/certs/` private.
- Rotate any credential that is accidentally exposed.
- The Docker socket mounted by Traefik is security-sensitive; restrict host
  access.
- Do not expose the `dev` profile or its logs to untrusted users.
- Keep image digests immutable in deployed revisions and review updates rather
  than returning to floating tags.

## Contributing

This project does not accept outside contributions at the moment. Please do
not open pull requests or feature requests; they will not be reviewed or
merged. You are welcome to fork the repository under the terms of its licence.

The one exception is security. If you find a vulnerability, do not open a
public issue, pull request, or discussion. Report it privately through
[GitHub private vulnerability reporting](https://github.com/bella-1337/effective-spoon/security/advisories/new)
and include:

- the affected file, service, or release tag;
- steps to reproduce, or a proof of concept that uses placeholder credentials
  only;
- the impact you expect, for example credential exposure, privilege
  escalation, or data leaving the stack.

If a live credential was exposed, rotate it first and say so in the report.
The [security policy](https://github.com/bella-1337/effective-spoon/blob/master/.github/SECURITY.md)
describes the supported versions and what is in scope.

## Licence and warranty

This stack is published as is, **without warranty of any kind**, express or
implied, including the warranties of merchantability, fitness for a particular
purpose, and non-infringement. Use it at your own risk; the authors are not
liable for any claim, damage, or other liability arising from its use.

- The stack's own container images (`litellm-opensearch-tools` and
  `litellm-image-security-monitor`) are released under the
  [MIT License](https://opensource.org/license/mit). The packages and tools
  they bundle keep their own licences.
- Every third-party tool the stack uses, including each container image,
  model, and package, is distributed under its own licence, which you must
  comply with. The [third-party licence register](docs/third-party-licences.md)
  lists them.
- The repository's own files are licensed under the
  [Apache License 2.0](https://github.com/bella-1337/effective-spoon/blob/master/LICENSE).
