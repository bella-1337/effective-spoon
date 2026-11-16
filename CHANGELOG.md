# Changelog

All notable changes to this stack are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases use
[semantic versioning](https://semver.org/spec/v2.0.0.html) tags
(`vMAJOR.MINOR.PATCH`). Each tag publishes a validated source bundle and its
`SHA256SUMS`; see [Development and delivery workflow](docs/development-workflow.md#continuous-delivery).

## [Unreleased]

### Changed

- Production LiteLLM moves from `v1.108.2` to the `v1.108.9` LTS patch
  release, still pinned by digest. The 1.108.x LTS line needs no migration or
  configuration change: production already sets `drop_params: true`, which
  1.108.7 requires for Responses-only fields on bridged providers.
- Development LiteLLM follows the nightly channel from `v1.110.0-dev.1` to
  `v1.111.0-dev.4`, still pinned by digest; it passes the upgrade
  compatibility check. Its new `redact_tokens_in_spend_logs` setting now
  defaults to off; both gateways keep `store_prompts_in_spend_logs: false`, so
  spend logs hold no free-form text to scan. Releases from `v1.111.0-dev.3`
  are signed with Sigstore keyless signing instead of `cosign.pub`.
  The image now runs as the non-root user 65532 and replaces
  `docker/prod_entrypoint.sh` with `docker-entrypoint.sh`, so `litellm-dev`
  launches the proxy through `/app/docker-entrypoint.sh proxy`. The
  compatibility check runs `litellm` directly and cannot detect such a
  launcher change; only starting the gateway does.
- The upgrade compatibility check gives its config validator 2 GiB of memory
  instead of 512 MiB. LiteLLM 1.111 peaks near 1 GiB while loading the
  configuration and otherwise never printed its policy-engine summary, which
  failed the check for a compatible image.
- The upgrade regression suite's `opensearch-logs` and `logs` checks no
  longer fail for a running service whose container has written no log line;
  they report it instead. Lens logs nothing in normal operation, so both
  checks failed whenever the `lens` profile ran.
- Prometheus `v3.15.1` to `v3.16.0`, Grafana Alloy `v1.21.3` to `v1.22.0`,
  and Data Prepper `2.16.0` to `2.17.0`. Data Prepper's `otel_traces`
  processor no longer copies `gen_ai.*` attributes and token totals onto
  non-GenAI root spans, so the **Model calls by model** trace panel stops
  counting each LiteLLM request's HTTP root span as a second model call. The
  Alloy breaking changes (`loki.enrich`, `prometheus.enrich`, OTel connectors)
  and the Prometheus TLS external-URL default do not affect this stack.
- Optional `grafana` profile: Grafana `13.3.0` to `13.3.1` (six CVE fixes)
  and Loki `3.7.9` to `3.7.10`.
- Optional `langfuse` profile: Langfuse web and worker `4.72.0` to `4.80.0`
  and ClickHouse `26.3.62.3` to `26.3.64.3` (same 26.3 LTS series), with
  rebuilt Redis `7.4.12` and Chainguard MinIO digests. Langfuse runs its
  PostgreSQL and ClickHouse migrations on start. From 4.77 it no longer
  tokenizes generations itself; LiteLLM already reports token usage on every
  span.
- Optional `lens` profile: its ClickHouse `26.9.6.6` to `26.9.29.5`, the
  latest patch of the 26.9 series Lens pins, and a newer
  `cgr.dev/chainguard/wolfi-base` runtime digest for the Lens build.
- The Xberg egress proxy moves from Squid `6.13-25.04_edge`, whose Ubuntu
  25.04 base no longer receives updates, to `7.2-26.04_edge`. The new Ubuntu
  rock runs Squid as its unprivileged `_daemon_` user (584792) without a
  shell, so the service calls the image's entrypoint directly, owns its
  tmpfs mounts as that user, drops all capabilities, and probes its port with
  Perl instead of Bash.
- Traefik `v3.7.17` to `v3.7.19`, which fixes five security advisories. Its
  migration notes (BasicAuth password length, Ingress-NGINX TLS options,
  dot-segments with path parameters) need no change here.
- SearXNG `2026.10.30-ffa88e3ba` to `2026.11.15-2d500cb6b` (rolling release).
- Qdrant `v1.20.0` to `v1.20.1`, which fixes slow HNSW builds on many-core
  hosts.
- Trivy `0.76.1` to `0.76.2` in the image-security monitor, host-mode scans,
  and both CI systems. The release rebuilds Trivy on Alpine 3.24.3 and fixes
  several dependency CVEs.
- Docker CLI and buildx `29.9.2-cli` to `29.9.3-cli` in the image-security
  monitor, and the GitLab CI Docker CLI and Docker-in-Docker images to
  `29.9.3`.
- CI tooling: Ruff `0.17.4` to `0.17.5` on GitHub Actions and GitLab CI, and
  the GitLab release job's `glab` image `v1.128.0` to `v1.130.0`.
- Remote GPU host: the TLS terminator moves from nginx `1.29.5-alpine` to the
  `1.31.7-alpine` mainline release; its configuration passes `nginx -t`
  unchanged. Redeploy the GPU host with `gpu-host/deploy.py` to apply it.
- PostgreSQL `16.15-alpine3.24` to `16.16-alpine3.24` and `17.11` to `17.12`,
  and a rebuilt `pgvector/pgvector:0.8.7-pg16` digest. These minor releases
  need no dump or restore. Their release notes ask for a `REINDEX` of B-tree
  indexes on unconstrained `bpchar` columns and of `gist_trgm_ops` indexes,
  and of GIN indexes suspected of corruption.

## [v0.3.0] - 2026-11-09

### Added

- Two request-flow diagrams that start from the user's prompt and show who
  decides on and runs each tool call: the model's Xberg `extract` call, which
  LiteLLM runs itself, and the model's search call, which the agent runs
  through the native `searxng-search` endpoint.
- Optional `lens` profile with [LiteLLM Lens](docs/lens.md): AI review of agent
  traces, findings, and datasets, embedded in both LiteLLM dashboards. Lens is
  built from the pinned `BerriAI/lens` release-candidate commit
  `b2ac5f8a289c3f77f02b81d67ca1ee075bfead6d` (`v1.108.0-rc.1`) and stores its
  data in its own ClickHouse on the internal `lens-storage` network. A
  preflight job rejects missing or reused secrets, Traefik routes `LENS_HOST`,
  Prometheus probes its readiness, and the **LiteLLM Lens is not ready** alert
  covers a running but unready Lens. The build uses a newer Wolfi runtime base
  than the pinned commit, whose base fails the image release gate with a
  fixable Critical `ca-certificates-bundle` CVE.
- `scripts/lens.py` (also `maintenance.py lens`) generates missing Lens secrets
  without rotating existing ones, checks Lens and each gateway's connection, and
  runs an end-to-end trace smoke test. The upgrade regression suite gains a
  `lens` check.
- Ruff (`ruff.toml`: syntax, Pyflakes, and bugbear rules) and zizmor
  (`.github/zizmor.yml`: workflow and Dependabot security audit) run in CI on
  GitHub Actions and GitLab CI through `scripts/ci/static-checks.sh`.
- An OpenSSF Scorecard workflow reports to code scanning and publishes the
  score behind a new README badge.
- Dependabot updates the image pins in `docker-compose.yml`,
  `compose.optional.yml`, and `gpu-host/compose.yml`, except LiteLLM and major
  versions, and grouping images that must match. Every Dependabot entry waits
  seven days after an upstream release.
- The `master` branch and `v*` release-tag rulesets are kept as code in
  `.github/rulesets/`. The repository-settings guide applies them together
  with immutable releases, a tag-only `release` environment, SHA-pinned
  actions, fork pull request approval, secret push protection, CodeQL default
  setup, and the labels Dependabot uses.
- A "Planned improvements" list in the development workflow guide: a Compose
  smoke test, a release SBOM attestation, dependency review, and a link check.

### Changed

- Both LiteLLM gateways start through `prepare_runtime_config.py` as a launcher.
  It enables the Lens connection only while the `lens` service runs, removes the
  `LITELLM_LENS_*` variables from the gateway process otherwise, and refuses to
  start a gateway whose Lens secrets are missing or invalid while Lens runs.
- The maintenance contracts accept build contexts fetched from Git only when
  they pin a full commit that the image's revision label and the licence
  register record.
- A manual Release run must be started on the release tag itself; the
  `release_tag` input is removed, and a run on a branch fails before any
  check.

### Fixed

- The OpenSearch bridge binds each cycle's evaluation time to its metric and
  usage tasks explicitly instead of through closures over the loop variable.
- Unused imports in three maintenance scripts.

## [v0.2.1] - 2026-11-08

### Changed

- Architecture diagrams are laid out by Graphviz `dot` instead of dashmotion's
  own engine. Connectors are curved and routed between boxes, so they no longer
  trace the dashed boundary rectangles, and each boundary keeps its members
  together. Rendering now needs Graphviz.
- The rendered diagram HTML is no longer committed. The Mermaid sources are the
  only form in the repository; the documentation build in CI renders them,
  installing Graphviz in its image, and fails when one does not render.
  `./scripts/render-diagrams.py` downloads the dashmotion renderer at a pinned
  commit and checks its SHA-256 digests, so the dashmotion skill is no longer
  needed. The `--check` option is removed.
- The Mermaid edges alone decide the rows, as in any Mermaid viewer. The
  `%% @tier` directive is removed from the diagram format and from every
  source.

## [v0.2.0] - 2026-11-07

### Added

- A [component versions](docs/component-versions.md) table lists the version
  of every image, model, and host tool in one place. `doctor` and
  `scripts/ci/validate.sh` fail when it misses an image the stack uses or lists
  one it no longer uses.
- `LITELLM_USE_V2_MIGRATION_RESOLVER` (default `false`) selects LiteLLM's
  database migration resolver for both proxies. LiteLLM 1.108.x cannot verify
  and baseline this stack's fresh Wolfi-backed databases with its default v2
  resolver, so the stack keeps the documented legacy fallback.

### Changed

- Production enables the LiteLLM Chat UI and accepts master-key sign-in to the
  UI, matching development. The promotion register records the Chat UI as
  `prod-canary`.
- Production LiteLLM moves from `v1.106.6` to the `v1.108.2` LTS release.
  Development tracks LiteLLM's nightly channel and moves from `v1.107.0-dev.3`
  to `v1.110.0-dev.1`. Both remain pinned by digest.

### Fixed

- The LiteLLM upgrade compatibility check now waits for the candidate image's
  startup output to settle before reading it. It previously stopped at the
  first success line and could miss the policy summary printed after it,
  wrongly reporting a compatible development image as incompatible.

## [v0.1.1] - 2026-11-06

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
- The eleven flow diagrams (`diagrams/10`–`20`) are now animated Mermaid
  sequence diagrams that show who sends what to whom, rendered in the
  dashmotion style by the new `scripts/sequence_diagrams.py`. The semantic
  cache sequence no longer shows responses being stored when a request did
  not opt in, matching the cache's `default_off` mode.
- `gpu-host/deploy.py` runs as a uv script (`./deploy.py` or
  `uv run deploy.py`).
- The README says that outside contributions are not accepted at the moment
  and explains how to report a vulnerability privately.

### Fixed

- A release tag now publishes the documentation site through one
  default-branch run, which the `github-pages` environment admits, instead of
  a second, rejected tag deployment.
- The development workflow guide no longer says the documentation site is
  restricted to repository members.

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

[Unreleased]: https://github.com/bella-1337/effective-spoon/compare/v0.3.0...HEAD
[v0.3.0]: https://github.com/bella-1337/effective-spoon/compare/v0.2.1...v0.3.0
[v0.2.1]: https://github.com/bella-1337/effective-spoon/compare/v0.2.0...v0.2.1
[v0.2.0]: https://github.com/bella-1337/effective-spoon/compare/v0.1.1...v0.2.0
[v0.1.1]: https://github.com/bella-1337/effective-spoon/compare/v0.1.0...v0.1.1
[v0.1.0]: https://github.com/bella-1337/effective-spoon/releases/tag/v0.1.0
