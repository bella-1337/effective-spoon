#!/usr/bin/env python3
"""Validate the static safety contracts required before an operations game day."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ALERT_UIDS = {
    "litellm-prod-unavailable",
    "litellm-postgres-unavailable",
    "litellm-host-disk-low",
    "litellm-backup-stale",
}
REQUIRED_SCRIPTS = {
    "backup": ROOT / "scripts" / "backup-databases.py",
    "restore": ROOT / "scripts" / "restore-databases.py",
    "upgrade preparation": ROOT / "scripts" / "prepare-litellm-upgrade.py",
    "rollback": ROOT / "scripts" / "rollback-litellm-upgrade.py",
}


def load_compose_config(env_file: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "--profile",
            "prod",
            "--profile",
            "dev",
            "--profile",
            "demo",
            "--profile",
            "security",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Docker Compose configuration failed: {detail}")
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, dict):
        raise RuntimeError("Docker Compose returned a non-object configuration")
    return parsed


def dependency_names(service: dict[str, Any]) -> set[str]:
    depends_on = service.get("depends_on", {})
    if isinstance(depends_on, dict):
        return set(depends_on)
    if isinstance(depends_on, list):
        return {value for value in depends_on if isinstance(value, str)}
    return set()


def validate_contracts(config: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    services = config.get("services")
    if not isinstance(services, dict):
        return ["Compose configuration has no services object"]

    production = services.get("litellm-prod")
    if not isinstance(production, dict):
        failures.append("Compose configuration has no litellm-prod service")
    else:
        forbidden = dependency_names(production) & {"langfuse-web", "xberg"}
        if forbidden:
            failures.append(
                "Production LiteLLM still depends on auxiliary services: "
                + ", ".join(sorted(forbidden))
            )

    alloy_proxy = services.get("alloy-docker-proxy")
    alloy_environment = (
        alloy_proxy.get("environment", {}) if isinstance(alloy_proxy, dict) else {}
    )
    if not isinstance(alloy_environment, dict) or str(
        alloy_environment.get("NETWORKS")
    ) != "1":
        failures.append("Alloy Docker discovery lacks read-only network metadata access")

    xberg = services.get("xberg")
    if not isinstance(xberg, dict):
        failures.append("Compose configuration has no xberg service")
    else:
        xberg_environment = xberg.get("environment", {})
        no_proxy = (
            str(xberg_environment.get("NO_PROXY", ""))
            if isinstance(xberg_environment, dict)
            else ""
        )
        healthcheck = xberg.get("healthcheck", {})
        health_test = (
            healthcheck.get("test", []) if isinstance(healthcheck, dict) else []
        )
        if "127.0.0.1" not in no_proxy:
            failures.append("Xberg loopback health traffic is not excluded from its proxy")
        if not isinstance(health_test, list) or "--fail" not in health_test:
            failures.append("Xberg healthcheck does not fail on HTTP error responses")

    for label, script in REQUIRED_SCRIPTS.items():
        if not script.is_file() or script.is_symlink():
            failures.append(f"Required {label} script is missing or unsafe: {script}")

    alert_text = (ROOT / "grafana" / "alerting" / "stack-production.yml").read_text(
        encoding="utf-8"
    )
    missing_alerts = {
        uid for uid in EXPECTED_ALERT_UIDS if f"uid: {uid}" not in alert_text
    }
    if missing_alerts:
        failures.append(
            "Required production alerts are missing: " + ", ".join(sorted(missing_alerts))
        )

    backup_text = REQUIRED_SCRIPTS["backup"].read_text(encoding="utf-8")
    if "litellm_backup_last_success_timestamp_seconds" not in backup_text:
        failures.append("Complete-backup freshness metric is not published")

    restore_text = REQUIRED_SCRIPTS["restore"].read_text(encoding="utf-8")
    for required in ("_pre_restore_", "pg_terminate_backend", "pgvector-exporter"):
        if required not in restore_text:
            failures.append(f"Restore safety contract is missing: {required}")

    rollback_text = REQUIRED_SCRIPTS["rollback"].read_text(encoding="utf-8")
    for required in (
        'ROOT / "docker-compose.override.yml"',
        "x-litellm-stack-active-rollback",
    ):
        if required not in rollback_text:
            failures.append(f"Rollback persistence contract is missing: {required}")

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    if "docker-compose.override.yml" not in gitignore:
        failures.append("The persistent rollback override is not ignored by Git")

    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=ROOT / ".env",
        help="environment file used to render every Compose profile",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_file = args.env_file.expanduser().resolve()
    if not env_file.is_file() or env_file.is_symlink():
        print(f"ERROR: environment file is missing or unsafe: {env_file}", file=sys.stderr)
        return 2

    try:
        config = load_compose_config(env_file)
        failures = validate_contracts(config)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1

    print("PASS: game-day static safety contracts are satisfied")
    print("NEXT: execute the isolated and live drills in OPERATIONS-GAME-DAY.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
