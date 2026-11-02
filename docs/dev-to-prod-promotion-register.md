# Development-to-production capability promotion register

This register tracks behavior that exists in the development profile but is not
enabled in production. It is intended to survive repeated LiteLLM upgrades:
promote one capability bundle at a time instead of copying the complete
development configuration into production.

## Register version

| Field | Value |
| --- | --- |
| Register tag | `dev-to-prod-register/v1.0.1` |
| Snapshot date | `2026-10-31` |
| Repository baseline | `8e9b9a0` |
| Production LiteLLM image | `v1.106.6@sha256:37e2dafb088a7723beb998590682957386dfc83ffea1848486f8e0e5cfac0b06` |
| Development LiteLLM image | `v1.107.0-dev.3@sha256:e7f68da02ed3ec52f0b8d386dbb278a0aa06dbc879f5ddda5a3cf87b2bff194c` |

Increment the register tag when the inventory, status model, or promotion
requirements change:

- patch: wording, evidence, or verification corrections;
- minor: add a capability or materially extend its promotion requirements;
- major: change the status model or promotion process.

The register version is independent of LiteLLM versions. A future release delta
must record its own source production version, source development version, and
candidate stable version.

## Status and version tags

Each capability has stable tags:

- `id`: permanent identifier used by release deltas and tests;
- `register-added`: first register version containing the capability;
- `state`: current lifecycle state;
- `stable-support`: first verified stable LiteLLM version, or `unverified`;
- `activation`: expected production activation method.

Allowed states are:

1. `dev-only`
2. `stable-supported`
3. `prod-disabled`
4. `prod-canary`
5. `prod-enabled`
6. `retired`
7. `never-promote`

`stable-supported` requires evidence from the candidate image's own validation
and the repository regression checks. Release notes alone are not sufficient.

## Promotion rules

1. Promote a capability bundle: image support, configuration, dependencies,
   secrets, storage, health checks, monitoring, backup, verification, and
   rollback.
2. Deploy the new stable LiteLLM image with existing production behavior before
   activating newly supported capabilities.
3. Keep development and production hostnames, databases, credentials, caches,
   vector stores, and test data isolated.
4. Give every promoted capability an independent off switch.
5. Pin every image and model artifact used by production to a reviewed immutable
   reference.
6. Do not mark a capability `prod-enabled` until production verification and its
   rollback procedure have both been exercised.

## Current delta summary

| ID | Capability | State | Stable support | Activation |
| --- | --- | --- | --- | --- |
| `D2P-001` | LiteLLM candidate image | `dev-only` | `unverified` | image digest |
| `D2P-002` | Development model entries | mixed | `unverified` | config |
| `D2P-003` | 60-second request timeout | `dev-only` | not version-gated | config |
| `D2P-004` | LiteLLM Chat UI | `dev-only` | `unverified` | config |
| `D2P-005` | Semantic MCP tool filtering | `dev-only` | `unverified` | config |
| `D2P-006` | Semantic response cache | `dev-only` | `unverified` | request opt-in |
| `D2P-007` | Qdrant cache service | `dev-only` | not LiteLLM-only | service |
| `D2P-008` | BGE-M3 embedding service | `dev-only` | not LiteLLM-only | service |
| `D2P-009` | Vector-store registry | `dev-only` | `unverified` | config |
| `D2P-010` | pgvector storage and migration | `dev-only` function | not LiteLLM-only | service |
| `D2P-011` | LiteLLM pgvector connector | `dev-only` | `unverified` | service |
| `D2P-012` | Xberg automatic indexing | `dev-only` | `unverified` | callback |
| `D2P-013` | Feature environment and secrets | `dev-only` | not version-gated | service |
| `D2P-014` | Remote D3-mini decision model | shared | `unverified` | service |
| `D2P-015` | LiteLLM coding Auto Router | shared | `unverified` | config |
| `D2P-016` | D3 DNS, TLS, and network policy | shared | not version-gated | infrastructure |
| `D2P-017` | Automatic demo-data seeding | `never-promote` | not applicable | profile |
| `D2P-018` | Dev database, hostname, and route | `never-promote` | not applicable | isolation |
| `D2P-019` | Feature monitoring and dashboards | `dev-only` | not version-gated | observability |
| `D2P-020` | Feature verification scripts | `dev-only` | not version-gated | tooling |
| `D2P-021` | Upgrade compatibility assertions | `dev-only` | not version-gated | tooling |
| `D2P-022` | Multi-service rollback coverage | `dev-only` | not version-gated | tooling |

## Capability plans

### D2P-001 - LiteLLM candidate image

Tags: `register-added:v1.0.0`, `state:dev-only`,
`stable-support:unverified`, `activation:image-digest`.

Dev uses a prerelease image while prod uses an older stable image.

