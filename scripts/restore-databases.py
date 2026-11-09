#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Restore a verified backup into stopped LiteLLM database services."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from maintenance_common import (
    LOG,
    compose,
    compose_capture,
    compose_from_file,
    container_environment,
    fail,
    parse_key_values,
    require_directory,
    require_docker,
    require_regular,
    run_main,
    service_is_running,
    verify_checksum_manifest,
)
from maintenance_contracts import (
    RESTORE_DATABASE_CLIENTS,
    restore_database_names,
    terminate_database_connections_sql,
)

USAGE = """Usage:
  scripts/restore-databases.py --confirm-restore BACKUP_DIRECTORY

Restores databases present in a backup created by backup-databases.py.

This operation restores each archive into staging and renames the current target
aside before promotion.
Start only postgres and vector-postgres in the target Compose project. LiteLLM,
Grafana, the OpenSearch bridge, the pgvector connector, and the pgvector exporter
must remain stopped until restoration is complete.
"""
DATABASES = (("postgres", "litellm_prod"), ("postgres", "litellm_dev"), ("vector-postgres", "vector_store"))
ALLOWED_FILES = {"litellm_prod.dump", "litellm_dev.dump", "vector_store.dump", "metadata.txt", "SHA256SUMS"}


def usage(stream: object = sys.stdout) -> None:
    print(USAGE, file=stream, end="")


def verify_contents(backup: Path) -> None:
    for entry in backup.iterdir():
        if entry.name not in ALLOWED_FILES:
            fail(f"unexpected backup entry: {entry.name}")
        if not entry.is_file() or entry.is_symlink():
            fail(f"backup entry must be a regular file: {entry.name}")
    require_regular(backup / "metadata.txt", "metadata.txt is missing or unsafe")
    require_regular(backup / "SHA256SUMS", "SHA256SUMS is missing or unsafe")


def read_metadata(backup: Path) -> tuple[list[str], dict[str, str]]:
    values = parse_key_values(
        backup / "metadata.txt",
        {"created_at_utc", "format", "databases_backed_up", "databases_skipped"},
        "metadata",
    )
    if not re.fullmatch(r"\d{8}T\d{6}Z", values.get("created_at_utc", "")):
        fail("invalid or missing created_at_utc metadata")
    if values.get("format") != "postgresql-custom":
        fail("unsupported or missing backup format")
    listed = values.get("databases_backed_up", "")
    if not listed:
        fail("backup metadata lists no completed databases")
    if "databases_skipped" not in values:
        fail("databases_skipped metadata is missing")
    databases = listed.split(",")
    allowed = {item[1] for item in DATABASES}
    if len(databases) != len(set(databases)):
        fail("duplicate database in metadata")
    for database in databases:
        if database not in allowed:
            fail(f"unsupported database in metadata: {database}")
    return databases, values


def require_application_services_stopped() -> None:
    for service in RESTORE_DATABASE_CLIENTS:
        if service_is_running(service):
            fail(f"database client '{service}' must be stopped before restoration")


def postgres_command(service: str, command: str, *arguments: str) -> bool:
    username = container_environment(service, "POSTGRES_USER")
    result = compose(
        ["exec", "-T", service, command, "--username", username, *arguments],
        check=False,
    )
    return result.returncode == 0


def database_exists(service: str, database: str) -> bool:
    if not re.fullmatch(r"[a-z0-9_]+", database):
        fail("refusing to query an unsafe database identifier")
    username = container_environment(service, "POSTGRES_USER")
    result = compose_capture(
        [
            "exec",
            "-T",
            service,
            "psql",
            "--username",
            username,
            "--dbname",
            "postgres",
            "--no-psqlrc",
            "--quiet",
            "--tuples-only",
            "--no-align",
            "--set",
            "ON_ERROR_STOP=1",
            "--command",
            f"SELECT 1 FROM pg_database WHERE datname = '{database}'",
        ],
        check=False,
    )
    return result.strip() == "1"


def rename_database(service: str, source: str, destination: str) -> bool:
    if not re.fullmatch(r"[a-z0-9_]+", source) or not re.fullmatch(
        r"[a-z0-9_]+", destination
    ):
        fail("refusing to rename a database with an unsafe identifier")
    username = container_environment(service, "POSTGRES_USER")
    terminate_result = compose(
        [
            "exec",
            "-T",
            service,
            "psql",
            "--username",
            username,
            "--dbname",
            "postgres",
            "--no-psqlrc",
            "--set",
            "ON_ERROR_STOP=1",
            "--command",
            (
                terminate_database_connections_sql(source)
            ),
        ],
        check=False,
    )
    if terminate_result.returncode:
        return False
    result = compose(
        [
            "exec",
            "-T",
            service,
            "psql",
            "--username",
            username,
            "--dbname",
            "postgres",
            "--no-psqlrc",
            "--set",
            "ON_ERROR_STOP=1",
            "--command",
            f'ALTER DATABASE "{source}" RENAME TO "{destination}"',
        ],
        check=False,
    )
    return result.returncode == 0


