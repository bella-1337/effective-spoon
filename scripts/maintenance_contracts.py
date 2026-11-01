"""Shared static and behavioral contracts for stack maintenance workflows."""

from __future__ import annotations

import json
import re
import runpy
import stat
import tomllib
from pathlib import Path
from typing import Any, Mapping, Sequence

from maintenance_common import IMAGE_REF_RE, ROOT, compose_capture

COMPOSE_PROFILES = ("prod", "dev", "demo", "security", "grafana", "langfuse")
EXPECTED_ALERT_UIDS = {
    "litellm-prod-unavailable",
    "litellm-postgres-unavailable",
    "litellm-host-disk-low",
    "litellm-backup-stale",
}
BACKUP_FRESHNESS_METRIC = "litellm_backup_last_success_timestamp_seconds"
RESTORE_DATABASE_CLIENTS = (
    "litellm-prod",
    "litellm-dev",
    "litellm-pgvector",
    "grafana",
    "opensearch-bridge",
    "pgvector-exporter",
)
ACTIVE_ROLLBACK_OVERRIDE = ROOT / "docker-compose.override.yml"
ACTIVE_ROLLBACK_MARKER = "x-litellm-stack-active-rollback: true"


def compose_profile_arguments() -> list[str]:
    return [
        argument
        for profile in COMPOSE_PROFILES
        for argument in ("--profile", profile)
    ]


def load_compose_config(env_file: Path) -> dict[str, Any]:
    output = compose_capture(
        [
            "--env-file",
            env_file,
            *compose_profile_arguments(),
            "config",
            "--format",
            "json",
        ]
    )
    parsed = json.loads(output)
    if not isinstance(parsed, dict):
        raise ValueError("Docker Compose returned a non-object configuration")
    return parsed


def required_dependency_names(service: Mapping[str, Any]) -> set[str]:
    depends_on = service.get("depends_on", {})
    if isinstance(depends_on, list):
        return {value for value in depends_on if isinstance(value, str)}
    if not isinstance(depends_on, dict):
        return set()
    return {
        name
        for name, options in depends_on.items()
        if not isinstance(options, dict) or options.get("required", True) is not False
    }


