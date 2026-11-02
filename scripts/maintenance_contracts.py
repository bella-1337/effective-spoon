"""Shared static and behavioral contracts for stack maintenance workflows."""

from __future__ import annotations

import json
import re
import runpy
import stat
import tomllib
from pathlib import Path
from typing import Any, Mapping, Sequence

from maintenance_common import (
    HOSTNAME_RE,
    IMAGE_REF_RE,
    PUBLIC_HOST_VARIABLES,
    ROOT,
    compose_capture,
    compose_command,
    env_file_is_present,
)

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
TRAEFIK_HOST_RULE_RE = re.compile(r"Host\(`([^`]*)`\)")
# A public hostname fallback such as ${LITELLM_PROD_HOST:-llm.example.test}
# would silently route a misconfigured deployment to a placeholder domain.
HARDCODED_HOST_FALLBACK_RE = re.compile(r"\$\{[A-Z0-9_]*HOST:?-[^}$]*\.[^}$]*\}")
STACK_DOMAIN_REFERENCE = "${STACK_DOMAIN}"
# LiteLLM serves a local logo or favicon only when its file header is one of these
# raster formats (litellm.proxy.common_utils.static_asset_utils); other values
# must be http(s) URLs, which LiteLLM redirects the browser to.
BRAND_RASTER_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF8", b"RIFF", b"\x00\x00\x01\x00")
BRAND_IMAGE_VARIABLES = ("UI_LOGO_PATH", "UI_LOGO_PATH_DARK", "LITELLM_FAVICON_URL")
BRAND_CONTAINER_DIR = "/app/branding/"
UNSAFE_SVG_RE = re.compile(r"<script|\son[a-z]+\s*=|javascript:|<foreignObject", re.IGNORECASE)
LICENCE_REGISTER = Path("docs") / "third-party-licences.md"
LICENCE_REGISTER_IMAGE_SECTIONS = ("## Runtime images", "## Build and auxiliary images")
LOCAL_BUILD_DOCKERFILES = (
    Path("pgvector") / "Dockerfile",
    Path("opensearch") / "tools" / "Dockerfile",
    Path("security") / "image-monitor.Dockerfile",
)
DOCKERFILE_FROM_RE = re.compile(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)", re.MULTILINE | re.IGNORECASE)
D3_IMAGE_RE = re.compile(r"^D3_[A-Z_]*IMAGE='([^']+)'", re.MULTILINE)
D3_REVISION_RE = re.compile(r"^D3_MODEL_REVISION='([0-9a-f]{40})'", re.MULTILINE)
TRIVY_IMAGE_RE = re.compile(r'^\s*"(aquasec/trivy:[^"@]+)"', re.MULTILINE)
OWN_IMAGE_REQUIRED_LABELS = (
    "vendor",
    "title",
    "description",
    "version",
    "revision",
    "licenses",
    "base.name",
)
# Base images such as uv set these; own images must override them explicitly.
OWN_IMAGE_OVERRIDDEN_LABELS = ("created", "source", "url")
REGISTER_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|", re.MULTILINE)


def compose_profile_arguments() -> list[str]:
    return [
        argument
        for profile in COMPOSE_PROFILES
        for argument in ("--profile", profile)
    ]


