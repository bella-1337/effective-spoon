# Validate and roll back a LiteLLM image upgrade

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

Before evaluating which development-only capabilities can move to production,
review the versioned
[`development-to-production capability promotion register`](dev-to-prod-promotion-register.md).
For each new stable LiteLLM candidate, use the repository-owned
[`release-delta prompt`](prompts/create-dev-to-prod-delta.md) to create a
separate evidence-based delta. Image upgrades and capability activation are
separate rollout steps.

Before any image upgrade, run `./scripts/maintenance.py doctor`. This also
enforces the dated runtime policy in `security/runtime-lifecycle.toml`. When a
runtime review is due, complete the
[runtime lifecycle upgrade](image-security.md#runtime-lifecycle-upgrades) in
the same tested rollout; do not postpone it by changing only the review date.

The single maintenance entry point exposes the routine operational workflows:

```bash
./scripts/maintenance.py help
./scripts/maintenance.py doctor
./scripts/maintenance.py status
./scripts/maintenance.py game-day-preflight
./scripts/maintenance.py backup
./scripts/maintenance.py post-upgrade
```

It delegates to the focused scripts below without weakening their validation,
confirmation flags, or exit codes. Restore and rollback remain explicit:

```bash
./scripts/maintenance.py restore \
  --confirm-restore \
  /secure/litellm-backups/<backup>

./scripts/maintenance.py rollback \
  --confirm-rollback \
  --mode image-only
```

Use an immutable candidate reference containing both a tag and digest. Run the
compatibility check before editing `docker-compose.yml`; `--pull` is required
only when the candidate is not already present locally:

```bash
CANDIDATE_IMAGE='ghcr.io/berriai/litellm-database:<version>@sha256:<digest>'

./scripts/check-litellm-upgrade-compatibility.py \
  --candidate-image "$CANDIDATE_IMAGE" \
  --pull
```

The compatibility check and rollback-point preparation can also run as one
fail-fast command:

```bash
./scripts/maintenance.py pre-upgrade \
  --candidate-image "$CANDIDATE_IMAGE" \
  --pull
```

The compatibility script never reads `.env`, connects to the stack databases,
or changes running services. It performs static configuration checks and runs
the candidate's own configuration validator in network-isolated, read-only,
capability-dropped containers with placeholder credentials.

Immediately before changing the image digest, capture the deployed images,
configuration checksums, and a fresh database backup:

```bash
./scripts/prepare-litellm-upgrade.py
```

The resulting private state is stored under ignored `upgrade-state/`. Do not
edit or move its files independently: the rollback script validates their
checksums and configuration drift. `--skip-backup` is available only when an
image-only rollback is sufficient and the database schema cannot become
incompatible.

After updating both LiteLLM image references to the tested digest and recreating
the proxies, run the complete regression suite:

```bash
./scripts/verify-litellm-upgrade.py
```

List or select checks when diagnosing a failure:

```bash
./scripts/verify-litellm-upgrade.py --list
./scripts/verify-litellm-upgrade.py --only compose-health,prod-auth,logs
```

The suite checks Compose health and exposure, authentication, prod/dev
isolation, vLLM embeddings, vLLM Semantic Router routing,
pgvector, Presidio behavior, SearXNG, Xberg MCP extraction, Prometheus targets,
OpenSearch health, dashboards, monitors, data sources, bridge freshness, and log
delivery, plus the Grafana data sources and Loki log delivery when the optional
`grafana` profile runs. A
missing optional prerequisite is reported as `SKIP`; once a prerequisite is
present, a failed assertion makes the command exit nonzero.

If the prior image can read the current schema, roll back images only:

```bash
./scripts/rollback-litellm-upgrade.py \
  --confirm-rollback \
  --mode image-only
```

If the upgrade migrated the database incompatibly, restore the pre-upgrade
backup and images together:

```bash
./scripts/rollback-litellm-upgrade.py \
  --confirm-rollback \
  --mode restore-db
```

Rollback refuses configuration drift by default, uses an exclusive lock,
validates immutable image references and state checksums, stops application
services before database restoration, checks proxy liveliness afterward, and
writes a private report. It leaves application services stopped when database
restoration fails.

The runtime rollback writes the validated image override to the ignored
`docker-compose.override.yml` file, which Docker Compose loads automatically.
The override remains active across ordinary `docker compose up` and maintenance
commands until the pinned image in `docker-compose.yml` is deliberately updated
and the override is removed. A copy is also retained with the rollback report.
