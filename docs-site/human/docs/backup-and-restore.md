<!-- human-version-of: docs/backup-and-restore.md sha256: f5488b6ce5eb669ee3cdd94e41002b1782c6a4d8d81dba7476656db38e05b009 -->

# Back up and restore the databases

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

This page shows how to back up the stack's PostgreSQL databases, keep the
backups safe, and restore them into a new instance. Read it when you set up
scheduled backups, plan for host loss, or need to recover data.

## Take a backup

Run the repository-owned backup script while `postgres` and `vector-postgres`
are healthy:

```bash
./scripts/backup-databases.py
```

The script:

1. creates PostgreSQL custom-format dumps for `litellm_prod`, `litellm_dev`,
   and `vector_store`;
2. validates that `pg_restore` can read each archive;
3. writes SHA-256 checksums; and
4. atomically publishes the completed directory under `backups/`.

Backup directories and files are private to the current operating system user.
If the run fails, the script removes the partial backup and never publishes it
as complete.

!!! note "PostgreSQL only"
    The script covers PostgreSQL only. Optional profiles keep their own state:
    see [Back up and restore](lens.md#back-up-and-restore) in the LiteLLM Lens
    guide for the `lens-clickhouse-data` volume.

### When a database is missing or unavailable

If a configured database service is unavailable, or a database does not exist,
the script:

- writes a warning;
- records the reason in `metadata.txt`;
- continues with the next database;
- still publishes a backup with the remaining resources, whose checksum
  manifest lists only the files that were created; and
- exits nonzero and does not prune older backups.

Skipping the pruning stops a run of partial backups from aging out the last
complete recovery point.

The script fails without publishing anything when no configured database is
available, so it never publishes an empty backup. Errors while dumping or
validating an existing database stay fatal, so a corrupt archive is never
reported as successful.

### The backup-age metric

After a complete backup, the script atomically publishes
`litellm_backup_last_success_timestamp_seconds` in
`security-metrics/litellm_backup.prom`. Node exporter exposes that value to
Prometheus.

Partial and failed backups do not advance it. That is how the provisioned
backup-age alert detects a stale recovery point.

## Choose where backups go and how long they stay

Pick a destination on encrypted storage. The default retention is 14 days;
change it when needed:

```bash
BACKUP_DIR=/secure/litellm-backups \
BACKUP_RETENTION_DAYS=30 \
./scripts/backup-databases.py
```

Retention cleanup deletes only directories that the script created, with:

- the expected name;
- metadata;
- a checksum manifest; and
- at least one known database dump.

A directory that contains an unexpected file, subdirectory, or symbolic link
is kept, and the script prints a warning.

### Run it daily with cron

For a daily cron job at 02:15 UTC, use absolute paths for the repository and
for `uv`. Find the `uv` path with `command -v uv` in the account that owns the
cron job:

```cron
CRON_TZ=UTC
15 2 * * * cd /absolute/path/to/litellm-stack && BACKUP_DIR=/secure/litellm-backups BACKUP_RETENTION_DAYS=30 /absolute/path/to/uv run --script ./scripts/backup-databases.py >>/var/log/litellm-backup.log 2>&1
```

## What the backup leaves out

The script intentionally does not copy:

- `.env`;
- TLS private keys; or
- live OpenSearch, Grafana, Loki, and Prometheus volume files.

Keep secrets in their existing secret-management system. If you must keep the
historical data of those observability volumes, use storage-level snapshots
designed for them.

!!! warning "A local backup is not enough"
    A local backup alone does not protect against host loss. Replicate
    completed backups to access-controlled, encrypted off-host storage, and
    test restores regularly.

## Restore into a new instance

1. Copy the complete timestamped backup directory to the target host.
2. Configure its `.env`.
3. Start only the database services, then run the restore:

```bash
docker compose up -d --wait postgres vector-postgres
./scripts/restore-databases.py \
  --confirm-restore \
  /secure/litellm-backups/<backup>
```

The `--confirm-restore` flag is mandatory, because each database found in the
backup replaces its current target.

### Checks before anything changes

The restore script refuses to run while any of these is running:
`litellm-prod`, `litellm-dev`, `litellm-pgvector`, Grafana,
`opensearch-bridge`, or the pgvector exporter.

Before it changes a target database, it rejects:

- symbolic links;
- unexpected files;
- unknown databases;
- malformed metadata;
- unsafe checksum entries; and
- checksum mismatches.

### How the restore protects the existing data

The script supports full and fault-tolerant partial backups:

- A database omitted from the backup metadata is skipped.
- If a required target database service is unavailable, the script records
  that restore as failed, continues with the remaining databases, and exits
  nonzero after reporting all failures.

For each archive, the script:

1. restores it with a single transaction into a staging database;
2. checks the staging database for restored tables before it replaces the
   existing target, so an archive error leaves the existing target untouched;
3. before promotion, renames an existing target to a process-specific
   `*_pre_restore_*` safety database instead of dropping it.

The script terminates stale database sessions. If promotion or the
post-promotion connection check fails, it automatically restores the prior
database.

After a successful application-level verification, remove the preserved safety
database explicitly to reclaim its storage.

!!! warning "Checksums are not signatures"
    `SHA256SUMS` detects accidental corruption but is not a cryptographic
    signature: someone able to replace both a dump and its manifest can
    produce matching new checksums. Use access-controlled backup storage, and
    add signed manifests through the organization's backup system when you
    need integrity against a malicious writer.

## Test the procedure

Test the procedure in a non-production instance regularly. After a successful
restore, start the application services and rerun the
[stack verification checks](getting-started.md#8-verify-the-stack).

For the quarterly failure and recovery exercise, follow
[`OPERATIONS-GAME-DAY.md`](../OPERATIONS-GAME-DAY.md). Its non-disruptive
preflight checks the topology, alert, backup, restore, and rollback contracts
before any live drill begins:

```bash
./scripts/maintenance.py game-day-preflight --env-file .env
```
