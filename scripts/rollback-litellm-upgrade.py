#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Perform a confirmed image or database LiteLLM rollback."""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path

from maintenance_common import (
    IMAGE_REF_RE,
    ROOT,
    SCRIPTS_DIR,
    STATE_NAME_RE,
    compose,
    compose_capture,
    fail,
    parse_key_values,
    require_directory,
    require_docker,
    require_regular,
    run_main,
    run_python_script,
    safe_output_root,
    secure_tempdir,
    service_is_running,
    utc_timestamp,
    verify_checksum_manifest,
    write_private_file,
)

USAGE = """Usage:
  scripts/rollback-litellm-upgrade.py --confirm-rollback --mode MODE [options]

Rolls back LiteLLM (litellm-prod / litellm-dev) to the image reference
captured by scripts/prepare-litellm-upgrade.py. Nothing in docker-compose.yml
is modified; the rollback image is persisted in docker-compose.override.yml so
ordinary Docker Compose and maintenance commands continue using it.
"""
STATE_KEYS = {
    "created_at_utc", "services_captured", "services_skipped", "image.litellm-prod",
    "image.litellm-dev", "backup_dir", "backup_skipped", "config_checksum_manifest",
}


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
        file = ROOT / relative
        require_regular(file, f"expected configuration file is missing or unsafe: {relative}")
        result.append(file)
    modules = ROOT / "litellm" / "modules"
    if modules.is_dir():
        result.extend(file for file in sorted(modules.rglob("*"))
                      if file.is_file() and not file.is_symlink() and file.name != ".DS_Store")
    return result


def read_state(state_root: Path, argument: str) -> tuple[Path, dict[str, str]]:
    if argument:
        state_argument = Path(argument).expanduser()
        if not state_argument.is_absolute():
            state_argument = ROOT / state_argument
        state = require_directory(state_argument, f"state directory does not exist or is a symbolic link: {argument}")
    else:
        latest = state_root / "LATEST"
        require_regular(latest, "no prepared upgrade state found; run scripts/prepare-litellm-upgrade.py first or pass --state")
        name = latest.read_text(encoding="utf-8").strip()
        if not STATE_NAME_RE.fullmatch(name):
            fail(f"invalid LATEST pointer in {state_root}")
        state = require_directory(state_root / name)
    if not STATE_NAME_RE.fullmatch(state.name):
        fail(f"unexpected state directory name: {state}")
    allowed_entries = {"state.env", "config-checksums.txt", "SHA256SUMS", "reports"}
    for entry in state.iterdir():
        if entry.name not in allowed_entries:
            fail(f"unexpected entry in state directory: {entry.name}")
        if entry.name == "reports":
            if not entry.is_dir() or entry.is_symlink():
                fail("state entry must be a directory: reports")
        elif not entry.is_file() or entry.is_symlink():
            fail(f"state entry must be a regular file: {entry.name}")
    for required in ("state.env", "config-checksums.txt", "SHA256SUMS"):
        require_regular(state / required, f"state directory is missing {required}")
    verify_checksum_manifest(state, {"state.env", "config-checksums.txt"})
    values = parse_key_values(state / "state.env", STATE_KEYS, "state.env")
    if not re.fullmatch(r"\d{8}T\d{6}Z", values.get("created_at_utc", "")):
        fail("invalid or missing created_at_utc in state.env")
    if not values.get("services_captured"):
        fail("state.env recorded no captured services")
    return state, values


def detect_drift(state: Path, allow: bool) -> None:
    current = {str(path.relative_to(ROOT)) for path in config_files()}
    recorded: set[str] = set()
    for line in (state / "config-checksums.txt").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"[0-9a-f]{64}  (.+)", line)
        if not match:
            fail("state directory checksum verification failed; the prepared rollback point may be corrupted or tampered with")
        recorded.add(match.group(1))
    drift = current != recorded
    if not drift:
        for line in (state / "config-checksums.txt").read_text(encoding="utf-8").splitlines():
            digest, relative = line.split("  ", 1)
            from maintenance_common import sha256_file
            if sha256_file(ROOT / relative) != digest:
                drift = True
                break
    if drift and not allow:
        fail("configuration drift detected since prepare; re-run scripts/prepare-litellm-upgrade.py or pass --allow-config-drift to proceed knowingly")
    if drift:
        print("WARNING: proceeding despite configuration drift because --allow-config-drift was given", file=sys.stderr)


def override_file(path: Path, services: list[str], images: dict[str, str]) -> None:
    write_private_file(
        path,
        "x-litellm-stack-active-rollback: true\nservices:\n"
        + "".join(f"  {service}:\n    image: {images[service]}\n" for service in services),
    )