Promotion plan:

1. Select a stable tag and digest.
2. Run `scripts/check-litellm-upgrade-compatibility.py` before editing Compose.
3. Run dev on that exact stable digest and complete the regression suite.
4. Prepare a rollback point and database backup.
5. Deploy the stable image to prod without enabling new capabilities.
6. Verify schema, authentication, health, guardrails, logging, and metrics.
7. Activate eligible capabilities separately.

### D2P-002 - Development model entries

Tags: `register-added:v1.0.0`, `state:mixed`,
`stable-support:unverified`, `activation:config`.

Dev adds `test-model` and `bge-m3`. Both profiles expose `d3-mini` for direct
Decisions calls and `coding-router` for four-tier coding classification.

Promotion plan:

- `test-model`: tag `state:never-promote`; keep a regression assertion that it is
  absent from production.
- `d3-mini` and `coding-router`: validate with `D2P-014` and `D2P-015`; assign
  budgets, rate limits, tier models, and fallback behavior before broad access.
- `bge-m3`: promote with `D2P-008`; do not expose it before production embedding
  capacity and access policy are defined.

### D2P-003 - Request timeout

Tags: `register-added:v1.0.0`, `state:dev-only`,
`stable-support:not-version-gated`, `activation:config`.

Dev uses 60 seconds and prod uses 120 seconds.

Promotion plan:

1. Measure production provider latency and timeout percentiles.
2. Confirm the target SLO and retry behavior.
3. Test long-running supported requests against the candidate value.
4. Change prod only when the measured SLO supports it.

This difference may remain environment-specific.

### D2P-004 - LiteLLM Chat UI

Tags: `register-added:v1.0.0`, `state:dev-only`,
`stable-support:unverified`, `activation:config`.

Promotion plan:

1. Validate the candidate stable image recognizes `enable_chat_ui`.
2. Review login, session, CSRF, model authorization, and public-route behavior.
3. Restrict the initial rollout to approved users and models.
4. Add an authenticated UI smoke test and operator ownership.
5. Keep a config-only off switch.

### D2P-005 - Semantic MCP tool filtering

Tags: `register-added:v1.0.0`, `state:dev-only`,
`stable-support:unverified`, `activation:config`.

Dev uses BGE-M3 with `top_k: 3` and a `0.3` similarity threshold.

Promotion plan:

1. Promote `D2P-008` first.
2. Evaluate representative multilingual tool descriptions and false exclusions.
3. Verify semantic selection can only reduce the authoritative MCP allowlist and
   can never grant a tool.
4. Define fail-closed or unfiltered fallback behavior explicitly.
5. Canary by team or key and monitor selection latency and errors.

### D2P-006 - Semantic response cache

Tags: `register-added:v1.0.0`, `state:dev-only`,
`stable-support:unverified`, `activation:request-opt-in`.

Dev uses authenticated Qdrant caching with `mode: default_off`, asynchronous chat
completions, end-user scoping, a `0.92` threshold, and BGE-M3 embeddings.

Promotion plan:

1. Promote `D2P-007` and `D2P-008`.
2. Use a distinct production collection and credential.
3. Keep `mode: default_off` for the initial production release.
4. Restrict use to approved single-turn, non-sensitive workloads.
5. Define TTL, invalidation, erasure, purge, storage limits, and false-hit
   monitoring.
6. Test tenant, organization, team, key, and end-user isolation.
7. Prevent opt-in for tool calls, RAG, personalized, permission-sensitive, and
   time-sensitive workflows unless separately approved.
8. Provide a config-only emergency disable path.

### D2P-007 - Qdrant cache service

Tags: `register-added:v1.0.0`, `state:dev-only`,
`stable-support:not-litellm-only`, `activation:service`.

The current dev service stores data in a bounded 1 GB `tmpfs`.

Promotion plan:

1. Create a separate `qdrant-prod` service; do not reuse the dev instance.
2. Use a persistent encrypted volume and a prod-only credential.
3. Define resource limits, retention, snapshots, restore tests, and capacity
   alerts.
4. Keep the service internal and authenticated.
5. Verify restart, corruption recovery, and cache purge procedures.

### D2P-008 - BGE-M3 embedding service

Tags: `register-added:v1.0.0`, `state:dev-only`,
`stable-support:not-litellm-only`, `activation:service`.

Dev calls BGE-M3 on the remote GPU host, served by Text Embeddings Inference
from the `gpu-host/` stack over plain HTTP with an API key. The image and model
revision are pinned in `gpu-host/compose.yml`.

Promotion plan:

1. Select a production embedding service: a separate instance of the remote
   deployment or a managed service. Do not share the dev instance.
2. Serve it over verified TLS, as D3 does; the dev endpoint's plain HTTP is an
   accepted development-only risk.
