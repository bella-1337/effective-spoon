# Changelog

All notable changes to this stack are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases use
[semantic versioning](https://semver.org/spec/v2.0.0.html) tags
(`vMAJOR.MINOR.PATCH`). Each tag publishes a validated source bundle and its
`SHA256SUMS`; see [Development and delivery workflow](docs/development-workflow.md#continuous-delivery).

## [Unreleased]

### Added

- Status badges for every GitHub Actions workflow, the latest release, the
  documentation site, and Dependabot in the README. The documentation site
  drops them so it still makes no third-party requests.
- Security policy, contributing guide, issue and pull request templates,
  release-note categories, and `.editorconfig`.
- A warranty disclaimer and licence notice in the README and in the
  documentation site footer: the stack is published as is, without warranty;
  its own container images are MIT-licensed; every third-party tool keeps its
  own licence.

### Changed

- The documentation site's home page banner and footer are rewritten for a
  published stack. Releases up to `v0.1.0`, whose pages lack the warranty and
  licence notice, are no longer built into the site.
- The OCI `licenses` label of the stack's own images is now `MIT` instead of
  `LicenseRef-Proprietary`.

### Fixed

- A release tag now publishes the documentation site through one
  default-branch run, which the `github-pages` environment admits, instead of
  a second, rejected tag deployment.

## [v0.1.0] - 2026-11-03

Initial release of the self-hosted LiteLLM gateway stack.

### Gateway and runtime profiles

- Separate LiteLLM `prod` (`v1.106.6`) and `dev` (`v1.107.0-dev.3`) proxies
  behind Traefik, each with its own PostgreSQL database and the
  `postgres-init` one-shot dependency.
- Public hostnames derived from `STACK_DOMAIN`; all on-prem settings
  externalized into `.env` and `.env.optional`, documented in the
  [environment variable reference](docs/environment-variables.md).
- Delivered TLS certificates for on-prem deployment; `mkcert` for private
  workstations only.
- Native LiteLLM admin UI branding and Enterprise virtual-key rotation.

### Models, search, and guardrails

- Remote GPU host stack ([`gpu-host/`](gpu-host/README.md)) serving D3-mini
  semantic routing and the pinned BGE-M3 embedding model behind mkcert TLS,
  trusted from LiteLLM through `GPU_HOST_CA_BUNDLE`.
- Presidio PII masking guardrails, internal-only SearXNG search, and the Xberg
  MCP document extractor with an isolated, DNS-restricted egress path.
- pgvector store with opt-in Xberg auto-indexing, and a development Qdrant
  semantic response cache.

### Observability

- OpenSearch, OpenSearch Dashboards, and Data Prepper as the default
  observability platform for logs, metrics, LiteLLM usage, tracing, alerting,
  and audit trails, with users, roles, and tenants.
- Prometheus with host, container, PostgreSQL, and blackbox exporters, and
  Grafana Alloy as the Docker log collector, scoped to the stack's Compose
  project.
- Optional `grafana` (Grafana and Loki) and `langfuse` profiles, management
  ROI dashboards, and a single alert-rule source in `grafana/alerting/`.

### Security and compliance

- Every image pinned by digest, with Trivy and CISA KEV image scanning,
  reviewed exceptions, SBOMs, licence evidence, and OCI labels on local
  images.
- The locally built images upgrade their Debian packages at build time, and
  the `litellm-pgvector` connector installs patched LiteLLM, FastAPI,
  Starlette, AnyIO, and python-multipart releases over its upstream pins
  (`pgvector/requirements-overrides.txt`), so the release image gate has no
  KEV or fixable Critical findings.
- Edge hardening against DDoS, MITM, and prompt injection: TLS policy, HSTS,
  per-client rate limits, and per-key defaults.
- Least-privilege observability database roles, restricted Docker socket
  access through socket proxies, and resource limits on every service.
- [Third-party licence register](docs/third-party-licences.md) with a drift
  check, [EU technical-control baseline](docs/eu-technical-controls.md), and
  the [dev-to-prod promotion register](docs/dev-to-prod-promotion-register.md).

### Operations

- `uv`-based maintenance scripts that log to the console and a private file,
  covering database backup and restore, LiteLLM upgrade compatibility checks,
  a regression suite, and rollback.
- Quarterly [operations game day](OPERATIONS-GAME-DAY.md) with a preflight
  check, and [hardware sizing](docs/hardware-sizing.md) for two-host
  deployments.

### Delivery and documentation

- Equivalent CI/CD on GitHub Actions and GitLab CI: hermetic validation,
  ShellCheck, Hadolint, Actionlint, Gitleaks, image and dependency
  vulnerability gates, and a development container build.
- Tag-triggered release publication of the source bundle and `SHA256SUMS`.
- Animated architecture diagrams generated from Mermaid sources, and a
  versioned documentation site built with Zensical and published to GitHub
  Pages or GitLab Pages.

[Unreleased]: https://github.com/bella-1337/effective-spoon/compare/v0.1.0...HEAD
[v0.1.0]: https://github.com/bella-1337/effective-spoon/releases/tag/v0.1.0
