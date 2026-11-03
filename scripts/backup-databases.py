#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Create verified logical PostgreSQL backups for the LiteLLM stack."""

from __future__ import annotations

import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from maintenance_common import (
    LOG,
    BACKUP_NAME_RE,
    ROOT,
    compose,
    compose_command,
    compose_from_file,
    container_environment,
    fail,
    require_docker,
    run_main,
    run_to_file,
    safe_output_root,
    secure_tempdir,
    service_is_running,
    utc_timestamp,
    write_checksum_manifest,
    write_private_file,
)
from maintenance_contracts import backup_freshness_metrics

DATABASES = (
    ("postgres", "litellm_prod", "litellm_prod.dump"),
    ("postgres", "litellm_dev", "litellm_dev.dump"),
    ("vector-postgres", "vector_store", "vector_store.dump"),
)

USAGE = """Usage: scripts/backup-databases.py

Creates logical backups of:
  - litellm_prod
  - litellm_dev
  - vector_store

Unavailable services and missing databases are recorded and skipped. The
command fails when no configured database can be backed up.

Environment:
  BACKUP_DIR             Destination directory (default: ./backups)
  BACKUP_RETENTION_DAYS  Remove complete backups older than this many days
                         (default: 14)
"""


def usage(stream: object = sys.stdout) -> None:
    print(USAGE, file=stream, end="")


def database_exists(service: str, database: str) -> int:
    result = compose(
        [
            "exec", "-T", service, "psql",
            "--username", container_environment(service, "POSTGRES_USER"),
            "--dbname", "postgres", "--list", "--tuples-only", "--no-align",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        return 2
    assert isinstance(result.stdout, str)
    return 0 if database in {line.partition("|")[0] for line in result.stdout.splitlines()} else 1


def dump_database(service: str, database: str, destination: Path) -> None:
    LOG.info(f"Backing up {database} from {service}...")
    dump = compose_command([
        "exec", "-T", service, "pg_dump",
        "--username", container_environment(service, "POSTGRES_USER"),
        "--dbname", database, "--format=custom", "--compress=9",
        "--lock-wait-timeout=30s", "--no-owner", "--no-privileges",
    ])
    run_to_file(dump, destination)
    result = compose_from_file(
        ["exec", "-T", service, "pg_restore", "--list"],
        destination,
        check=False,
    )
    if result.returncode:
        fail(f"backup failed for existing database '{database}' in service '{service}'")


def safe_backup_directory(path: Path) -> bool:
    entries = list(path.iterdir())
    expected = {"litellm_prod.dump", "litellm_dev.dump", "vector_store.dump", "metadata.txt", "SHA256SUMS"}
    dumps = 0
    for entry in entries:
        if entry.name not in expected or not entry.is_file() or entry.is_symlink():
            return False
        if entry.name.endswith(".dump"):
            dumps += 1
    return dumps > 0 and all((path / name).is_file() and not (path / name).is_symlink()
                             for name in ("metadata.txt", "SHA256SUMS"))


def prune_old_backups(root: Path, retention_days: int) -> None:
    # `find -mtime +N` matches only after N+1 complete 24-hour periods.
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days + 1)
    for candidate in root.iterdir():
        if not candidate.is_dir() or candidate.is_symlink() or not BACKUP_NAME_RE.fullmatch(candidate.name):
            continue
        if datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc) >= cutoff:
            continue
        if not safe_backup_directory(candidate):
            LOG.warning(f"WARNING: skipped retention cleanup for unexpected contents in {candidate}")
            continue
        for entry in candidate.iterdir():
            entry.unlink()
        candidate.rmdir()
        LOG.info(f"Removed expired backup {candidate}")


def publish_success_metric() -> None:
    metrics_dir = ROOT / "security-metrics"
    if not metrics_dir.is_dir() or metrics_dir.is_symlink():
        fail("security-metrics must be an existing regular directory")
    destination = metrics_dir / "litellm_backup.prom"
    temporary = metrics_dir / f".litellm_backup.{os.getpid()}.tmp"
    try:
        write_private_file(
            temporary,
            backup_freshness_metrics(int(datetime.now(timezone.utc).timestamp())),
        )
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> None:
    if len(sys.argv) > 1:
        if sys.argv[1] in {"-h", "--help"} and len(sys.argv) == 2:
            usage()
            return
        usage(sys.stderr)
        raise SystemExit(2)
    retention_raw = os.environ.get("BACKUP_RETENTION_DAYS") or "14"
    if not re.fullmatch(r"\d+", retention_raw):
        fail("BACKUP_RETENTION_DAYS must be a non-negative integer")
    require_docker()
    compose(["config", "--quiet"])
    root = safe_output_root(os.environ.get("BACKUP_DIR") or str(ROOT / "backups"), "BACKUP_DIR")
    timestamp = utc_timestamp()
    final = root / f"litellm-stack-{timestamp}"
    if final.exists():
        fail(f"backup destination already exists: {final}")
    backed_up: list[str] = []
    skipped: list[str] = []
    with secure_tempdir(root, ".litellm-stack-backup.") as staging:
        dumps: list[str] = []
        for service, database, filename in DATABASES:
            if not service_is_running(service):
                LOG.warning(f"WARNING: skipping {database} because service {service} is unavailable")
                skipped.append(f"{database}:service-unavailable")
                continue
            status = database_exists(service, database)
            if status == 1:
                LOG.warning(f"WARNING: skipping {database} because it does not exist in service {service}")
                skipped.append(f"{database}:database-not-found")
                continue
            if status != 0:
                fail(f"could not query databases in service '{service}'")
            dump_database(service, database, staging / filename)
            backed_up.append(database)
            dumps.append(filename)
        if not backed_up:
            fail("no configured databases were available to back up")
        write_private_file(
            staging / "metadata.txt",
            f"created_at_utc={timestamp}\nformat=postgresql-custom\n"
            f"databases_backed_up={','.join(backed_up)}\ndatabases_skipped={','.join(skipped)}\n",
        )
        write_checksum_manifest(staging, [*dumps, "metadata.txt"])
        staging.rename(final)
    LOG.info(f"Backup completed: {final}")
    if skipped:
        LOG.info(f"Completed with skipped resources: {','.join(skipped)}")
        LOG.warning("Older backups were not pruned because this backup is partial")
        raise SystemExit(3)
    publish_success_metric()
    prune_old_backups(root, int(retention_raw))
    if shutil.which("sha256sum"):
        LOG.info(f"Verify with: cd {final} && sha256sum --check SHA256SUMS")
    elif shutil.which("shasum"):
        LOG.info(f"Verify with: cd {final} && shasum -a 256 --check SHA256SUMS")
    else:
        LOG.info(f"Checksum manifest: {final / 'SHA256SUMS'}")


if __name__ == "__main__":
    run_main(main)