3. Review the pinned image and model artifact provenance and license.
4. Validate 1,024-dimensional output compatibility and multilingual quality.
5. Capacity-test GPU memory, concurrency, cold starts, and request limits.
6. Define availability and failure behavior for cache, vector search, and MCP
   filtering.
7. Add readiness, latency, GPU, saturation, and error alerts.

### D2P-009 - Vector-store registry

Tags: `register-added:v1.0.0`, `state:dev-only`,
`stable-support:unverified`, `activation:config`.

Dev registers the `sme-knowledge` pgvector store.

Promotion plan:

1. Create a production store identifier and credential.
2. Define document, team, and tenant authorization.
3. Document data classification, residency, retention, export, erasure, and
   restore behavior.
4. Validate the candidate image's registry and search behavior.
5. Expose the store only after `D2P-010` and `D2P-011` pass production checks.

### D2P-010 - pgvector storage and migration

Tags: `register-added:v1.0.0`, `state:dev-only-function`,
`stable-support:not-litellm-only`, `activation:service`.

`vector-postgres`, its migration job, volume, and exporter are currently
unprofiled even though the feature is consumed only by dev.

Promotion plan:

1. Prefer separate dev and prod databases, users, volumes, and backup sets.
2. Apply least-privilege database roles.
3. Version schema migrations and validate forward and rollback compatibility.
4. Add storage and index-growth alerts.
5. Extend database backup and restore scripts to the final topology.
6. Exercise restore before production activation.

### D2P-011 - LiteLLM pgvector connector

Tags: `register-added:v1.0.0`, `state:dev-only`,
`stable-support:unverified`, `activation:service`.

The current connector is built from a fixed upstream commit and calls
`litellm-dev` for embeddings.

Promotion plan:

1. Create a prod connector with a reviewed release or commit and immutable image
   digest.
2. Point it only at the prod vector database and prod embedding endpoint.
3. Use a separate API key and resource limits.
4. Validate unauthenticated rejection, invalid dimensions, unknown stores,
   malformed content, health, and restart behavior.

### D2P-012 - Xberg automatic indexing

Tags: `register-added:v1.0.0`, `state:dev-only`,
`stable-support:unverified`, `activation:callback`.

The dev callback indexes successful Xberg extraction results as a bounded,
best-effort background task.

Promotion plan:

1. Define user/team/tenant/store authorization for ingestion.
2. Treat extracted documents as untrusted content and define prompt-injection
   handling.
3. Add durable job state, idempotency, bounded retry, backpressure, and audit
   events.
4. Support complete source update and erasure.
5. Decide and document whether indexing failure affects extraction success.
6. Keep the callback independently disableable.

### D2P-013 - Feature environment and secrets

Tags: `register-added:v1.0.0`, `state:dev-only`,
`stable-support:not-version-gated`, `activation:service`.

Dev receives pgvector, Qdrant, vector database, and embedding endpoint settings.

Promotion plan:

1. Create distinct production endpoints and secrets.
2. Source them from the production secret-management mechanism.
3. Fail startup when required values are missing.
4. Never reuse dev credentials or publish resolved Compose output.
5. Add rotation and revocation procedures.

### D2P-014 - Remote D3-mini decision model

Tags: `register-added:v1.0.0`, `state:shared`,
`stable-support:unverified`, `activation:service`.

Promotion plan:

1. Deploy the pinned D3-mini CUDA image and model revision on the separate GPU
   host with the `gpu-host/` stack (`gpu-host/deploy.py`).
2. Benchmark memory, latency, throughput, and thermal stability on the actual
   16 GB GPU.
3. Evaluate the four coding tiers on a representative, labeled prompt set.
4. Test malformed requests, oversized input, concurrency, and model unavailability.
5. Keep the D3 credential independent from LiteLLM client credentials and rotate it.
6. Use `d3-nano` if D3-mini lacks safe memory headroom.

### D2P-015 - LiteLLM coding Auto Router

Tags: `register-added:v1.0.0`, `state:shared`,
`stable-support:unverified`, `activation:config`.

Promotion plan:

1. Keep `d3-mini` registered as a Decisions deployment in both profiles.
2. Keep clients on the normal LiteLLM authentication and policy boundary.
3. Review SIMPLE, MEDIUM, COMPLEX, and REASONING model assignments.
4. Preserve the 15-second classifier timeout and fail-safe `gpt-4o` fallback.
5. Verify the classifier response header so fallback cannot masquerade as success.
6. Monitor classifier latency, errors, tier distribution, and fallback rate.

### D2P-016 - D3 DNS, TLS, and network policy

Tags: `register-added:v1.0.0`, `state:shared`,
`stable-support:not-version-gated`, `activation:infrastructure`.

Promotion plan:

