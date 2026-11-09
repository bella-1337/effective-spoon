# LiteLLM Operations Game Day

Use this runbook to verify that the stack fails predictably and can be
recovered. Run it at least quarterly and after material changes to persistence,
alerting, observability, or LiteLLM images.

The database restore drill must use an isolated Compose project and isolated
volumes. The live outage and alert drills intentionally interrupt services;
schedule them in an approved maintenance window.

## 1. Record the exercise

Before changing anything, record:

- incident commander and observers;
- maintenance-window start and end;
- target recovery time objective and recovery point objective;
- current Git commit and rendered image references;
- the backup selected for restore;
- where screenshots, command output, alert timestamps, and follow-up actions
  will be stored.

Never copy `.env`, API keys, database passwords, backup contents, or raw request
payloads into game-day evidence.

## 2. Run the non-disruptive preflight

From the repository root:

```bash
./scripts/maintenance.py game-day-preflight --env-file .env
python3 -m unittest discover -s scripts/tests -v
```

The preflight renders every Compose profile and verifies the contracts required
by the drills: optional Langfuse and LiteLLM Lens wiring, production
independence from Langfuse, Lens, and Xberg, required alert definitions, complete-backup freshness metrics,
rename-aside restore safety, and persistent rollback overrides. Stop if either
command fails.

Also confirm the most recent complete backup is replicated to encrypted,
access-controlled off-host storage and that its `SHA256SUMS` verifies.

## 3. Langfuse outage isolation

This drill applies only when the optional `langfuse` profile is enabled. It
proves that the production proxy remains available when the secondary tracing
backend is unavailable; OpenSearch tracing remains the default path.

1. Send a known, non-sensitive completion through the production proxy and
   record the response status and latency.
2. Stop only the Langfuse application tier:

   ```bash
   docker compose --env-file .env --env-file .env.optional \
     --profile langfuse stop langfuse-web langfuse-worker
   ```

3. Wait two normal request intervals, then repeat the same production
   completion and query the LiteLLM liveness endpoint through its normal HTTPS
   route.
4. Confirm the production response succeeds. Confirm tracing failures are
   visible in logs without request bodies, credentials, or raw PII.
5. Start Langfuse and wait for health:

   ```bash
   docker compose --env-file .env --env-file .env.optional \
     --profile langfuse up -d --wait langfuse-web langfuse-worker
   ```

6. Send one more completion and confirm new tracing resumes.

Record whether production stayed within its availability and latency targets.

When the optional `lens` profile is enabled, repeat the drill for LiteLLM Lens:
stop `lens` with `--profile lens stop lens`, confirm production completions
still succeed and that the gateway logs Lens export failures without request
content, then start it with `--profile lens up -d --wait lens` and confirm
`./scripts/lens.py status` reports both gateways connected. A gateway keeps its
Lens connection across a Lens restart; it does not need to be recreated.

## 4. Backup and restore recovery

Do not run this drill against production volumes.

1. Create a fresh, isolated checkout or host with a unique Compose project name
   and empty database volumes.
2. Copy one complete timestamped backup directory into that environment. Do not
   copy production secrets; configure separate drill credentials.
3. Start only the database services:

   ```bash
   docker compose up -d --wait postgres vector-postgres
   ```

4. Restore the backup:

   ```bash
   ./scripts/restore-databases.py \
     --confirm-restore \
     /path/to/isolated-backup
   ```

5. Confirm the restored databases contain expected schema objects and a
   pre-agreed, non-sensitive canary record.
6. Confirm any prior target was retained as `*_pre_restore_*`.
7. Start the selected LiteLLM profile and execute a completion plus a
   pgvector retrieval. Measure time from restore start to successful checks.
8. After verification, remove only the process-specific safety databases
   created by this drill and destroy the isolated project volumes.

The exercise fails if checksum validation can be bypassed, a live database
client is allowed during promotion, automatic recovery loses the prior target,
or the measured recovery time exceeds the objective.

## 5. Alert delivery

Coordinate with the notification recipient before triggering alerts.

1. In OpenSearch Dashboards (**Alerting → Monitors**, prefixed `[LiteLLM]`), and
   in Grafana when the optional `grafana` profile runs, confirm these
   provisioned rules are healthy:
   - Production LiteLLM is unavailable
   - LiteLLM PostgreSQL is unavailable
   - LiteLLM host disk space is low
   - Complete database backup is stale
2. Stop `litellm-prod`, wait beyond the rule's five-minute `for` duration, and
   confirm the production-unavailable notification is delivered:

   ```bash
   docker compose stop litellm-prod
   ```

3. Restore production and confirm the alert resolves:

   ```bash
   docker compose --profile prod up -d --wait litellm-prod
   ```

4. Validate the backup-stale rule in a non-production Prometheus/OpenSearch pair by
   withholding the complete-backup metric beyond its threshold. A partial
   backup must not advance the metric.
5. Review the disk and PostgreSQL rules in the **Security, Backups & Alerts**
   dashboard (latest evaluated state) or Grafana's expression preview. Trigger
   them only in an isolated project; do not fill a production disk or stop the
   production database for this exercise.

Record pending, firing, delivered, acknowledged, and resolved timestamps.

## 6. Rollback persistence

Use a planned test upgrade or an isolated project with representative data.

1. Run the documented upgrade preparation and retain its state directory and
   complete backup.
2. Deploy the candidate image and execute the normal verification checks.
3. Invoke image-only rollback:

   ```bash
   ./scripts/rollback-litellm-upgrade.py \
     --confirm-rollback \
     --mode image-only
   ```

4. Confirm `docker-compose.override.yml` exists at the repository root, contains
   `x-litellm-stack-active-rollback: true`, and renders the captured prior image:

   ```bash
   docker compose config --images
   ```

5. Run an ordinary `docker compose up -d` without extra `-f` flags and confirm
   the prior image remains selected after container recreation.
6. If database compatibility is part of the exercise, repeat in the isolated
   project with `--mode restore-db` and rerun the restore checks from section 4.

Do not delete the override until the incident commander approves returning to
the version declared in the main Compose file.

## 7. Closeout

- Restore every intentionally stopped service and confirm Compose health.
- Confirm production completions, tracing, metrics, logs, and dashboards.
- Verify no drill containers, safety databases, temporary overrides, or
  isolated volumes remain.
- Compare measured recovery and alert times with the objectives.
- Create tracked follow-up work for every failed or ambiguous check, with an
  owner and due date.
