#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Capture a safe, checksummed rollback point before a LiteLLM upgrade."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from maintenance_common import (
    IMAGE_REF_RE,
    ROOT,
    SCRIPTS_DIR,
    compose,
    compose_capture,
    fail,
    require_docker,
    require_regular,
    run,
    run_main,
    run_python_script,
    safe_output_root,
    secure_tempdir,
    service_is_running,
    sha256_file,
    utc_timestamp,
    write_checksum_manifest,
    write_private_file,
)

USAGE = """Usage: scripts/prepare-litellm-upgrade.py [--skip-backup] [--services LIST]

Captures a deterministic rollback point BEFORE a LiteLLM image upgrade is
applied, for later use by scripts/rollback-litellm-upgrade.py.

For each running target service (default: litellm-prod, litellm-dev) this
records:
  - the exact, digest-pinned image reference currently deployed;
  - checksums of the repository-owned LiteLLM configuration currently in use
    (litellm/config.prod.yaml, litellm/config.dev.yaml, litellm/modules/**);
  - optionally, a fresh database backup via scripts/backup-databases.py, so a
    schema-incompatible rollback can restore pre-upgrade data.

Nothing in docker-compose.yml, litellm/config.*.yaml, or litellm/modules is
modified. Secrets (.env values, DATABASE_URL, API keys) are never captured or
printed.
"""


def usage(stream: object = sys.stdout) -> None:
    print(USAGE, file=stream, end="")


def normalize_service(value: str) -> str:
    if value in {"prod", "litellm-prod"}:
        return "litellm-prod"
    if value in {"dev", "litellm-dev"}:
        return "litellm-dev"
    fail(f"unsupported service: {value} (expected prod, dev, litellm-prod, or litellm-dev)")


def config_files() -> list[Path]:
    result: list[Path] = []
    for relative in ("litellm/config.prod.yaml", "litellm/config.dev.yaml"):
        path = ROOT / relative
        require_regular(path, f"expected configuration file is missing or unsafe: {relative}")
        result.append(path)
    modules = ROOT / "litellm" / "modules"
    if modules.is_dir():
        result.extend(path for path in sorted(modules.rglob("*")) if path.is_file() and not path.is_symlink() and path.name != ".DS_Store")
    return result


def main() -> None:
    args = sys.argv[1:]
    skip_backup = False
    services_value = "litellm-prod,litellm-dev"
    index = 0
    while index < len(args):
        if args[index] == "--skip-backup":
            skip_backup = True
            index += 1
        elif args[index] == "--services":
            if index + 1 >= len(args):
                usage(sys.stderr)
                raise SystemExit(2)
            if not args[index + 1]:
                fail("--services requires a non-empty value")
            services_value = args[index + 1]
            index += 2
        elif args[index] in {"-h", "--help"}:
            usage()
            return
        else:
            usage(sys.stderr)
            raise SystemExit(2)
    services = [normalize_service(value) for value in services_value.split(",")]
    require_docker()
    compose(["config", "--quiet"])
    state_root_value = Path(os.environ.get("UPGRADE_STATE_DIR") or str(ROOT / "upgrade-state")).expanduser()
    if not state_root_value.is_absolute():
        state_root_value = ROOT / state_root_value
    state_root = safe_output_root(str(state_root_value), "UPGRADE_STATE_DIR")
    state_name = f"litellm-upgrade-{utc_timestamp()}"
    final = state_root / state_name
    if final.exists():
        fail(f"state destination already exists: {final}")
    images: dict[str, str] = {}
    captured: list[str] = []
    skipped: list[str] = []
    for service in services:
        if not service_is_running(service):
            skipped.append(f"{service}:service-not-running")
            print(f"WARNING: skipping {service} (service-not-running)", file=sys.stderr)
            continue
        image = compose_capture(["ps", "--format", "{{.Image}}", service]).strip()
        if not image:
            fail(f"could not determine the deployed image for {service}")
        if not IMAGE_REF_RE.fullmatch(image):
            fail(f"service '{service}' is not running a digest-pinned image ({image}); refusing to capture an unsafe rollback point")
        container = compose_capture(["ps", "-q", service]).strip()
        if not container:
            fail(f"could not resolve a container id for {service}")
        inspected = run(["docker", "inspect", "--format", "{{.Config.Image}}", container], capture=True).stdout.strip()
        if inspected != image:
            fail(f"image reference mismatch for {service} between 'docker compose ps' and 'docker inspect'")
        images[service] = image
        captured.append(service)
        print(f"Captured rollback image for {service}: {image}")
    if not captured:
        fail("no running target service was available to capture a rollback point for")
    files = config_files()
    backup_dir = ""
    with secure_tempdir(state_root, ".litellm-upgrade-prepare.") as staging:
        checksum_lines = "".join(
            f"{sha256_file(path)}  {path.relative_to(ROOT)}\n" for path in files
        )
        write_private_file(staging / "config-checksums.txt", checksum_lines)
        if skip_backup:
            print("WARNING: --skip-backup was used; schema-incompatible rollback will require an explicit --backup argument", file=sys.stderr)
        else:
            print("Creating pre-upgrade database backup...")
            backup = SCRIPTS_DIR / "backup-databases.py"
            result = run_python_script(backup, [], capture_stdout=True)
            if result.returncode:
                fail("pre-upgrade backup failed; rerun with --skip-backup only if this is acceptable")
            assert isinstance(result.stdout, str)
            print(result.stdout, end="")
            for line in result.stdout.splitlines():
                if line.startswith("Backup completed: "):
                    backup_dir = line.removeprefix("Backup completed: ")
            candidate = Path(backup_dir)
            if not backup_dir or not candidate.is_dir() or candidate.is_symlink():
                fail("could not determine the backup directory from backup-databases.py output")
            require_regular(candidate / "SHA256SUMS", "captured backup is missing its checksum manifest")
        write_private_file(
            staging / "state.env",
            f"created_at_utc={state_name.removeprefix('litellm-upgrade-')}\n"
            f"services_captured={','.join(captured)}\nservices_skipped={','.join(skipped)}\n"
            f"image.litellm-prod={images.get('litellm-prod', '')}\n"
            f"image.litellm-dev={images.get('litellm-dev', '')}\nbackup_dir={backup_dir}\n"
            f"backup_skipped={'true' if skip_backup else 'false'}\nconfig_checksum_manifest=config-checksums.txt\n",
        )
        write_checksum_manifest(staging, ["state.env", "config-checksums.txt"])
        staging.rename(final)
    write_private_file(state_root / ".LATEST.tmp", f"{state_name}\n")
    (state_root / ".LATEST.tmp").replace(state_root / "LATEST")
    print(f"Prepared upgrade rollback point: {final}")
    if skipped:
        print(f"Skipped services: {','.join(skipped)}")
    if skip_backup:
        print("No database backup was captured (schema-incompatible rollback needs --backup).")
    else:
        print(f"Database backup for schema-incompatible rollback: {backup_dir}")
    print("\nAfter upgrading, if rollback is needed:")
    print("  Image-only rollback:    scripts/rollback-litellm-upgrade.py --confirm-rollback --mode image-only")
    print("  Schema-incompatible:     scripts/rollback-litellm-upgrade.py --confirm-rollback --mode restore-db")


if __name__ == "__main__":
    run_main(main)