1. Allocate an internal DNS name covered by an operator-managed certificate.
2. Restrict the D3 port at the firewall to LiteLLM hosts.
3. Enforce TLS 1.2+ and certificate verification; never add an insecure mode.
4. Expose only `/v1/systemone`, `/v1/decisions`, and model discovery.
5. Define credential and certificate rotation plus rollback procedures.

### D2P-017 - Automatic demo-data seeding

Tags: `register-added:v1.0.0`, `state:never-promote`,
`stable-support:not-applicable`, `activation:profile`.

Dev automatically seeds synthetic teams, users, keys, and spend rows.

Permanent plan:

- Never run automatic demo seeding during normal production startup.
- Retain the explicit production `demo` profile only for disposable
  demonstrations.
- Keep a regression check that normal prod startup creates no demo records.

### D2P-018 - Dev database, hostname, and route

Tags: `register-added:v1.0.0`, `state:never-promote`,
`stable-support:not-applicable`, `activation:isolation`.

Permanent plan:

- Keep `litellm_dev`, `LITELLM_DEV_HOST`, its Traefik router, credentials, users,
  keys, spend logs, and test data separate from production.
- Promote reviewed repository configuration and explicit data migrations, not
  the dev database.

### D2P-019 - Feature monitoring and dashboards

Tags: `register-added:v1.0.0`, `state:dev-only`,
`stable-support:not-version-gated`, `activation:observability`.

Feature monitoring includes LiteLLM classifier metrics and logs, pgvector
dashboards, alerts, and the development PostgreSQL Grafana datasource. The
separate D3 host must be monitored by its infrastructure owner.

Promotion plan:

1. Parameterize dashboards and alerts by environment or service.
2. Add prod scrape targets without removing dev coverage.
3. Monitor cache hits and false-hit indicators, vector growth, embedding latency,
   indexing failures, routing decisions, fallback rates, and feature SLOs.
4. Configure production contact points and notification policies.
5. Test alerts during the canary.

### D2P-020 - Feature verification scripts

Tags: `register-added:v1.0.0`, `state:dev-only`,
`stable-support:not-version-gated`, `activation:tooling`.

The semantic-cache and pgvector verification scripts hardcode dev service,
hostname, store, and collection names.

Promotion plan:

1. Parameterize the scripts by environment or explicit service/host/store
   arguments.
2. Preserve safe defaults that target dev.
3. Use a dedicated production smoke-test tenant and store.
4. Ensure production checks clean up only the records they created.
5. Run dev checks before promotion and prod checks immediately afterward.

### D2P-021 - Upgrade compatibility assertions

Tags: `register-added:v1.0.0`, `state:dev-only`,
`stable-support:not-version-gated`, `activation:tooling`.

The current compatibility script explicitly requires vector storage, semantic
cache, semantic aliases, BGE-M3, and `test-model` to be absent from prod.

Promotion plan:

1. Replace feature-specific absence assertions with expectations derived from a
   capability manifest or this register's release delta.
2. Keep permanent prod prohibitions, such as `test-model`, explicit.
3. Validate `prod-disabled`, `prod-canary`, and `prod-enabled` states separately.
4. Require candidate-image validation for every promoted configuration.

### D2P-022 - Multi-service rollback coverage

Tags: `register-added:v1.0.0`, `state:dev-only`,
`stable-support:not-version-gated`, `activation:tooling`.

Current upgrade state focuses on the two LiteLLM proxy images and their
configuration. Promoted capabilities introduce more images, migrations, and
persistent data.

Promotion plan:

1. Capture every promoted companion image digest and config checksum.
2. Record schema/migration versions, activation state, and backup or snapshot
   identifiers.
3. Define rollback order: disable feature, stop ingestion/workers, restore
   compatible configuration and images, then restore data only if required.
4. Test image-only and data-restoring rollback paths.

## Recurring release workflow

For every new stable LiteLLM release:

1. Create a release delta using
   `docs/prompts/create-dev-to-prod-delta.md`.
2. Record the current prod, current dev, and candidate stable tags and digests.
3. Review release notes, but prove support with candidate-image validation.
4. Classify each `D2P-*` item as unchanged, newly stable-supported, promoted,
   deferred, retired, or blocked.
5. Run dev on the exact candidate stable digest.
6. Prepare isolated production dependencies, secrets, storage, observability,
   backups, and rollback.
7. Deploy the stable image with old production behavior.
8. Canary one capability at a time.
9. Verify correctness, authorization, isolation, latency, cost, resources,
   restart behavior, backup, restore, and rollback.
10. Update this register only after the release delta is reviewed and accepted.

## Release delta history

Add one row per generated and reviewed release delta.

| Delta | From prod | Candidate stable | Result |
| --- | --- | --- | --- |
| None yet | `v1.106.3` | Not selected | Initial register |