def persist_active_override(source: Path) -> Path:
    active = ROOT / "docker-compose.override.yml"
    if active.exists():
        if active.is_symlink() or not active.is_file():
            fail(f"refusing to replace unsafe active rollback override: {active}")
        if not active.read_text(encoding="utf-8").startswith(
            "x-litellm-stack-active-rollback: true\n"
        ):
            fail(
                "docker-compose.override.yml already exists and was not generated "
                "by the rollback workflow"
            )
    temporary = ROOT / f".docker-compose.override.{os.getpid()}.tmp"
    try:
        write_private_file(temporary, source.read_text(encoding="utf-8"))
        os.replace(temporary, active)
        os.chmod(active, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()
    return active


def wait_running(service: str) -> None:
    for _ in range(30):
        if service_is_running(service):
            return
        time.sleep(2)
    fail(f"service {service} did not start within 60 seconds")


def stop_if_running(service: str) -> None:
    if service_is_running(service):
        print(f"Stopping {service}...")
        compose(["stop", service])
    if service_is_running(service):
        fail(f"service {service} did not stop; aborting to avoid an application/database race")


def verify_image(service: str, image: str) -> None:
    actual = compose_capture(["ps", "--format", "{{.Image}}", service]).strip()
    if actual != image:
        fail(f"post-rollback image mismatch for {service}: expected {image}, got {actual}")


def check_liveliness(service: str) -> bool:
    probe = (
        "import sys, urllib.request\n"
        "try:\n"
        "  with urllib.request.urlopen('http://localhost:4000/health/liveliness', timeout=5) as r: sys.exit(0 if r.status == 200 else 1)\n"
        "except Exception: sys.exit(1)\n"
    )
    for _ in range(15):
        if compose(["exec", "-T", service, "python3", "-c", probe], check=False).returncode == 0:
            return True
        time.sleep(2)
    return False


def main() -> None:
    args = sys.argv[1:]
    confirmed = False
    mode = ""
    state_arg = backup_arg = services_arg = ""
    allow_drift = False
    index = 0
    valued = {"--mode": "mode", "--state": "state", "--backup": "backup", "--services": "services"}
    while index < len(args):
        arg = args[index]
        if arg == "--confirm-rollback":
            confirmed = True
            index += 1
        elif arg == "--allow-config-drift":
            allow_drift = True
            index += 1
        elif arg in {"-h", "--help"}:
            usage()
            return
        elif arg in valued:
            if index + 1 >= len(args):
                usage(sys.stderr)
                raise SystemExit(2)
            value = args[index + 1]
            if arg == "--mode": mode = value
            elif arg == "--state": state_arg = value
            elif arg == "--backup": backup_arg = value
            else:
                if not value: fail("--services requires a non-empty value")
                services_arg = value
            index += 2
        else:
            usage(sys.stderr)
            raise SystemExit(2)
    if not confirmed or mode not in {"image-only", "restore-db"}:
        usage(sys.stderr)
        raise SystemExit(2)
    if mode == "restore-db" and services_arg:
        fail("--services is not supported with restore-db because database restoration is stack-wide")
    require_docker()
    compose(["config", "--quiet"])
    state_root_value = Path(os.environ.get("UPGRADE_STATE_DIR") or str(ROOT / "upgrade-state")).expanduser()
    if not state_root_value.is_absolute():
        state_root_value = ROOT / state_root_value
    state_root = safe_output_root(str(state_root_value), "UPGRADE_STATE_DIR")
    state, values = read_state(state_root, state_arg)
    captured = values["services_captured"].split(",")
    if any(item not in {"litellm-prod", "litellm-dev"} for item in captured):
        fail("unsupported service recorded in state.env")
    targets = [normalize_service(item) for item in services_arg.split(",")] if services_arg else captured
    if any(target not in captured for target in targets):
        fail("no captured rollback image for requested service; rerun scripts/prepare-litellm-upgrade.py")
    images = {service: values.get(f"image.{service}", "") for service in targets}
    for service, image in images.items():
        if not IMAGE_REF_RE.fullmatch(image):
            fail(f"recorded rollback image for {service} is not an immutable digest reference: {image}")
    detect_drift(state, allow_drift)
    lock = state_root / ".rollback.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        fail(f"another rollback appears to be in progress (lock present: {lock})")
    reports = state / "reports"
    report = reports / f"rollback-report-{utc_timestamp()}.txt"
    actions = {service: "not-attempted" for service in targets}
    liveliness = {service: "not-checked" for service in targets}
    previous = {service: "" for service in targets}
    result, failed_stage, applied = "FAILURE", "argument-validation", ""
    backup_report = ""
    database_restore_exit_code = "not-run"
    try:
        reports.mkdir(exist_ok=True)
        previous = {
            service: compose_capture(["ps", "--format", "{{.Image}}", service]).strip()
            if service_is_running(service)
            else ""
            for service in targets
        }
        with secure_tempdir(state_root, ".litellm-rollback-override.") as temp:
            override = temp / "rollback-override.yml"
            override_file(override, targets, images)
            compose(["-f", "docker-compose.yml", "-f", str(override), "--profile", "prod", "--profile", "dev", "config", "--quiet"])
            if mode == "restore-db":
                failed_stage = "backup-resolution"
                backup_value = backup_arg or values.get("backup_dir", "")
                if not backup_value:
                    fail("no backup recorded in state.env and none provided via --backup; a schema-incompatible rollback requires a database backup")
                backup = Path(backup_value).expanduser()
                if not backup.is_absolute():
                    backup = ROOT / backup
                backup = require_directory(
                    backup,
                    f"backup directory does not exist or is a symbolic link: {backup}",
                )
                backup_report = str(backup)
                failed_stage = "application-stop"
                pgvector_was_running = service_is_running("litellm-pgvector")
                bridge_was_running = service_is_running("opensearch-bridge")
                # The OpenSearch bridge reads LiteLLM tables and must not race the restore.
                for service in ("litellm-prod", "litellm-dev", "litellm-pgvector", "opensearch-bridge"):
                    stop_if_running(service)
                failed_stage = "database-startup"
                compose(["up", "-d", "--wait", "postgres", "vector-postgres"])
                failed_stage = "database-restore"
                print(f"Restoring database backup: {backup}")
                restore = run_python_script(
                    SCRIPTS_DIR / "restore-databases.py",
                    ["--confirm-restore", backup],
                )
                database_restore_exit_code = str(restore.returncode)
                if restore.returncode:
                    fail(f"database restore failed (exit code {restore.returncode}); application services were left stopped to avoid running against an inconsistent database")
            failed_stage = "image-rollback"
            for service in targets:
                if mode == "image-only" and previous[service] == images[service]:
                    print(f"{service} is already running the rollback image; nothing to do")
                    actions[service] = "already-current"
                    continue
                if mode == "image-only":
                    stop_if_running(service)
                print(f"Starting {service} with rollback image...")
                compose(["-f", "docker-compose.yml", "-f", str(override), "--profile", "prod", "--profile", "dev", "up", "-d", "--no-deps", service])
                wait_running(service)
                verify_image(service, images[service])
                if not check_liveliness(service):
                    actions[service] = "rolled-back-liveliness-failed"
                    liveliness[service] = "fail"
                    fail(f"service {service} was rolled back to the prior image but failed its liveliness check")
                actions[service], liveliness[service] = "rolled-back", "pass"
            applied_path = persist_active_override(override)
            report_override = reports / "active-rollback-override.yml"
            shutil.copyfile(override, report_override)
            os.chmod(report_override, 0o600)
            applied = str(applied_path)
            if mode == "restore-db" and pgvector_was_running:
                failed_stage = "pgvector-restart"
                print("Restarting litellm-pgvector...")
                compose(["up", "-d", "--no-deps", "litellm-pgvector"])
                wait_running("litellm-pgvector")
            if mode == "restore-db" and bridge_was_running:
                failed_stage = "opensearch-bridge-restart"
                print("Restarting opensearch-bridge...")
                compose(["up", "-d", "--no-deps", "opensearch-bridge"])
                wait_running("opensearch-bridge")
            result = "SUCCESS"
    finally:
        try:
            if reports.is_dir() and not reports.is_symlink():
                lines = [f"rollback_mode={mode}", f"state_dir={state}", f"confirmed={str(confirmed).lower()}",
                         f"services_requested={','.join(targets)}"]
                if mode == "restore-db":
                    lines.extend(
                        (
                            f"backup_dir={backup_report}",
                            f"database_restore_exit_code={database_restore_exit_code}",
                        )
                    )
                for service in targets:
                    lines.extend((f"service.{service}.previous_image={previous[service]}", f"service.{service}.target_image={images[service]}",
                                  f"service.{service}.action={actions[service]}", f"service.{service}.liveliness={liveliness[service]}"))
                lines.extend((f"applied_override={applied}", f"result={result}",
                              *([f"failed_stage={failed_stage}"] if result == "FAILURE" else []),
                              f"completed_at_utc={utc_timestamp()}"))
                write_private_file(report, "\n".join(lines) + "\n")
                print(f"\nRollback report: {report}")
                print(report.read_text(encoding="utf-8"), end="")
            else:
                print(f"WARNING: rollback report directory is unavailable: {reports}", file=sys.stderr)
        finally:
            lock.rmdir()
    print(f"Rollback completed successfully (mode: {mode})")
    if applied:
        print(f"Activated persistent rollback override: {applied}")
        print("Remove it only after docker-compose.yml is pinned to the intended image.")


if __name__ == "__main__":
    run_main(main)
