# LiteLLM Stack Review Summary

> This is the baseline five-model review captured before remediation. The
> subsequent commits on this branch address the recommended correction groups
> in order; retain this report as the audit input rather than treating every
> finding below as current state.

## Executive verdict

I completed **five review rounds with five different models**:

1. **Claude Opus 5** - architecture and reliability
2. **GPT-5.6 Terra** - security and supply chain
3. **Claude Sonnet 5.5** - SRE and operations
4. **GPT-5.3 Codex** - adversarial verification of disputed claims
5. **Claude Opus 4.8** - final severity adjudication

The result is consistent: this is a **well-engineered private, single-host development stack with unusually careful maintenance scripts**, but its runtime wiring and recovery posture are not yet safe enough to treat the `prod` profile as production-like.

My critical rating:

- **Private workstation/development use:** good, roughly **7/10**
- **Important internal service:** needs corrections, roughly **5/10**
- **Public or business-critical production:** **not ready**

The biggest mistake would be interpreting the `prod` profile as a production deployment. The repository correctly warns that it is local/private and mkcert-based, but the naming still invites overconfidence.

## What is actually wrong

### 1. Critical: LiteLLM Prometheus metrics are broken on normal Linux hosts

The Prometheus image runs as `nobody`, while the setup instructs the operator to create a host-owned credential file with mode `0600`. That file is bind-mounted into Prometheus, so it is unreadable on Linux. Both LiteLLM scrape jobs then fail, dashboards lose application metrics, and alerts gated on `up{job="litellm-dev"}` cannot fire.