def remove_staging_database(service: str, staging: str) -> None:
    if not postgres_command(service, "dropdb", "--if-exists", "--force", staging):
        LOG.warning(f"WARNING: could not remove failed staging database {staging}")


def restore_database(service: str, database: str, archive: Path) -> bool:
    staging, previous = restore_database_names(database, os.getpid())
    if not service_is_running(service):
        LOG.error(f"ERROR: cannot restore {database} because service {service} is unavailable")
        return False
    if compose_from_file(
        ["exec", "-T", service, "pg_restore", "--list"],
        archive,
        check=False,
    ).returncode:
        LOG.error(f"ERROR: archive validation failed for {database}")
        return False
    LOG.info(f"Restoring {database} into staging database {staging}...")
    if not postgres_command(service, "dropdb", "--if-exists", "--force", staging):
        LOG.error(f"ERROR: could not remove an existing staging database for {database}")
        return False
    if not postgres_command(service, "createdb", staging):
        LOG.error(f"ERROR: could not create staging database for {database}")
        return False
    if compose_from_file(
        [
            "exec", "-T", service, "pg_restore",
            "--username", container_environment(service, "POSTGRES_USER"),
            "--dbname", staging, "--exit-on-error", "--single-transaction",
            "--no-owner", "--no-privileges",
        ],
        archive,
        check=False,
    ).returncode:
        LOG.error(f"ERROR: staged restore failed for {database}")
        remove_staging_database(service, staging)
        return False
    table_count = compose_capture(
        [
            "exec", "-T", service, "psql",
            "--username", container_environment(service, "POSTGRES_USER"),
            "--dbname", staging, "--no-psqlrc", "--quiet", "--tuples-only",
            "--no-align", "--set", "ON_ERROR_STOP=1", "--command",
            "SELECT COUNT(*) FROM pg_tables WHERE schemaname = current_schema()",
        ],
        check=False,
    ).strip()
    if not table_count.isdigit() or int(table_count) <= 0:
        LOG.error(f"ERROR: staged restore validation failed for {database}")
        remove_staging_database(service, staging)
        return False
    LOG.info(f"Replacing target database {database}...")
    target_existed = database_exists(service, database)
    if target_existed:
        if database_exists(service, previous):
            LOG.error(
                f"ERROR: safety database already exists for {database}: {previous}",
            )
            return False
        if not rename_database(service, database, previous):
            LOG.error(
                f"ERROR: could not preserve current target database {database}",
            )
            return False
    if not rename_database(service, staging, database):
        if target_existed and not rename_database(service, previous, database):
            LOG.error(
                f"ERROR: promotion and automatic recovery failed for {database}; "
                f"recover the prior data from {previous}",
            )
        else:
            LOG.error(
                f"ERROR: could not promote staged restore for {database}; "
                f"recovered data remains in {staging}",
            )
        return False
    if not postgres_command(
        service,
        "psql",
        "--dbname", database, "--no-psqlrc", "--set", "ON_ERROR_STOP=1",
        "--command", "SELECT 1",
    ):
        if target_existed:
            restored_preserved = rename_database(service, database, staging)
            prior_restored = restored_preserved and rename_database(
                service, previous, database
            )
            if not prior_restored:
                LOG.error(
                    f"ERROR: post-promotion check and automatic recovery failed for "
                    f"{database}; inspect {database}, {staging}, and {previous}",
                )
                return False
        LOG.error(
            f"ERROR: post-promotion connection check failed for {database}",
        )
        return False
    if target_existed:
        LOG.info(
            f"Preserved the pre-restore database as {previous}; remove it only "
            "after application verification succeeds"
        )
    return True


def main() -> None:
    args = sys.argv[1:]
    if args == ["-h"] or args == ["--help"]:
        usage()
        return
    if len(args) != 2 or args[0] != "--confirm-restore":
        usage(sys.stderr)
        raise SystemExit(2)
    backup = require_directory(Path(args[1]), "backup directory does not exist or is a symbolic link")
    if backup == Path("/"):
        fail("backup directory must not be the filesystem root")
    require_docker()
    compose(["config", "--quiet"])
    verify_contents(backup)
    verified = verify_checksum_manifest(backup, ALLOWED_FILES - {"SHA256SUMS"}, require_metadata=True)
    databases, _ = read_metadata(backup)
    for _, database in DATABASES:
        filename = f"{database}.dump"
        if database in databases and filename not in verified:
            fail(f"metadata lists {database} but {filename} is not verified")
        if database not in databases and filename in verified:
            fail(f"{filename} exists in the manifest but not in metadata")
    require_application_services_stopped()
    restored: list[str] = []
    failed: list[str] = []
    for service, database in DATABASES:
        if database not in databases:
            LOG.info(f"Skipping {database} because it is not present in this backup")
            continue
        if restore_database(service, database, backup / f"{database}.dump"):
            restored.append(database)
        else:
            failed.append(database)
    if failed:
        LOG.error(f"ERROR: restore completed with failures: {','.join(failed)}")
        if restored:
            LOG.info(f"Successfully restored before failure: {','.join(restored)}")
        raise SystemExit(1)
    LOG.info(f"Restore completed successfully: {','.join(restored)}")


if __name__ == "__main__":
    run_main(main)
