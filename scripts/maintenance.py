#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Central maintenance command dispatcher."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from maintenance_common import (
    ROOT,
    SCRIPTS_DIR,
    compose,
    fail,
    require_docker,
    run,
    run_main,
    run_python_script,
)
from maintenance_contracts import (
    load_compose_config,
    validate_compose_contracts,
    validate_repository_contracts,
)

USAGE = """Usage: scripts/maintenance.py COMMAND [OPTIONS]

Central maintenance entry point for the LiteLLM stack.

Commands:
  doctor
      Validate required scripts, Python syntax, Docker Compose, and both profiles.

  status
      Show all Compose containers and their current state.

  game-day-preflight [--env-file FILE]
      Validate all Compose profiles and the static safety contracts required
      before executing the operations game-day drills.

  security-scan [--all | --services LIST] [--enforce] [--project NAME]
      Scan running image digests with pinned Trivy, correlate CISA KEV, retain
      private reports, and publish aggregate node-exporter textfile metrics.

  security-schedule install|uninstall|status [OPTIONS]
      Manage the Linux systemd timer for the nightly scan. Installation and
      removal require root; the scanner itself runs as a non-root account.

  backup
      Run the logical database backup. Partial backups are published for
      recovery but exit nonzero and never trigger retention pruning.
      Configure BACKUP_DIR and BACKUP_RETENTION_DAYS through the environment.

  restore --confirm-restore BACKUP_DIRECTORY
      Restore a complete or partial backup into stopped application services.
      The explicit confirmation is enforced by restore-databases.py.

  upgrade-check --candidate-image IMAGE_REF [--pull]
      Validate an immutable candidate image without touching live services.

  upgrade-prepare [--skip-backup] [--services LIST]
      Capture current image/config rollback state and, by default, a database
      backup immediately before changing the pinned LiteLLM image.

  pre-upgrade --candidate-image IMAGE_REF [--pull] [--skip-backup]
              [--services LIST]
      Run upgrade-check and then upgrade-prepare as one fail-fast workflow.

  upgrade-verify [--only LIST | --skip LIST | --list]
      Run the post-upgrade regression suite.

  post-upgrade [--only LIST | --skip LIST]
      Alias for upgrade-verify, intended for the deployment runbook.

  rollback --confirm-rollback --mode image-only|restore-db [OPTIONS]
      Run the validated rollback workflow. All confirmation, state, backup,
      drift, and service-selection options are delegated unchanged.

  help
      Show this help text.
"""

SCRIPTS = (
    "maintenance.py", "game-day-preflight.py", "backup-databases.py",
    "restore-databases.py", "verify-pgvector.py",
    "check-litellm-upgrade-compatibility.py", "prepare-litellm-upgrade.py",
    "verify-litellm-upgrade.py", "rollback-litellm-upgrade.py",
    "monitor-image-vulnerabilities.py", "install-image-security-schedule.py",
    "run-image-security-scheduler.py",
)


def usage(stream: object = sys.stdout) -> None:
    print(USAGE, file=stream, end="")


def usage_error(message: str) -> None:
    print(f"ERROR: {message}\n", file=sys.stderr)
    usage(sys.stderr)
    raise SystemExit(2)


def require_script(name: str) -> Path:
    path = SCRIPTS_DIR / name
    if not path.is_file() or path.is_symlink():
        fail(f"required script is missing or unsafe: scripts/{name}")
    if not os.access(path, os.X_OK):
        fail(f"required script is not executable: scripts/{name}")
    return path


def invoke(script: str, arguments: list[str]) -> None:
    path = require_script(script)
    result = run_python_script(path, arguments, cwd=Path.cwd())
    if result.returncode:
        raise SystemExit(result.returncode)


def invoke_with_current_python(script: str, arguments: list[str]) -> None:
    path = require_script(script)
    result = run([sys.executable, path, *arguments], check=False)
    if result.returncode:
        raise SystemExit(result.returncode)