Evidence: [docker-compose.yml:378](docker-compose.yml#L378), [README.md:302](README.md#L302), [prometheus.yml:5](litellm/prometheus.yml#L5).

**Correction:** use a Docker secret, or a shared group with `0640` plus `group_add`. Add a doctor check that executes `test -r` inside the Prometheus container.

### 2. Critical: production proxy recovery depends on non-critical tracing and extraction services

`litellm-prod` cannot start or be recreated unless Xberg and Langfuse are healthy. Langfuse itself requires PostgreSQL, ClickHouse, Redis, and MinIO. A tracing or extraction outage can therefore prevent recovery of the core LLM gateway. Xberg's healthcheck only runs `xberg --version`, which does not verify its HTTP server.

Evidence: [docker-compose.yml:587](docker-compose.yml#L587), especially [the dependency gates](docker-compose.yml#L613) and [Xberg's probe](docker-compose.yml#L370).

**Correction:** remove these hard health dependencies from the production proxy, let telemetry degrade explicitly, and give Xberg a real readiness probe.

### 3. High: a partial backup can age out the last complete backup

The backup script skips an unavailable database, publishes a partial backup successfully, and then performs age-based pruning unconditionally. If one database remains unavailable longer than the retention period, older complete backups can be deleted while only partial backups remain.

Evidence: [backup-databases.py:96](scripts/backup-databases.py#L96) and [backup-databases.py:145](scripts/backup-databases.py#L145).

**Correction:** do not prune after a partial run; return non-zero or a distinct degraded status; preserve the newest backup containing every configured database; emit a backup-age metric and alert.

Backup scope is also incomplete by design: Langfuse PostgreSQL, ClickHouse, MinIO, Redis, Grafana, Loki, and Prometheus state are not covered. The documentation discloses this, but it remains a production blocker rather than merely a documentation note.

### 4. High: rollback is not durable

The rollback tool starts LiteLLM with a temporary override and saves a copy under reports, but the main Compose file remains pinned to the failed image. A later ordinary `docker compose up` silently deploys the bad version again.

Evidence: [rollback-litellm-upgrade.py:294](scripts/rollback-litellm-upgrade.py#L294) and [rollback-litellm-upgrade.py:339](scripts/rollback-litellm-upgrade.py#L339).

**Correction:** persist an active rollback override in stable state and make all maintenance commands include it, or make rollback revert the image pin in source control. `doctor` should fail if a saved rollback is not active.

### 5. High: there is no effective production alerting path

Provisioned alerts cover the development semantic router and image scanning, but not production LiteLLM availability, error rate, latency, PostgreSQL, disk pressure, backup age, TLS expiry, or telemetry ingestion. No contact point or policy is provisioned. Eight image-security rules also use `noDataState: Alerting` even though the security profile is optional, creating an alert storm once notifications are configured without that profile.

Evidence: [grafana/alerting/](grafana/alerting/), [vllm-semantic-router.yml](grafana/alerting/vllm-semantic-router.yml), and [image-security.yml](grafana/alerting/image-security.yml).

Grafana also lacks `GF_SECURITY_SECRET_KEY`, so UI-stored notification secrets rely on Grafana's default encryption key.

**Correction:** set an environment-sourced Grafana secret key, provision contact points/policies securely, add production and dead-man alerts, and make optional-profile rules tolerate absent metrics.

### 6. High: Grafana connects to PostgreSQL as the database bootstrap superuser

The same `${PG_USER}` creates the cluster and is used by both Grafana datasources and exporters. A Grafana user able to edit SQL-backed panels receives far more database authority than required. A correct read-only role is already suggested, but only as commented SQL.

Evidence: [docker-compose.yml:82](docker-compose.yml#L82), [litellm-pg.yml:4](grafana/datasources/litellm-pg.yml#L4), [litellm-pg-dev.yml:4](grafana/datasources/litellm-pg-dev.yml#L4), and [verify-schema.sql:15](scripts/verify-schema.sql#L15).

**Correction:** provision separate least-privilege roles for Grafana and exporters, restrict them to required views/tables, and cap datasource connections.

### 7. High operational risk: container logs are unbounded

None of the 35 services defines Docker logging limits. The default `json-file` logs can fill the same host disk that holds databases, metrics, traces, and backups. This is more serious than the absence of CPU limits in a declared single-host environment.

Evidence: the service definitions throughout [docker-compose.yml](docker-compose.yml).

**Correction:** apply a shared logging anchor with `max-size` and `max-file`, add host/filesystem alerts, and add a Prometheus storage-size cap.

### 8. High security risk: authenticated Xberg users get an SSRF-capable fetcher on the flat internal network

Xberg accepts caller-controlled remote URLs, is available through both LiteLLM profiles, and shares the same `internal` network as unauthenticated or highly privileged service APIs. The README accurately warns that internal placement does not block private destinations, but the risk remains implemented rather than mitigated.

Evidence: [README.md:548](README.md#L548), [docker-compose.yml:336](docker-compose.yml#L336), [config.prod.yaml:16](litellm/config.prod.yaml#L16), and [config.dev.yaml:40](litellm/config.dev.yaml#L40).

**Correction:** place Xberg behind an outbound allowlisting proxy, block loopback/RFC1918/link-local/cloud-metadata destinations after DNS resolution, isolate it from observability and database networks, and restrict MCP access to explicit trusted keys or teams.

### 9. Medium-high security risk: Docker socket mounts provide host-root blast radius

The Docker socket is mounted into Traefik and root-running Alloy. A read-only socket mount does **not** make Docker API operations read-only; compromise of either container can reach host-root-equivalent Docker API operations. cAdvisor is also privileged with host mounts.

Evidence: [docker-compose.yml:53](docker-compose.yml#L53), [docker-compose.yml:407](docker-compose.yml#L407), and [docker-compose.yml:883](docker-compose.yml#L883).

**Correction:** use scoped socket proxies for Traefik and Alloy, remove Alloy's root user, and explicitly accept or replace cAdvisor's privileged deployment.

## Important but secondary improvements

- The shared development RAG store intentionally shares data, so I do **not** classify it as an accidental tenant breach. However, every successful extraction is ingested without opt-in, principal metadata, PII processing, or source approval, and the callback writes directly to PostgreSQL. Improve [xberg_auto_index.py](litellm/modules/xberg_auto_index.py) before using it with non-public content.
- Change `usage_bytes` from `integer` to `bigint` in [init.sql:8](pgvector/init.sql#L8).
- Pin downloaded dashboards to immutable revisions/commits and verify checksums in [fetch-dashboards.py](scripts/fetch-dashboards.py).
- Add CI. Existing tests cover image-security tooling but not backup, restore, rollback, callbacks, Compose behavior, or dashboard/alert validity.
- Split the GPU-backed embedding service into an optional profile or align documentation with its 15-minute health start period. The current documented 150-second wait cannot cover it.
- The restore flow is already staging-first and preserves the restored staging database if promotion fails. Improve it by renaming the old database aside or taking a safety dump; do not describe it as silent permanent data loss.
- [ARCHITECTURE.md](ARCHITECTURE.md) is materially behind the stack: it omits many volumes, the security network, Langfuse, Qdrant, pgvector, Xberg, and the semantic router.

## Claims rejected or deliberately down-ranked

- The SearXNG secret is **not inert**; the pinned image applies `SEARXNG_SECRET` after merging settings.
- Automatic dev demo-data seeding is documented and intentional, not a correctness bug.
- Lack of HA, managed PostgreSQL, and multiple replicas is not a defect for the explicitly declared private single-host scope.
- The restore flow has a short cutover/recovery window, but it does not silently discard the successfully restored staging database.

## What the stack does well

- Images are mostly digest-pinned.
- Maintenance subprocesses avoid shells and use defensive filesystem handling.
- Backup files use custom PostgreSQL format, checksums, private permissions, and atomic publication.
- Restore validates into a staging database before cutover.
- Several risky containers use `cap_drop`, `no-new-privileges`, read-only filesystems, and bounded temporary storage.
- Privacy limitations, Langfuse access limitations, internal plaintext transport, and incomplete backup scope are documented honestly.

## Recommended correction order

1. Fix Prometheus secret readability and Grafana's encryption key.
2. Stop pruning after partial backups; make rollback persistent.
3. Remove Xberg/Langfuse health gates from `litellm-prod`.
4. Add log rotation, disk monitoring, backup-age monitoring, and production alerts.
5. Replace Grafana/exporter superuser access with read-only roles.
6. Isolate and constrain Xberg egress; proxy Docker socket access.
7. Improve RAG ingestion/privacy, restore rollback safety, dashboard pinning, CI, and architecture documentation.
8. Run a game day: simulate Langfuse failure, perform backup/restore, verify alerts, and confirm rollback survives an ordinary Compose restart.

## Validation

- Worktree was clean before this report was added; no stack implementation files were modified.
- Docker Compose configuration passed for prod, dev, demo, and security profiles using `.env.example`.
- Existing Python compilation passed.
- Existing unit tests passed: **26 tests, 1 skipped**.

Overall: **do not redesign the stack from scratch**. Its foundations are good. Correct the runtime dependency graph, least privilege, observability, and recovery semantics first.