def load_compose_config(
    env_file: Path,
    optional_env_file: Path | None = None,
) -> dict[str, Any]:
    resolved_optional_env = optional_env_file
    if resolved_optional_env is None:
        local_optional_env = ROOT / ".env.optional"
        # Fall back to the example only when no local file exists; an unsafe
        # local file must fail instead of validating placeholder values.
        resolved_optional_env = (
            local_optional_env
            if env_file_is_present(local_optional_env)
            else ROOT / ".env.optional.example"
        )
    output = compose_capture(
        [
            "--env-file",
            env_file,
            "--env-file",
            resolved_optional_env,
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

    for name in ("grafana-db-init", "grafana", "loki", "alloy-loki"):
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

    langfuse_web = services.get("langfuse-web")
    if isinstance(langfuse_web, dict):
        web_environment = langfuse_web.get("environment", {})
        if (
            not isinstance(web_environment, dict)
            or str(web_environment.get("AUTH_DISABLE_SIGNUP", "")).lower() != "true"
        ):
            failures.append("langfuse-web must disable public self-registration")

    for name in langfuse_services[1:]:
        service = services.get(name)
        if (
            isinstance(service, dict)
            and "langfuse-preflight" not in required_dependency_names(service)
        ):
            failures.append(f"{name} must depend on langfuse-preflight")

    for name in ("image-security-docker-proxy", "image-security-monitor"):
        service = services.get(name)
        if not isinstance(service, dict):
            failures.append(f"Optional image-security service is missing: {name}")
        elif service.get("profiles") != ["security"]:
            failures.append(
                f"Optional image-security service must stay in the security profile: {name}"
            )

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

    searxng = services.get("searxng")
    if not isinstance(searxng, dict):
        failures.append("Compose configuration has no searxng service")
    else:
        if set(searxng.get("networks", {})) != {"internal"}:
            failures.append("SearXNG must stay on the internal network only")
        labels = searxng.get("labels", {})
        label_keys = (
            {str(label).partition("=")[0] for label in labels}
            if isinstance(labels, list)
            else set(labels) if isinstance(labels, dict) else set()
        )
        if any(key.startswith("traefik.") for key in label_keys):
            failures.append("SearXNG must not have a Traefik route")
        if searxng.get("ports"):
            failures.append("SearXNG must not publish host ports")

    failures.extend(public_hostname_failures(services))
    failures.extend(branding_failures(services))
    failures.extend(edge_and_abuse_compose_failures(services))
    return failures


def image_without_digest(reference: str) -> str:
    return reference.partition("@")[0]


def register_image_rows(source: str) -> set[str]:
    """Return image references from the register's image tables."""
    images: set[str] = set()
    section = ""
    for line in source.splitlines():
        if line.startswith("## "):
            section = line.strip()
            continue
        if section in LICENCE_REGISTER_IMAGE_SECTIONS:
            match = REGISTER_ROW_RE.match(line)
            if match:
                images.add(match.group(1))
    return images


def auxiliary_images(root: Path) -> tuple[set[str], list[str]]:
    """Collect build bases and non-Compose images the stack executes."""
    images: set[str] = set()
    failures: list[str] = []
    for relative in LOCAL_BUILD_DOCKERFILES:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            failures.append(f"Local build Dockerfile is missing or unsafe: {relative}")
            continue
        for reference in DOCKERFILE_FROM_RE.findall(path.read_text(encoding="utf-8")):
            images.add(image_without_digest(reference))
    deploy_d3 = root / "scripts" / "deploy-d3-mini.sh"
    if deploy_d3.is_file() and not deploy_d3.is_symlink():
        images.update(
            image_without_digest(reference)
            for reference in D3_IMAGE_RE.findall(deploy_d3.read_text(encoding="utf-8"))
        )
    monitor = root / "scripts" / "monitor-image-vulnerabilities.py"
    if monitor.is_file() and not monitor.is_symlink():
        images.update(TRIVY_IMAGE_RE.findall(monitor.read_text(encoding="utf-8")))
    return images, failures


def compose_model_revisions(services: Mapping[str, Any]) -> set[str]:
    revisions: set[str] = set()
    for service in services.values():
        command = service.get("command") if isinstance(service, dict) else None
        if not isinstance(command, list):
            continue
        for option, value in zip(command, command[1:]):
            if option in {"--revision", "--tokenizer-revision"} and isinstance(value, str):
                revisions.add(value)
    return revisions


def rendered_supply_chain_failures(config: Mapping[str, Any]) -> list[str]:
    """Check the register and own-image labels against a fully rendered config."""
    services = config.get("services")
    if not isinstance(services, dict):
        return ["Compose configuration has no services object"]
    return [
        *licence_register_failures(services),
        *own_image_label_failures(config),
    ]


def final_base_image(dockerfile: Path) -> str | None:
    if not dockerfile.is_file() or dockerfile.is_symlink():
        return None
    references = DOCKERFILE_FROM_RE.findall(dockerfile.read_text(encoding="utf-8"))
    return image_without_digest(references[-1]) if references else None


def own_image_label_failures(config: Mapping[str, Any], root: Path = ROOT) -> list[str]:
    """Require consistent OCI labels on every image this repository builds."""
    failures: list[str] = []
    services = config.get("services")
    if not isinstance(services, dict):
        return failures
    vendor = config.get("x-stack-domain")
    for name, service in sorted(services.items()):
        build = service.get("build") if isinstance(service, dict) else None
        if not isinstance(build, dict):
            continue
        labels = build.get("labels")
        labels = labels if isinstance(labels, dict) else {}
        missing = [
            key
            for key in OWN_IMAGE_REQUIRED_LABELS
            if not str(labels.get(f"org.opencontainers.image.{key}") or "").strip()
        ]
        missing.extend(
            key
            for key in OWN_IMAGE_OVERRIDDEN_LABELS
            if f"org.opencontainers.image.{key}" not in labels
        )
        if missing:
            failures.append(
                f"Locally built image of {name} lacks OCI labels: " + ", ".join(missing)
            )
            continue
        if isinstance(vendor, str) and labels["org.opencontainers.image.vendor"] != vendor:
            failures.append(f"Vendor label of {name} must equal STACK_DOMAIN")
        image = service.get("image")
        tag = str(image).partition("@")[0].rsplit("/", 1)[-1].partition(":")[2] if image else ""
        if labels["org.opencontainers.image.version"] != tag:
            failures.append(f"Version label of {name} must equal its image tag")
        base = final_base_image(root / str(build.get("dockerfile") or "Dockerfile"))
        if labels["org.opencontainers.image.base.name"] != base:
            failures.append(
                f"Base-name label of {name} must equal the final FROM image of its Dockerfile"
            )
    return failures


def licence_register_failures(
    services: Mapping[str, Any], root: Path = ROOT
) -> list[str]:
    """Keep docs/third-party-licences.md in step with every image and model pin."""
    register = root / LICENCE_REGISTER
    if not register.is_file() or register.is_symlink():
        return [f"Licence register is missing or unsafe: {LICENCE_REGISTER}"]
    source = register.read_text(encoding="utf-8")
    listed = register_image_rows(source)
    required = {
        image_without_digest(service["image"])
        for service in services.values()
        if isinstance(service, dict) and isinstance(service.get("image"), str)
    }
    extra, failures = auxiliary_images(root)
    required |= extra
    failures.extend(
        f"Licence register has no row for image {image}"
        for image in sorted(required - listed)
    )
    failures.extend(
        f"Licence register lists an image the stack no longer uses: {image}"
        for image in sorted(listed - required)
    )
    revisions = compose_model_revisions(services)
    deploy_d3 = root / "scripts" / "deploy-d3-mini.sh"
    if deploy_d3.is_file() and not deploy_d3.is_symlink():
        revisions.update(D3_REVISION_RE.findall(deploy_d3.read_text(encoding="utf-8")))
    failures.extend(
        f"Licence register does not record pinned model revision {revision}"
        for revision in sorted(revisions)
        if f"`{revision}`" not in source
    )
    for name, service in sorted(services.items()):
        build = service.get("build") if isinstance(service, dict) else None
        labels = build.get("labels") if isinstance(build, dict) else None
        revision = (
            labels.get("org.opencontainers.image.revision")
            if isinstance(labels, dict)
            else None
        )
        contexts = build.get("additional_contexts") if isinstance(build, dict) else None
        if revision is None or not isinstance(contexts, dict) or not contexts:
            continue
        sources = contexts.values()
        if not any(str(value).endswith(f"#{revision}") for value in sources):
            failures.append(
                f"Build revision label of {name} does not match its pinned upstream context"
            )
    return failures


def branding_failures(services: Mapping[str, Any]) -> list[str]:
    """Check that both gateways use LiteLLM's native, read-only branding inputs."""
    failures: list[str] = []
    for name in ("litellm-prod", "litellm-dev"):
        service = services.get(name)
        if not isinstance(service, dict):
            continue
        environment = service.get("environment", {})
        environment = environment if isinstance(environment, dict) else {}
        mounts = {
            str(volume.get("target")): volume
            for volume in service.get("volumes", [])
            if isinstance(volume, dict)
        }
        branding_mount = mounts.get(BRAND_CONTAINER_DIR.rstrip("/"))
        if not branding_mount or branding_mount.get("read_only") is not True:
            failures.append(f"{name} must mount the brand asset directory read-only at /app/branding")
        for variable in BRAND_IMAGE_VARIABLES:
            value = str(environment.get(variable) or "")
            if value.startswith("https://") or value.startswith(BRAND_CONTAINER_DIR):
                continue
            failures.append(
                f"{name} {variable} must be an https:// URL or a file under {BRAND_CONTAINER_DIR}"
            )
    return failures


def public_hostname_failures(services: Mapping[str, Any]) -> list[str]:
    """Reject Traefik routers whose rendered Host() is not a DNS hostname."""
    failures: list[str] = []
    for name, service in sorted(services.items()):
        labels = service.get("labels", {}) if isinstance(service, dict) else {}
        if isinstance(labels, list):
            labels = dict(str(label).partition("=")[::2] for label in labels)
        if not isinstance(labels, dict):
            continue
        for key, rule in sorted(labels.items()):
            if not (str(key).startswith("traefik.http.routers.") and str(key).endswith(".rule")):
                continue
            for host in TRAEFIK_HOST_RULE_RE.findall(str(rule)):
                if not HOSTNAME_RE.fullmatch(host):
                    failures.append(
                        f"{name} Traefik rule has an invalid public hostname {host!r}; "
                        "set STACK_DOMAIN or the matching *_HOST variable"
                    )
    return failures


def brand_asset_failures(root: Path, env_examples: Mapping[str, str]) -> list[str]:
    """Require the example brand files LiteLLM serves to be supported raster images."""
    assets_dir = env_examples.get("BRAND_ASSETS_DIR", "")
    if not assets_dir or assets_dir.startswith("/") or ".." in Path(assets_dir).parts:
        return ["BRAND_ASSETS_DIR in .env.example must be a repository-relative directory"]
    directory = root / assets_dir
    failures: list[str] = []
    for variable in BRAND_IMAGE_VARIABLES:
        value = env_examples.get(variable, "")
        if not value.startswith(BRAND_CONTAINER_DIR):
            continue
        path = directory / value.removeprefix(BRAND_CONTAINER_DIR)
        if not path.is_file() or path.is_symlink():
            failures.append(f"{variable} example file is missing: {assets_dir}/{path.name}")
        elif not path.read_bytes()[:8].startswith(BRAND_RASTER_SIGNATURES):
            failures.append(
                f"{variable} example file is not a PNG, JPEG, GIF, WEBP, or ICO image "
                f"that LiteLLM can serve: {assets_dir}/{path.name}"
            )
    for svg in sorted(directory.rglob("*.svg")):
        if UNSAFE_SVG_RE.search(svg.read_text(encoding="utf-8")):
            failures.append(f"Brand SVG contains scriptable content: {svg.relative_to(root)}")
    return failures


def public_hostname_source_failures(
    compose_sources: Mapping[str, str],
    env_examples: Mapping[str, Mapping[str, str]],
) -> list[str]:
    """Keep public hostnames configurable from STACK_DOMAIN, never hard-coded."""
    failures: list[str] = []
    for name, source in sorted(compose_sources.items()):
        for fallback in sorted(set(HARDCODED_HOST_FALLBACK_RE.findall(source))):
            failures.append(f"{name} hard-codes a public hostname fallback: {fallback}")
    core_source = compose_sources.get("docker-compose.yml", "")
    if not re.search(r"^x-stack-domain: \$\{STACK_DOMAIN:\?", core_source, re.MULTILINE):
        failures.append("docker-compose.yml does not require STACK_DOMAIN")
    if not env_examples.get(".env.example", {}).get("STACK_DOMAIN"):
        failures.append("STACK_DOMAIN is missing from .env.example")
    for variable in PUBLIC_HOST_VARIABLES:
        values = [
            values[variable]
            for values in env_examples.values()
            if variable in values
        ]
        if len(values) != 1:
            failures.append(
                f"{variable} must be defined in exactly one environment example"
            )
        elif STACK_DOMAIN_REFERENCE not in values[0]:
            failures.append(
                f"{variable} example must derive from {STACK_DOMAIN_REFERENCE}"
            )
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


def traefik_dynamic_config_failures(source: str) -> list[str]:
    """Reject Traefik file-provider config that could route to SearXNG."""
    uncommented = "\n".join(line.partition("#")[0] for line in source.splitlines())
    if re.search(r"searxng", uncommented, re.IGNORECASE):
        return ["Traefik dynamic configuration must not route to internal-only SearXNG"]
    return []


EDGE_MIDDLEWARES = ("edge-security-headers", "edge-rate-limit", "edge-in-flight")
WEAK_TLS12_CIPHER_RE = re.compile(r"_CBC_|_SHA\s*$|_RC4_|_3DES_", re.MULTILINE)


def traefik_edge_hardening_failures(source: str) -> list[str]:
    """Require the edge TLS policy, security headers, and abuse limits."""
    uncommented = "\n".join(line.partition("#")[0] for line in source.splitlines())
    failures: list[str] = []
    if not re.search(r"^\s+minVersion:\s*VersionTLS1[23]\s*$", uncommented, re.MULTILINE):
        failures.append("Traefik default TLS options must require TLS 1.2 or newer")
    cipher_block = re.search(r"cipherSuites:\s*\n((?:\s+-\s*\S+\s*\n?)+)", uncommented)
    if not cipher_block or WEAK_TLS12_CIPHER_RE.search(cipher_block.group(1)):
        failures.append("Traefik TLS 1.2 cipher suites must be an explicit AEAD-only list")
    if not re.search(r"^\s+stsSeconds:\s*[1-9][0-9]*\s*$", uncommented, re.MULTILINE):
        failures.append("Traefik must send an HSTS header")
    for name in EDGE_MIDDLEWARES:
        if not re.search(rf"^\s+{re.escape(name)}:\s*$", uncommented, re.MULTILINE):
            failures.append(f"Traefik dynamic configuration is missing middleware {name}")
    return failures


def edge_and_abuse_compose_failures(services: Mapping[str, Any]) -> list[str]:
    """Reject Compose drift that would undo the DDoS and MITM protections."""
    failures: list[str] = []
    traefik = services.get("traefik")
    if isinstance(traefik, dict):
        command = traefik.get("command", []) or []
        entrypoint_middlewares = next(
            (
                str(argument).partition("=")[2]
                for argument in command
                if str(argument).startswith("--entrypoints.websecure.http.middlewares=")
            ),
            "",
        )
        attached = {name.partition("@")[0] for name in entrypoint_middlewares.split(",")}
        missing = [name for name in EDGE_MIDDLEWARES if name not in attached]
        if missing:
            failures.append(
                "Traefik websecure entrypoint lacks edge middlewares: " + ", ".join(missing)
            )
        if any(
            str(argument).startswith("--entrypoints.websecure.forwardedheaders")
            for argument in command
        ):
            failures.append("Traefik must not trust client-supplied forwarded headers")

    for name in ("traefik", "litellm-prod", "litellm-dev", "blackbox", "xberg-egress-proxy"):
        service = services.get(name)
        if not isinstance(service, dict):
            continue
        cap_drop = {str(cap).upper() for cap in service.get("cap_drop", []) or []}
        if not cap_drop & {"NET_RAW", "ALL"}:
            failures.append(f"{name} must drop NET_RAW to prevent ARP spoofing")

    for name in ("presidio-analyzer", "presidio-anonymizer"):
        service = services.get(name)
        if not isinstance(service, dict):
            continue
        environment = service.get("environment", {})
        workers = str(environment.get("WORKERS", "")) if isinstance(environment, dict) else ""
        if not workers.isdigit() or int(workers) < 2:
            failures.append(f"{name} needs at least two workers so one prompt cannot stall masking")
    return failures


def xberg_sandbox_policy_failures(root: Path) -> list[str]:
    """Keep Xberg local-file access, plaintext egress, and DNS exfiltration closed."""
    failures: list[str] = []
    security = root / "security"
    with (security / "xberg-config.toml").open("rb") as handle:
        url_config = tomllib.load(handle).get("url", {})
    if url_config.get("allow_local_file_inputs") is not False or url_config.get(
        "allow_file_uris"
    ) is not False:
        failures.append("Xberg must disable local path and file:// inputs")

    squid = [
        line.strip()
        for line in (security / "xberg-egress-squid.conf").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    access = [line for line in squid if line.startswith("http_access ")]
    if "http_access deny !CONNECT" not in access:
        failures.append("Xberg egress proxy must allow only HTTPS CONNECT tunnels")
    if any(re.fullmatch(r"acl Safe_ports port 80", line) for line in squid):
        failures.append("Xberg egress proxy must not allow plaintext port 80")
    try:
        allowlist_index = access.index("http_access deny !allowed_domains")
        dst_index = min(
            index for index, line in enumerate(access) if "blocked_ipv" in line
        )
    except ValueError:
        failures.append("Xberg egress proxy must deny unlisted names before resolving them")
    else:
        if allowlist_index > dst_index:
            failures.append("Xberg egress proxy must deny unlisted names before resolving them")
    if "pinger_enable off" not in squid:
        failures.append("Xberg egress proxy must disable the raw-socket ICMP pinger")

    zones_source = (security / "xberg-dns-zones.conf").read_text(encoding="utf-8")
    root_zone = (security / "xberg-dns-deny-root.zone").read_text(encoding="utf-8")
    if not re.search(r'zone\s+"\."\s*\{[^}]*type\s+primary;', zones_source):
        failures.append("Xberg DNS relay must serve an authoritative empty root")
    forward_zones = set(
        re.findall(r'zone\s+"([^"]+)"\s*\{[^}]*type\s+forward;', zones_source)
    )
    delegated = {
        name.rstrip(".")
        for name in re.findall(r"^(\S+)\s+IN\s+NS\s+", root_zone, re.MULTILINE)
        if name not in {"@", "."}
    }
    if forward_zones != delegated:
        failures.append("Xberg DNS forward zones and root delegations are not aligned")
    proxy_allowlist = {
        line.strip().lstrip(".")
        for line in (security / "xberg-egress-allowlist.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    unresolvable = proxy_allowlist - forward_zones
    if unresolvable:
        failures.append(
            "Xberg egress allowlist entries have no DNS forward zone: "
            + ", ".join(sorted(unresolvable))
        )
    return failures


def litellm_guardrail_failures(root: Path) -> list[str]:
    """Keep the prompt-injection screen, Presidio chunking, and key safety nets wired."""
    failures: list[str] = []
    modules = root / "litellm" / "modules" / "guardrails"
    injection = modules / "prompt-injection.yaml"
    injection_source = injection.read_text(encoding="utf-8") if injection.is_file() else ""
    if (
        "guardrail: litellm_content_filter" not in injection_source
        or not re.search(r"mode:\s*\[[^\]]*post_mcp_call", injection_source)
        or not re.search(r"default_on:\s*true", injection_source)
    ):
        failures.append("MCP tool output must pass a default-on prompt-injection guardrail")
    presidio_source = (modules / "presidio.yaml").read_text(encoding="utf-8")
    chunk = re.search(r"presidio_analyze_chunk_size_bytes:\s*([0-9]+)", presidio_source)
    if not chunk or not 0 < int(chunk.group(1)) <= 200_000:
        failures.append("Presidio analysis must chunk prompts at 200 KB or less")
    for config_name in ("config.prod.yaml", "config.dev.yaml"):
        source = (root / "litellm" / config_name).read_text(encoding="utf-8")
        if "modules/guardrails/prompt-injection.yaml" not in source:
            failures.append(f"{config_name} does not include the prompt-injection guardrail")
        defaults = re.search(
            r"default_key_generate_params:\s*\n((?:\s{4,}\S.*\n)+)", source
        )
        if not defaults or not all(
            re.search(rf"^\s+{key}:\s*[1-9][0-9]*\s*$", defaults.group(1), re.MULTILINE)
            for key in ("max_parallel_requests", "rpm_limit")
        ):
            failures.append(f"{config_name} must set default per-key request limits")
    return failures


def validate_repository_contracts(root: Path = ROOT) -> list[str]:
    failures: list[str] = []

    traefik_dynamic = root / "traefik" / "dynamic.yml"
    if not traefik_dynamic.is_file() or traefik_dynamic.is_symlink():
        failures.append("Traefik dynamic configuration is missing or unsafe")
    else:
        traefik_source = traefik_dynamic.read_text(encoding="utf-8")
        failures.extend(traefik_dynamic_config_failures(traefik_source))
        failures.extend(traefik_edge_hardening_failures(traefik_source))

    failures.extend(xberg_sandbox_policy_failures(root))
    failures.extend(litellm_guardrail_failures(root))

    compose_file = root / "docker-compose.yml"
    optional_compose_file = root / "compose.optional.yml"
    core_env_example = root / ".env.example"
    optional_env_example = root / ".env.optional.example"
    required_files = (compose_file, optional_compose_file, core_env_example, optional_env_example)
    missing_files = [path.name for path in required_files if not path.is_file() or path.is_symlink()]
    if missing_files:
        failures.append(
            "Required Compose split files are missing or unsafe: "
            + ", ".join(sorted(missing_files))
        )
    else:
        compose_source = compose_file.read_text(encoding="utf-8")
        optional_compose_source = optional_compose_file.read_text(encoding="utf-8")
        if (
            "path: compose.optional.yml" not in compose_source
            or "env_file: .env.optional.example" not in compose_source
        ):
            failures.append(
                "Core Compose does not include the optional module with safe defaults"
            )
        optional_services = (
            "grafana",
            "loki",
            "alloy-loki",
            "langfuse-web",
            "image-security-monitor",
        )
        for service in optional_services:
            declaration = re.compile(rf"^  {re.escape(service)}:\s*$", re.MULTILINE)
            if declaration.search(compose_source):
                failures.append(
                    f"Optional service remains in the core Compose file: {service}"
                )
            if not declaration.search(optional_compose_source):
                failures.append(
                    f"Optional service is missing from the optional Compose file: {service}"
                )

        def env_keys(path: Path) -> set[str]:
            return {
                line.partition("=")[0]
                for line in path.read_text(encoding="utf-8").splitlines()
                if re.fullmatch(r"[A-Z][A-Z0-9_]*=.*", line)
            }

        core_env_keys = env_keys(core_env_example)
        optional_env_keys = env_keys(optional_env_example)

        def env_values(path: Path) -> dict[str, str]:
            return {
                key: value
                for key, _, value in (
                    line.partition("=")
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if re.fullmatch(r"[A-Z][A-Z0-9_]*=.*", line)
                )
            }

        failures.extend(brand_asset_failures(root, env_values(core_env_example)))
        failures.extend(
            public_hostname_source_failures(
                {
                    compose_file.name: compose_source,
                    optional_compose_file.name: optional_compose_source,
                },
                {
                    core_env_example.name: env_values(core_env_example),
                    optional_env_example.name: env_values(optional_env_example),
                },
            )
        )
        core_langfuse_keys = {
            "LANGFUSE_PUBLIC_KEY",
            "LANGFUSE_SECRET_KEY",
            "LANGFUSE_USER_ID_SALT",
        }
        misplaced_optional_keys = {
            key
            for key in core_env_keys
            if key.startswith(("GRAFANA_", "LANGFUSE_", "IMAGE_SECURITY_"))
            and key not in core_langfuse_keys
        }
        if misplaced_optional_keys:
            failures.append(
                "Optional variables remain in .env.example: "
                + ", ".join(sorted(misplaced_optional_keys))
            )
        missing_core_langfuse_keys = core_langfuse_keys - core_env_keys
        if missing_core_langfuse_keys:
            failures.append(
                "Core-consumed Langfuse variables are missing from .env.example: "
                + ", ".join(sorted(missing_core_langfuse_keys))
            )
        duplicated_core_langfuse_keys = core_langfuse_keys & optional_env_keys
        if duplicated_core_langfuse_keys:
            failures.append(
                "Core-consumed Langfuse variables remain in .env.optional.example: "
                + ", ".join(sorted(duplicated_core_langfuse_keys))
            )
        required_optional_keys = {
            "GRAFANA_ADMIN_PASSWORD",
            "LANGFUSE_POSTGRES_PASSWORD",
            "IMAGE_SECURITY_UID",
        }
        missing_optional_keys = required_optional_keys - optional_env_keys
        if missing_optional_keys:
            failures.append(
                "Required optional variables are missing from .env.optional.example: "
                + ", ".join(sorted(missing_optional_keys))
            )

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
            "compose_command": compose_command,
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