def doctor() -> None:
    require_docker()
    for script in SCRIPTS:
        path = require_script(script)
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError:
            fail(f"scripts/{script} has invalid Python syntax")
        print(f"PASS: scripts/{script} is executable and has valid Python syntax")
    prometheus_key = SCRIPTS_DIR.parent / "litellm" / "secrets" / "litellm-master-key"
    if not prometheus_key.is_file() or prometheus_key.is_symlink():
        fail("litellm/secrets/litellm-master-key is missing or unsafe")
    if prometheus_key.stat().st_mode & 0o077:
        fail("litellm/secrets/litellm-master-key must not be accessible to group or other users")
    print("PASS: Prometheus credential source exists with private permissions")
    env_file = ROOT / ".env"
    if not env_file.is_file() or env_file.is_symlink():
        fail(f"environment file is missing or unsafe: {env_file}")
    config = load_compose_config(env_file)
    failures = [
        *validate_compose_contracts(config),
        *validate_repository_contracts(),
    ]
    if failures:
        fail("game-day safety contracts failed:\n- " + "\n- ".join(failures))
    print("PASS: all Compose profiles and game-day safety contracts are valid")


def pre_upgrade(arguments: list[str]) -> None:
    candidate = ""
    pull = False
    skip_backup = False
    services = ""
    index = 0
    while index < len(arguments):
        arg = arguments[index]
        if arg == "--candidate-image":
            if index + 1 >= len(arguments) or not arguments[index + 1]:
                usage_error("--candidate-image requires a non-empty value")
            candidate = arguments[index + 1]
            index += 2
        elif arg.startswith("--candidate-image="):
            candidate = arg.partition("=")[2]
            if not candidate:
                usage_error("--candidate-image requires a non-empty value")
            index += 1
        elif arg == "--pull":
            pull = True
            index += 1
        elif arg == "--skip-backup":
            skip_backup = True
            index += 1
        elif arg == "--services":
            if index + 1 >= len(arguments) or not arguments[index + 1]:
                usage_error("--services requires a non-empty value")
            services = arguments[index + 1]
            index += 2
        else:
            usage_error(f"unsupported pre-upgrade option: {arg}")
    if not candidate:
        usage_error("pre-upgrade requires --candidate-image")
    print("== Candidate compatibility ==")
    invoke("check-litellm-upgrade-compatibility.py",
           ["--candidate-image", candidate, *(["--pull"] if pull else [])])
    print("\n== Rollback-point preparation ==")
    invoke("prepare-litellm-upgrade.py",
           [*(["--skip-backup"] if skip_backup else []), *(["--services", services] if services else [])])


def main() -> None:
    args = sys.argv[1:]
    command = args.pop(0) if args else "help"
    if command in {"help", "-h", "--help"}:
        if args:
            usage_error("help accepts no additional arguments")
        usage()
    elif command == "doctor":
        if args:
            usage_error("doctor accepts no additional arguments")
        doctor()
    elif command == "status":
        if args:
            usage_error("status accepts no additional arguments")
        require_docker()
        compose(["ps", "--all"])
    elif command == "game-day-preflight":
        invoke("game-day-preflight.py", args)
    elif command == "security-scan":
        invoke("monitor-image-vulnerabilities.py", args)
    elif command == "security-schedule":
        invoke_with_current_python("install-image-security-schedule.py", args)
    elif command == "backup":
        if args:
            usage_error("backup accepts no command-line arguments")
        invoke("backup-databases.py", [])
    elif command == "restore":
        invoke("restore-databases.py", args)
    elif command == "upgrade-check":
        invoke("check-litellm-upgrade-compatibility.py", args)
    elif command == "upgrade-prepare":
        invoke("prepare-litellm-upgrade.py", args)
    elif command == "pre-upgrade":
        pre_upgrade(args)
    elif command in {"upgrade-verify", "post-upgrade"}:
        invoke("verify-litellm-upgrade.py", args)
    elif command == "rollback":
        invoke("rollback-litellm-upgrade.py", args)
    else:
        usage_error(f"unknown command: {command}")


if __name__ == "__main__":
    run_main(main)