def validate_compose_contracts(config: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    services = config.get("services")
    if not isinstance(services, dict):
        return ["Compose configuration has no services object"]

    production = services.get("litellm-prod")
    if not isinstance(production, dict):
        failures.append("Compose configuration has no litellm-prod service")
    else:
        forbidden = required_dependency_names(production) & {"langfuse-web", "xberg"}
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

    for name in (
        "opensearch",
        "opensearch-dashboards",
        "opensearch-bridge",
        "data-prepper",
        "alloy",
    ):
        service = services.get(name)
        if not isinstance(service, dict):
            failures.append(f"Default observability service is missing: {name}")
        elif service.get("profiles"):
            failures.append(
                f"Default observability service must not require a profile: {name}"
            )

    for name in ("grafana", "loki", "alloy-loki"):
        service = services.get(name)
        if not isinstance(service, dict):
            failures.append(f"Optional Grafana service is missing: {name}")
        elif service.get("profiles") != ["grafana"]:
            failures.append(
                f"Optional Grafana service must stay in the grafana profile: {name}"
            )

    langfuse_services = (
        "langfuse-preflight",
        "langfuse-postgres",
        "langfuse-clickhouse",
        "langfuse-clickhouse-observability-setup",
        "langfuse-minio",
        "langfuse-redis",
        "langfuse-worker",
        "langfuse-web",
    )
    for name in langfuse_services:
        service = services.get(name)
        if not isinstance(service, dict):
            failures.append(f"Optional Langfuse service is missing: {name}")
        elif service.get("profiles") != ["langfuse"]:
            failures.append(
                f"Optional Langfuse service must stay in the langfuse profile: {name}"
            )

    for name in ("litellm-prod", "litellm-dev"):
        service = services.get(name)
        if not isinstance(service, dict):
            continue
        depends_on = service.get("depends_on", {})
        langfuse_dependency = (
            depends_on.get("langfuse-web") if isinstance(depends_on, dict) else None
        )
        if not isinstance(langfuse_dependency, dict) or (
            langfuse_dependency.get("condition") != "service_healthy"
            or langfuse_dependency.get("required") is not False
        ):
            failures.append(
                f"{name} must treat healthy Langfuse as an optional dependency"
            )

    for name in langfuse_services[1:]:
        service = services.get(name)
        if (
            isinstance(service, dict)
            and "langfuse-preflight" not in required_dependency_names(service)
        ):
            failures.append(f"{name} must depend on langfuse-preflight")

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
        health_command = (
            " ".join(str(value) for value in health_test)
            if isinstance(health_test, list)
            else ""
        )
        if "127.0.0.1" not in no_proxy:
            failures.append("Xberg loopback health traffic is not excluded from its proxy")
        if "--fail" not in health_command:
            failures.append("Xberg healthcheck does not fail on HTTP error responses")
        if "getent hosts docs.xberg.io" not in health_command:
            failures.append("Xberg healthcheck does not validate sandbox DNS resolution")
        xberg_dns = xberg.get("dns", [])
        if not isinstance(xberg_dns, list) or len(xberg_dns) != 1:
            failures.append("Xberg does not use the sandbox DNS relay")
        if set(xberg.get("networks", {})) != {"xberg-sandbox"}:
            failures.append("Xberg must remain isolated to the xberg-sandbox network")
        command = xberg.get("command", [])
        if (
            not isinstance(command, list)
            or "--config" not in command
            or "/etc/xberg/xberg.toml" not in command
        ):
            failures.append("Xberg does not load its operator-owned SSRF policy")

    xberg_dns_relay = services.get("xberg-dns")
    if not isinstance(xberg_dns_relay, dict):
        failures.append("Compose configuration has no xberg-dns service")
    else:
        relay_networks = xberg_dns_relay.get("networks", {})
        if set(relay_networks) != {"egress", "xberg-sandbox"}:
            failures.append(
                "Xberg DNS relay must connect only to xberg-sandbox and egress"
            )
        sandbox_network = (
            relay_networks.get("xberg-sandbox", {})
            if isinstance(relay_networks, dict)
            else {}
        )
        relay_ip = (
            sandbox_network.get("ipv4_address")
            if isinstance(sandbox_network, dict)
            else None
        )
        xberg_dns = xberg.get("dns", []) if isinstance(xberg, dict) else []
        if (
            not isinstance(relay_ip, str)
            or not relay_ip
            or not isinstance(xberg_dns, list)
            or xberg_dns != [relay_ip]
        ):
            failures.append("Xberg DNS relay does not use the reserved sandbox address")

    return failures


def backup_freshness_metrics(timestamp: int) -> str:
    if timestamp < 0:
        raise ValueError("backup metric timestamp must be non-negative")
    return (
        f"# HELP {BACKUP_FRESHNESS_METRIC} "
        "Unix timestamp of the last complete database backup.\n"
        f"# TYPE {BACKUP_FRESHNESS_METRIC} gauge\n"
        f"{BACKUP_FRESHNESS_METRIC} {timestamp}\n"
    )


def restore_database_names(database: str, process_id: int) -> tuple[str, str]:
    if not re.fullmatch(r"[a-z0-9_]+", database):
        raise ValueError("unsafe database identifier")
    if process_id <= 0:
        raise ValueError("process ID must be positive")
    return (
        f"{database}_restore_{process_id}",
        f"{database}_pre_restore_{process_id}",
    )


def terminate_database_connections_sql(database: str) -> str:
    if not re.fullmatch(r"[a-z0-9_]+", database):
        raise ValueError("unsafe database identifier")
    return (
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{database}' AND pid <> pg_backend_pid()"
    )


def render_rollback_override(
    services: Sequence[str], images: Mapping[str, str]
) -> str:
    if not services:
        raise ValueError("rollback override requires at least one service")
    lines = [ACTIVE_ROLLBACK_MARKER, "services:"]
    for service in services:
        if service not in {"litellm-prod", "litellm-dev"}:
            raise ValueError(f"unsupported rollback service: {service}")
        image = images.get(service, "")
        if not IMAGE_REF_RE.fullmatch(image):
            raise ValueError(f"invalid immutable image for {service}")
        lines.extend((f"  {service}:", f"    image: {image}"))
    return "\n".join(lines) + "\n"


def alert_rule_uids(alert_directory: Path) -> set[str]:
    uids: set[str] = set()
    for path in sorted(alert_directory.glob("*.yml")):
        if not path.is_file() or path.is_symlink():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.fullmatch(
                r"\s*(?:-\s*)?uid:\s*([A-Za-z0-9_-]+)\s*",
                line,
            )
            if match:
                uids.add(match.group(1))
    return uids


def validate_repository_contracts(root: Path = ROOT) -> list[str]:
    failures: list[str] = []

    missing_alerts = EXPECTED_ALERT_UIDS - alert_rule_uids(
        root / "grafana" / "alerting"
    )
    if missing_alerts:
        failures.append(
            "Required production alerts are missing: "
            + ", ".join(sorted(missing_alerts))
        )

    metric = backup_freshness_metrics(1)
    if (
        f"# TYPE {BACKUP_FRESHNESS_METRIC} gauge" not in metric
        or f"{BACKUP_FRESHNESS_METRIC} 1" not in metric
    ):
        failures.append("Complete-backup freshness metric contract is invalid")

    staging, previous = restore_database_names("litellm_prod", 1)
    if staging != "litellm_prod_restore_1" or previous != "litellm_prod_pre_restore_1":
        failures.append("Restore rename-aside database naming contract is invalid")
    if "pg_terminate_backend" not in terminate_database_connections_sql(
        "litellm_prod"
    ):
        failures.append("Restore session-termination contract is invalid")
    required_clients = {"pgvector-exporter", "opensearch-bridge"}
    if not required_clients.issubset(RESTORE_DATABASE_CLIENTS):
        failures.append(
            "Restore database-client stop contract is missing: "
            + ", ".join(sorted(required_clients - set(RESTORE_DATABASE_CLIENTS)))
        )

    sample_image = "example.invalid/litellm@sha256:" + ("0" * 64)
    override = render_rollback_override(
        ["litellm-prod"], {"litellm-prod": sample_image}
    )
    if not override.startswith(ACTIVE_ROLLBACK_MARKER + "\n"):
        failures.append("Rollback override marker contract is invalid")
    if f"image: {sample_image}" not in override:
        failures.append("Rollback override image contract is invalid")

    gitignore = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    if ACTIVE_ROLLBACK_OVERRIDE.name not in gitignore:
        failures.append("The persistent rollback override is not ignored by Git")

    proxy_allowlist = {
        line.lstrip(".")
        for line in (
            root / "security" / "xberg-egress-allowlist.txt"
        ).read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }
    with (root / "security" / "xberg-config.toml").open("rb") as handle:
        xberg_config = tomllib.load(handle)
    ssrf_allowlist = {
        str(matcher.get("value", "")).lstrip(".")
        for matcher in xberg_config.get("url", {})
        .get("crawl", {})
        .get("ssrf", {})
        .get("allowlist", [])
        if isinstance(matcher, dict)
        and matcher.get("type") in {"exact", "suffix"}
    }
    if not proxy_allowlist or ssrf_allowlist != proxy_allowlist:
        failures.append(
            "Xberg SSRF and egress-proxy destination allowlists are not aligned"
        )

    workflow_bindings = {
        "backup-databases.py": {
            "backup_freshness_metrics": backup_freshness_metrics,
        },
        "restore-databases.py": {
            "RESTORE_DATABASE_CLIENTS": RESTORE_DATABASE_CLIENTS,
            "restore_database_names": restore_database_names,
            "terminate_database_connections_sql": terminate_database_connections_sql,
        },
        "rollback-litellm-upgrade.py": {
            "ACTIVE_ROLLBACK_MARKER": ACTIVE_ROLLBACK_MARKER,
            "ACTIVE_ROLLBACK_OVERRIDE": ACTIVE_ROLLBACK_OVERRIDE,
            "render_rollback_override": render_rollback_override,
        },
    }
    for script_name, expected_bindings in workflow_bindings.items():
        script = root / "scripts" / script_name
        if (
            not script.is_file()
            or script.is_symlink()
            or not script.stat().st_mode & stat.S_IXUSR
        ):
            failures.append(
                f"Required executable maintenance workflow is missing or unsafe: {script_name}"
            )
            continue
        try:
            namespace = runpy.run_path(
                str(script),
                run_name=f"_maintenance_contract_{script.stem.replace('-', '_')}",
            )
        except (ImportError, OSError, RuntimeError, SyntaxError, ValueError) as error:
            failures.append(f"Could not load maintenance workflow {script_name}: {error}")
            continue
        disconnected = [
            name
            for name, expected in expected_bindings.items()
            if namespace.get(name) is not expected
        ]
        if disconnected:
            failures.append(
                f"Maintenance workflow {script_name} is disconnected from shared contracts: "
                + ", ".join(sorted(disconnected))
            )

    return failures
