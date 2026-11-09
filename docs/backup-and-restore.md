# Back up and restore the databases

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

Run the repository-owned backup script while `postgres` and `vector-postgres`
are healthy:

```bash
./scripts/backup-databases.py
```

It creates PostgreSQL custom-format dumps for `litellm_prod`, `litellm_dev`, and
`vector_store`, validates that each archive can be read by `pg_restore`, writes
SHA-256 checksums, and atomically publishes the completed directory under
`backups/`. Backup directories and files are private to the current operating
system user. Partial backups are removed on failure and are never published as
complete.

The script covers PostgreSQL only. Optional profiles keep their own state:
see [Back up and restore](lens.md#back-up-and-restore) in the LiteLLM Lens
guide for the `lens-clickhouse-data` volume.

If a configured database service is unavailable or a database does not exist,
the script writes a warning, records the reason in `metadata.txt`, and continues
with the next database. A backup containing the remaining resources is still
published and its checksum manifest includes only files that were created, but
the command exits nonzero and does not prune older backups. This prevents a run
of partial backups from aging out the last complete recovery point. The script
fails without publishing an empty backup when no configured database is
available. Errors while dumping or validating an existing database remain fatal
so a corrupt archive is never reported as successful.

After a complete backup, the script atomically publishes
`litellm_backup_last_success_timestamp_seconds` in
`security-metrics/litellm_backup.prom`. Node exporter exposes that value to
Prometheus. Partial and failed backups do not advance it, so the provisioned
backup-age alert detects a stale recovery point.

Choose a destination on encrypted storage and change the default 14-day
retention when needed:

```bash
BACKUP_DIR=/secure/litellm-backups \
BACKUP_RETENTION_DAYS=30 \
./scripts/backup-databases.py
```

Only directories created by the script with the expected name, metadata,
checksum manifest, and at least one known database dump are eligible for
retention cleanup. A directory containing an unexpected file, subdirectory, or
symbolic link is retained and produces a warning.

For a daily cron job at 02:15 UTC, use absolute repository and `uv` paths.
Find the latter with `command -v uv` in the account that owns the cron job:

```cron
CRON_TZ=UTC
15 2 * * * cd /absolute/path/to/litellm-stack && BACKUP_DIR=/secure/litellm-backups BACKUP_RETENTION_DAYS=30 /absolute/path/to/uv run --script ./scripts/backup-databases.py >>/var/log/litellm-backup.log 2>&1
```

The script intentionally does not copy `.env`, TLS private keys, or live
OpenSearch, Grafana, Loki, and Prometheus volume files. Keep secrets in their existing
secret-management system. Use storage-level snapshots designed for those
observability volumes if their historical data must be retained. Replicate
completed backups to access-controlled, encrypted off-host storage and test
restores regularly; a local backup alone does not protect against host loss.

To restore into a new instance, copy the complete timestamped backup directory
to the target host, configure its `.env`, and start only the database services:

```bash
docker compose up -d --wait postgres vector-postgres
./scripts/restore-databases.py \
  --confirm-restore \
  /secure/litellm-backups/<backup>
```

The confirmation flag is mandatory because each database found in the backup
replaces its current target. The restore script refuses to run while
`litellm-prod`, `litellm-dev`, `litellm-pgvector`, Grafana, `opensearch-bridge`,
or the pgvector exporter is running. It rejects symbolic links, unexpected files, unknown
databases, malformed metadata, unsafe checksum entries, and checksum mismatches
before changing a target database.

Full and fault-tolerant partial backups are supported. A database omitted from
the backup metadata is skipped. If a required target database service is
unavailable, the script records that restore as failed, continues with the
remaining databases, and exits nonzero after reporting all failures. Each
archive is restored with a single transaction into a staging database and
checked for restored tables before the existing target is replaced. An archive
error therefore leaves the existing target untouched. Before promotion, an
existing target is renamed to a process-specific `*_pre_restore_*` safety
database rather than dropped. The script terminates stale database sessions and
automatically restores that prior database if promotion or the post-promotion
connection check fails. After a successful application-level verification,
remove the preserved safety database explicitly to reclaim its storage.

`SHA256SUMS` detects accidental corruption but is not a cryptographic signature:
someone able to replace both a dump and its manifest can produce matching new
checksums. Use access-controlled backup storage and add signed manifests through
the organization's backup system when integrity against a malicious writer is
required.

Test the procedure in a non-production instance regularly. After a successful
restore, start the application services and rerun the
[stack verification checks](getting-started.md#8-verify-the-stack).

For the quarterly failure and recovery exercise, follow
[`OPERATIONS-GAME-DAY.md`](../OPERATIONS-GAME-DAY.md). Its non-disruptive preflight
checks the topology, alert, backup, restore, and rollback contracts before any
live drill begins:

```bash
./scripts/maintenance.py game-day-preflight --env-file .env
```
