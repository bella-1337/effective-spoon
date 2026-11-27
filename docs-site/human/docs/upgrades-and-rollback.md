<!-- human-version-of: docs/upgrades-and-rollback.md sha256: fd2bc09c6e39017c8aa96da832e42b650c37f79af14b13814e86c969350569ad -->

# Validate and roll back a LiteLLM image upgrade

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

This page walks through a LiteLLM image upgrade: check the candidate, capture a
rollback point, verify the result, and roll back if needed. Read it before you
change a LiteLLM image digest.

In short:

1. Run `./scripts/maintenance.py doctor`.
2. Check the candidate image for compatibility.
3. Capture a rollback point with `./scripts/prepare-litellm-upgrade.py`.
4. Change the image, recreate the proxies, and run
   `./scripts/verify-litellm-upgrade.py`.
5. If something breaks, roll back images only, or images and database
   together.

## Before you upgrade

Image upgrades and capability activation are separate rollout steps.

- Before you evaluate which development-only capabilities can move to
  production, review the versioned
  [`development-to-production capability promotion register`](dev-to-prod-promotion-register.md).
- For each new stable LiteLLM candidate, use the repository-owned
  [`release-delta prompt`](prompts/create-dev-to-prod-delta.md) to create a
  separate evidence-based delta.

Before any image upgrade, run `./scripts/maintenance.py doctor`. It also
enforces the dated runtime policy in `security/runtime-lifecycle.toml`. When a
runtime review is due, complete the
[runtime lifecycle upgrade](image-security.md#runtime-lifecycle-upgrades) in
the same tested rollout. Do not postpone it by changing only the review date.

## The maintenance entry point

One maintenance entry point exposes the routine operational workflows:

```bash
./scripts/maintenance.py help
./scripts/maintenance.py doctor
./scripts/maintenance.py up --profile prod -d
./scripts/maintenance.py status
./scripts/maintenance.py game-day-preflight
./scripts/maintenance.py backup
./scripts/maintenance.py post-upgrade
```

It delegates to the focused scripts below, and keeps their validation,
confirmation flags, and exit codes. Restore and rollback stay explicit:

```bash
./scripts/maintenance.py restore \
  --confirm-restore \
  /secure/litellm-backups/<backup>

./scripts/maintenance.py rollback \
  --confirm-rollback \
  --mode image-only
```

## Check the candidate image

Use an immutable candidate reference that contains both a tag and a digest.
Run the compatibility check before you edit `docker-compose.yml`. `--pull` is
required only when the candidate is not already present locally:

```bash
CANDIDATE_IMAGE='ghcr.io/berriai/litellm-database:<version>@sha256:<digest>'

./scripts/check-litellm-upgrade-compatibility.py \
  --candidate-image "$CANDIDATE_IMAGE" \
  --pull
```

You can also run the compatibility check and the rollback-point preparation as
one fail-fast command:

```bash
./scripts/maintenance.py pre-upgrade \
  --candidate-image "$CANDIDATE_IMAGE" \
  --pull
```

### What the compatibility check does

The compatibility script never reads `.env`, never connects to the stack
databases, and never changes running services. It:

- performs static configuration checks;
- confirms that the candidate image provides a launcher the gateway start
  commands use; and
- runs the candidate's own configuration validator in network-isolated,
  read-only, capability-dropped containers with placeholder credentials.

??? note "How the validator builds the config it checks"
    The validator starts the way the gateways do, through
    `litellm/modules/prepare_runtime_config.py`, so it checks the generated
    runtime config rather than the raw YAML.

    It makes `langfuse-web` and `lens` resolve to an unreachable loopback
    address. The launcher then keeps the Langfuse callbacks and adds Lens
    tracing: the fullest config a gateway can run. Every other start only
    removes settings from it.

    The check fails if the launcher does not report both integrations as
    enabled.

## Capture a rollback point

Immediately before you change the image digest, capture the deployed images,
configuration checksums, and a fresh database backup:

```bash
./scripts/prepare-litellm-upgrade.py
```

The resulting private state is stored under the ignored `upgrade-state/`
directory.

!!! warning
    Do not edit or move its files independently: the rollback script validates
    their checksums and configuration drift.

`--skip-backup` is available only when an image-only rollback is sufficient and
the database schema cannot become incompatible.

## Verify the upgrade

Update both LiteLLM image references to the tested digest and recreate the
proxies. Then run the complete regression suite:

```bash
./scripts/verify-litellm-upgrade.py
```

When you diagnose a failure, list or select checks:

```bash
./scripts/verify-litellm-upgrade.py --list
./scripts/verify-litellm-upgrade.py --only compose-health,prod-auth,logs
```

The suite checks:

- Compose health and exposure;
- authentication;
- prod/dev isolation;
- vLLM embeddings;
- vLLM Semantic Router routing;
- pgvector;
- Presidio behavior;
- SearXNG;
- Xberg MCP extraction;
- Prometheus targets;
- OpenSearch health, dashboards, monitors, data sources, bridge freshness, and
  log delivery; and
- the Grafana data sources and Loki log delivery, when the optional `grafana`
  profile runs.

A missing optional prerequisite is reported as `SKIP`. Once a prerequisite is
present, a failed assertion makes the command exit nonzero.

## Roll back

Pick the mode by what the upgrade did to the database.

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

### What rollback checks and does

Rollback:

- refuses configuration drift by default;
- uses an exclusive lock;
- validates immutable image references and state checksums;
- stops application services before database restoration;
- checks proxy liveliness afterward; and
- writes a private report.

It leaves application services stopped when database restoration fails.

### The rollback override stays active

The runtime rollback writes the validated image override to the ignored
`docker-compose.override.yml` file, which Docker Compose loads automatically.

The override remains active across ordinary `docker compose up` and maintenance
commands. It stays until you deliberately update the pinned image in
`docker-compose.yml` and remove the override. A copy is also kept with the
rollback report.
