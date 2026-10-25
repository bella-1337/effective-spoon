#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Scan running Compose images and publish aggregate security metrics."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

from maintenance_common import (
    IMAGE_REF_RE,
    ROOT,
    ScriptError,
    capture,
    fail,
    require_docker,
    run,
    run_main,
    safe_output_root,
    utc_timestamp,
)

TRIVY_IMAGE = (
    "aquasec/trivy:0.76.1"
    "@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c"
)
TRIVY_VERSION = "0.76.1"
KEV_URL = (
    "https://www.cisa.gov/sites/default/files/feeds/"
    "known_exploited_vulnerabilities.json"
)
DEFAULT_SERVICES = ("litellm-prod", "litellm-dev")
SEVERITIES = ("UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL")
MAX_KEV_BYTES = 8 * 1024 * 1024
MAX_STATE_BYTES = 8 * 1024 * 1024
IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.IGNORECASE)
SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class RunningImage:
    service: str
    configured_reference: str
    local_image_id: str
    platform: str


@dataclass(frozen=True)
class ExceptionRule:
    service: str
    vulnerability: str
    package: str
    installed_version: str
    expires: date
    reason: str
    reviewed_by: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan running Compose images with pinned Trivy, correlate CISA KEV, "
            "and publish node-exporter textfile metrics."
        )
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--all",
        action="store_true",
        help="scan every running service in the Compose project",
    )
    scope.add_argument(
        "--services",
        metavar="LIST",
        help="scan a comma-separated service list (default: litellm-prod,litellm-dev)",
    )
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="exit nonzero when an unexcepted fixable Critical or KEV finding exists",
    )
    parser.add_argument(
        "--project",
        default=os.environ.get("COMPOSE_PROJECT_NAME") or "litellm-stack",
        help="Compose project label to inventory (default: litellm-stack)",
    )
    return parser.parse_args()


def parse_non_negative_int(name: str, default: int) -> int:
    raw = os.environ.get(name) or str(default)
    if not re.fullmatch(r"\d+", raw):
        fail(f"{name} must be a non-negative integer")
    return int(raw)


def private_root(name: str, default: Path) -> Path:
    return safe_output_root(os.environ.get(name) or str(default), name)


def metrics_root() -> Path:
    raw = os.environ.get("IMAGE_SECURITY_METRICS_DIR") or str(
        ROOT / "security-metrics"
    )
    path = Path(raw).expanduser()
    if path.is_symlink():
        fail("IMAGE_SECURITY_METRICS_DIR must not be a symbolic link")
    path.mkdir(mode=0o755, parents=True, exist_ok=True)
    path = path.resolve()
    if path == Path("/"):
        fail("IMAGE_SECURITY_METRICS_DIR must not be the filesystem root")
    os.chmod(path, 0o755)
    return path


@contextmanager
def exclusive_lock(state_root: Path) -> Iterator[None]:
    lock = state_root / "scan.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(lock, flags, 0o600)
    except OSError:
        fail(f"could not open the image-security scan lock: {lock}")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        fail(f"another image-security scan is already running: {lock}")
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    os.fsync(fd)
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def atomic_write(path: Path, content: str, mode: int) -> None:
    if path.is_symlink():
        fail(f"refusing to write through symbolic link: {path}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(temporary, flags, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()


def empty_state() -> dict[str, object]:
    return {
        "version": 1,
        "last_run": {"timestamp": 0, "success": False},
        "feeds": {
            "trivy_db_timestamp": 0,
            "kev_feed_timestamp": 0,
            "trivy_version": TRIVY_VERSION,
        },
        "services": {},
        "exception_earliest_expiry": 0,
    }


def load_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return empty_state()
    if not path.is_file() or path.is_symlink():
        fail(f"image-security state is missing or unsafe: {path}")
    if path.stat().st_size > MAX_STATE_BYTES:
        fail("image-security state exceeds the allowed size")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        fail("image-security state is unreadable or invalid")
    if not isinstance(payload, dict) or payload.get("version") != 1:
        fail("unsupported image-security state version")
    if not isinstance(payload.get("services"), dict):
        fail("invalid image-security service state")
    return payload


def save_state(path: Path, state: dict[str, object]) -> None:
    atomic_write(
        path,
        json.dumps(state, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        0o600,
    )


def selected_services(arguments: argparse.Namespace) -> tuple[str, ...] | None:
    if arguments.all:
        return None
    values = (
        arguments.services.split(",")
        if arguments.services is not None
        else list(DEFAULT_SERVICES)
    )
    services: list[str] = []
    for raw in values:
        service = raw.strip()
        if not SERVICE_RE.fullmatch(service):
            fail(f"invalid service name: {raw}")
        if service not in services:
            services.append(service)
    if not services:
        fail("--services requires at least one service")
    return tuple(services)


def normalize_architecture(value: str) -> str:
    return {
        "aarch64": "arm64",
        "arm64/v8": "arm64",
        "x86_64": "amd64",
        "x86-64": "amd64",
    }.get(value, value)


def inspect_platform(local_image_id: str, timeout: int) -> str:
    output = capture(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Os}}/{{.Architecture}}",
            local_image_id,
        ],
        timeout=timeout,
    ).strip()
    os_name, separator, architecture = output.partition("/")
    if separator != "/" or not os_name or not architecture:
        fail("docker returned an invalid image platform")
    return f"{os_name}/{normalize_architecture(architecture)}"


def inventory_running_images(project: str, timeout: int) -> dict[str, RunningImage]:
    ids = capture(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.ID}}",
        ],
        timeout=timeout,
    ).split()
    if not ids:
        return {}
    try:
        containers = json.loads(
            capture(["docker", "inspect", *ids], timeout=timeout)
        )
    except json.JSONDecodeError:
        fail("docker returned invalid container metadata")
    if not isinstance(containers, list):
        fail("docker returned invalid container inventory")
    inventory: dict[str, RunningImage] = {}
    for container in containers:
        if not isinstance(container, dict):
            fail("docker returned invalid container inventory entry")
        config = container.get("Config")
        state = container.get("State")
        if not isinstance(config, dict) or not isinstance(state, dict):
            fail("docker container metadata is incomplete")
        if state.get("Running") is not True:
            continue
        labels = config.get("Labels")
        if not isinstance(labels, dict):
            continue
        service = labels.get("com.docker.compose.service")
        reference = config.get("Image")
        local_image_id = container.get("Image")
        if not all(
            isinstance(item, str) and item
            for item in (service, reference, local_image_id)
        ):
            continue
        assert isinstance(service, str)
        assert isinstance(reference, str)
        assert isinstance(local_image_id, str)
        candidate = RunningImage(
            service=service,
            configured_reference=reference,
            local_image_id=local_image_id,
            platform=inspect_platform(local_image_id, timeout),
        )
        previous = inventory.get(service)
        if previous is not None and previous != candidate:
            fail(f"service '{service}' has running replicas with different images")
        inventory[service] = candidate
    return inventory


def repository_without_tag(reference: str) -> str:
    repository = reference.partition("@")[0]
    final_component = repository.rsplit("/", 1)[-1]
    if ":" in final_component:
        repository = repository.rsplit(":", 1)[0]
    return repository


def platform_reference(image: RunningImage, timeout: int) -> str:
    result = run(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            "--raw",
            image.configured_reference,
        ],
        capture=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode:
        fail(f"could not inspect registry manifest for service '{image.service}'")
    assert isinstance(result.stdout, str)
    try:
        manifest = json.loads(result.stdout)
    except json.JSONDecodeError:
        fail(f"registry returned invalid manifest data for service '{image.service}'")
    if not isinstance(manifest, dict):
        fail(f"registry returned invalid manifest data for service '{image.service}'")
    manifests = manifest.get("manifests")
    selected_digest = image.configured_reference.rpartition("@")[2]
    if isinstance(manifests, list):
        os_name, architecture = image.platform.split("/", 1)
        selected_digest = ""
        for candidate in manifests:
            if not isinstance(candidate, dict):
                continue
            platform = candidate.get("platform")
            digest = candidate.get("digest")
            if (
                isinstance(platform, dict)
                and platform.get("os") == os_name
                and normalize_architecture(str(platform.get("architecture")))
                == architecture
                and isinstance(digest, str)
                and IMAGE_DIGEST_RE.fullmatch(digest)
            ):
                selected_digest = digest
                break
        if not selected_digest:
            fail(
                f"no {image.platform} manifest exists for service '{image.service}'"
            )
    if not IMAGE_DIGEST_RE.fullmatch(selected_digest):
        fail(f"invalid platform digest for service '{image.service}'")
    return f"{repository_without_tag(image.configured_reference)}@{selected_digest}"


def trivy_command(cache: Path, arguments: list[str]) -> list[str]:
    local_trivy = os.environ.get("IMAGE_SECURITY_TRIVY_EXECUTABLE")
    if local_trivy:
        executable = Path(local_trivy)
        if not executable.is_absolute():
            fail("IMAGE_SECURITY_TRIVY_EXECUTABLE must be an absolute path")
        executable = executable.resolve()
        if not executable.is_file() or not os.access(executable, os.X_OK):
            fail(
                "IMAGE_SECURITY_TRIVY_EXECUTABLE is missing or not executable: "
                f"{executable}"
            )
        return [str(executable), "--cache-dir", str(cache), *arguments]
    user = (
        ["--user", f"{os.getuid()}:{os.getgid()}"]
        if hasattr(os, "getuid") and hasattr(os, "getgid")
        else []
    )
    return [
        "docker",
        "run",
        "--rm",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "256",
        "--memory",
        "2g",
        "--tmpfs",
        "/tmp:mode=1777",
        *user,
        "--volume",
        f"{cache}:/cache",
        TRIVY_IMAGE,
        "--cache-dir",
        "/cache",
        *arguments,
    ]


def parse_rfc3339_timestamp(value: object, label: str) -> int:
    if not isinstance(value, str) or not value:
        fail(f"Trivy did not report {label}")
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        fail(f"Trivy reported an invalid {label}")


def update_trivy_database(cache: Path, timeout: int) -> tuple[int, str]:
    result = run(
        trivy_command(
            cache,
            ["image", "--download-db-only", "--no-progress"],
        ),
        capture=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode:
        fail("Trivy vulnerability database update failed")
    version_result = run(
        trivy_command(cache, ["version", "--format", "json"]),
        capture=True,
        check=False,
        timeout=timeout,
    )
    if version_result.returncode:
        fail("Trivy version/database metadata check failed")
    assert isinstance(version_result.stdout, str)
    try:
        metadata = json.loads(version_result.stdout)
        version = metadata["Version"]
        updated = metadata["VulnerabilityDB"]["UpdatedAt"]
    except (KeyError, TypeError, json.JSONDecodeError):
        fail("Trivy returned invalid version/database metadata")
    if version != TRIVY_VERSION:
        fail(f"expected Trivy {TRIVY_VERSION}, got {version}")
    return parse_rfc3339_timestamp(updated, "database update time"), version


def fetch_kev(timeout: int) -> tuple[set[str], bytes, int]:
    request = urllib.request.Request(
        KEV_URL,
        headers={"User-Agent": "litellm-stack-image-security-monitor/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                fail(f"CISA KEV feed returned HTTP {response.status}")
            body = response.read(MAX_KEV_BYTES + 1)
    except urllib.error.HTTPError as error:
        fail(f"CISA KEV feed returned HTTP {error.code}")
    except (urllib.error.URLError, OSError):
        fail("CISA KEV feed request failed")
    if len(body) > MAX_KEV_BYTES:
        fail("CISA KEV feed exceeds the allowed size")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("CISA KEV feed is not valid JSON")
    if not isinstance(payload, dict) or not isinstance(
        payload.get("vulnerabilities"), list
    ):
        fail("CISA KEV feed has an unsupported schema")
    identifiers: set[str] = set()
    for vulnerability in payload["vulnerabilities"]:
        if not isinstance(vulnerability, dict):
            fail("CISA KEV feed contains an invalid vulnerability")
        identifier = vulnerability.get("cveID")
        if not isinstance(identifier, str) or not CVE_RE.fullmatch(identifier):
            fail("CISA KEV feed contains an invalid CVE identifier")
        identifiers.add(identifier.upper())
    if not identifiers:
        fail("CISA KEV feed is empty")
    return identifiers, body, int(time.time())


def load_exceptions(path: Path) -> list[ExceptionRule]:
    if not path.is_file() or path.is_symlink():
        fail(f"security exception policy is missing or unsafe: {path}")
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        fail("security exception policy is invalid")
    if payload.get("version") != 1:
        fail("unsupported security exception policy version")
    raw_rules = payload.get("exceptions", [])
    if not isinstance(raw_rules, list):
        fail("security exceptions must be a list")
    rules: list[ExceptionRule] = []
    allowed = {
        "service",
        "vulnerability",
        "package",
        "installed_version",
        "expires",
        "reason",
        "reviewed_by",
    }
    for raw in raw_rules:
        if not isinstance(raw, dict) or set(raw) != allowed:
            fail("security exception has unsupported or missing fields")
        if not all(isinstance(raw[key], str) and raw[key] for key in allowed):
            fail("security exception fields must be non-empty strings")
        try:
            expiry = date.fromisoformat(raw["expires"])
        except ValueError:
            fail("security exception expiry must use YYYY-MM-DD")
        rules.append(
            ExceptionRule(
                service=raw["service"],
                vulnerability=raw["vulnerability"].upper(),
                package=raw["package"],
                installed_version=raw["installed_version"],
                expires=expiry,
                reason=raw["reason"],
                reviewed_by=raw["reviewed_by"],
            )
        )
    return rules


def vulnerability_aliases(vulnerability: dict[str, object]) -> set[str]:
    aliases: set[str] = set()
    identifier = vulnerability.get("VulnerabilityID")
    if isinstance(identifier, str) and identifier:
        aliases.add(identifier.upper())
    explicit_aliases = vulnerability.get("Aliases")
    if isinstance(explicit_aliases, list):
        aliases.update(
            alias.upper()
            for alias in explicit_aliases
            if isinstance(alias, str) and alias
        )
    return aliases


def exception_matches(
    rule: ExceptionRule,
    service: str,
    vulnerability: dict[str, object],
    today: date,
) -> bool:
    if rule.expires < today or rule.service not in {"*", service}:
        return False
    if rule.vulnerability not in vulnerability_aliases(vulnerability):
        return False
    package = vulnerability.get("PkgName")
    installed = vulnerability.get("InstalledVersion")
    if not isinstance(package, str) or not isinstance(installed, str):
        return False
    return (
        rule.package in {"*", package}
        and rule.installed_version in {"*", installed}
    )


def summarize_scan(
    service: str,
    payload: object,
    kev_identifiers: set[str],
    exceptions: list[ExceptionRule],
    today: date,
) -> tuple[dict[str, int], int, int, int, int, int]:
    if not isinstance(payload, dict) or not isinstance(payload.get("Results"), list):
        fail(f"Trivy returned invalid scan JSON for service '{service}'")
    counts = {
        f"{severity}|{fixed}": 0
        for severity in SEVERITIES
        for fixed in ("false", "true")
    }
    kev_count = 0
    unexcepted_kev_count = 0
    unexcepted_fixable_critical_count = 0
    policy_violations = 0
    finding_count = 0
    for result in payload["Results"]:
        if not isinstance(result, dict):
            fail(f"Trivy returned invalid result data for service '{service}'")
        vulnerabilities = result.get("Vulnerabilities") or []
        if not isinstance(vulnerabilities, list):
            fail(f"Trivy returned invalid vulnerability data for service '{service}'")
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict):
                fail(
                    f"Trivy returned invalid vulnerability entry for service '{service}'"
                )
            severity = str(vulnerability.get("Severity") or "UNKNOWN").upper()
            if severity not in SEVERITIES:
                severity = "UNKNOWN"
            fixed = bool(vulnerability.get("FixedVersion"))
            counts[f"{severity}|{str(fixed).lower()}"] += 1
            finding_count += 1
            aliases = vulnerability_aliases(vulnerability)
            is_kev = bool(aliases & kev_identifiers)
            excepted = any(
                exception_matches(rule, service, vulnerability, today)
                for rule in exceptions
            )
            if is_kev:
                kev_count += 1
                if not excepted:
                    unexcepted_kev_count += 1
            if severity == "CRITICAL" and fixed and not excepted:
                unexcepted_fixable_critical_count += 1
            if not excepted and (is_kev or (severity == "CRITICAL" and fixed)):
                policy_violations += 1
    return (
        counts,
        kev_count,
        unexcepted_kev_count,
        unexcepted_fixable_critical_count,
        policy_violations,
        finding_count,
    )


def scan_image(
    cache: Path,
    service: str,
    reference: str,
    timeout: int,
) -> tuple[object, str]:
    result = run(
        trivy_command(
            cache,
            [
                "image",
                "--image-src",
                "remote",
                "--scanners",
                "vuln",
                "--skip-db-update",
                "--no-progress",
                "--quiet",
                "--format",
                "json",
                reference,
            ],
        ),
        capture=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode:
        assert isinstance(result.stderr, str)
        detail = " ".join(result.stderr.strip().splitlines()[-1:])
        fail(
            f"Trivy scan failed for service '{service}'"
            + (f": {detail}" if detail else "")
        )
    assert isinstance(result.stdout, str)
    try:
        return json.loads(result.stdout), result.stdout
    except json.JSONDecodeError:
        fail(f"Trivy returned invalid JSON for service '{service}'")


def report_filename(service: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", service) + ".trivy.json"


def retain_reports(report_root: Path, retention_days: int) -> None:
    cutoff = time.time() - retention_days * 86400
    for candidate in report_root.iterdir():
        if (
            not candidate.is_dir()
            or candidate.is_symlink()
            or not re.fullmatch(r"image-security-\d{8}T\d{6}Z", candidate.name)
            or candidate.stat().st_mtime >= cutoff
        ):
            continue
        safe = all(
            entry.is_file()
            and not entry.is_symlink()
            and re.fullmatch(
                r"(?:[A-Za-z0-9_.-]+\.trivy\.json|kev\.json|summary\.json)",
                entry.name,
            )
            for entry in candidate.iterdir()
        )
        if not safe:
            print(
                f"WARNING: skipped security report retention for {candidate}",
                file=sys.stderr,
            )
            continue
        for entry in candidate.iterdir():
            entry.unlink()
        candidate.rmdir()


def prom_label(value: object) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )


def metric(name: str, labels: dict[str, object], value: int) -> str:
    rendered = ",".join(
        f'{key}="{prom_label(label)}"' for key, label in sorted(labels.items())
    )
    return f"{name}{{{rendered}}} {value}" if rendered else f"{name} {value}"


def render_metrics(state: dict[str, object]) -> str:
    last_run = state.get("last_run")
    feeds = state.get("feeds")
    services = state.get("services")
    if not all(isinstance(item, dict) for item in (last_run, feeds, services)):
        fail("cannot render invalid image-security state")
    assert isinstance(last_run, dict)
    assert isinstance(feeds, dict)
    assert isinstance(services, dict)
    lines = [
        "# HELP image_security_scan_run_timestamp_seconds Unix time of the latest scan attempt.",
        "# TYPE image_security_scan_run_timestamp_seconds gauge",
        metric(
            "image_security_scan_run_timestamp_seconds",
            {},
            int(last_run.get("timestamp") or 0),
        ),
        "# HELP image_security_scan_run_success Whether the latest complete scan run succeeded.",
        "# TYPE image_security_scan_run_success gauge",
        metric(
            "image_security_scan_run_success",
            {},
            int(bool(last_run.get("success"))),
        ),
        "# HELP image_security_trivy_db_timestamp_seconds Trivy vulnerability database update time.",
        "# TYPE image_security_trivy_db_timestamp_seconds gauge",
        metric(
            "image_security_trivy_db_timestamp_seconds",
            {},
            int(feeds.get("trivy_db_timestamp") or 0),
        ),
        "# HELP image_security_kev_feed_timestamp_seconds Latest successful CISA KEV fetch time.",
        "# TYPE image_security_kev_feed_timestamp_seconds gauge",
        metric(
            "image_security_kev_feed_timestamp_seconds",
            {},
            int(feeds.get("kev_feed_timestamp") or 0),
        ),
        "# HELP image_security_scanner_info Pinned image scanner version.",
        "# TYPE image_security_scanner_info gauge",
        metric(
            "image_security_scanner_info",
            {"version": feeds.get("trivy_version") or "unknown"},
            1,
        ),
        "# HELP image_security_exception_earliest_expiry_timestamp_seconds Earliest active exception expiry.",
        "# TYPE image_security_exception_earliest_expiry_timestamp_seconds gauge",
        metric(
            "image_security_exception_earliest_expiry_timestamp_seconds",
            {},
            int(state.get("exception_earliest_expiry") or 0),
        ),
        "# HELP image_security_scan_success Whether the latest service scan succeeded.",
        "# TYPE image_security_scan_success gauge",
        "# HELP image_security_scan_last_success_timestamp_seconds Unix time of the latest successful service scan.",
        "# TYPE image_security_scan_last_success_timestamp_seconds gauge",
        "# HELP image_security_vulnerabilities Detected package vulnerability findings.",
        "# TYPE image_security_vulnerabilities gauge",
        "# HELP image_security_kev Detected findings in the CISA KEV catalog.",
        "# TYPE image_security_kev gauge",
        "# HELP image_security_unexcepted_kev Unexcepted findings in the CISA KEV catalog.",
        "# TYPE image_security_unexcepted_kev gauge",
        "# HELP image_security_unexcepted_fixable_critical Unexcepted fixable Critical findings.",
        "# TYPE image_security_unexcepted_fixable_critical gauge",
        "# HELP image_security_policy_violations Unexcepted KEV or fixable Critical findings.",
        "# TYPE image_security_policy_violations gauge",
        "# HELP image_security_images_unknown Running images not covered by remote scanning.",
        "# TYPE image_security_images_unknown gauge",
        "# HELP image_security_service_info Current scanned service artifact.",
        "# TYPE image_security_service_info gauge",
    ]
    for service in sorted(services):
        current = services[service]
        if not isinstance(current, dict):
            fail("cannot render invalid service security state")
        status = str(current.get("status") or "scan-failed")
        if status == "not-applicable":
            lines.append(
                metric(
                    "image_security_images_unknown",
                    {
                        "service": service,
                        "reason": current.get("reason") or "unknown",
                    },
                    1,
                )
            )
            continue
        labels = {"service": service}
        lines.append(
            metric(
                "image_security_scan_success",
                labels,
                int(bool(current.get("scan_success"))),
            )
        )
        lines.append(
            metric(
                "image_security_scan_last_success_timestamp_seconds",
                labels,
                int(current.get("last_success_timestamp") or 0),
            )
        )
        counts = current.get("counts")
        if not isinstance(counts, dict):
            counts = {}
        for severity in SEVERITIES:
            for fixed in ("false", "true"):
                lines.append(
                    metric(
                        "image_security_vulnerabilities",
                        {
                            "service": service,
                            "severity": severity,
                            "fix_available": fixed,
                        },
                        int(counts.get(f"{severity}|{fixed}") or 0),
                    )
                )
        lines.append(
            metric(
                "image_security_kev",
                labels,
                int(current.get("kev_count") or 0),
            )
        )
        lines.append(
            metric(
                "image_security_unexcepted_kev",
                labels,
                int(current.get("unexcepted_kev_count") or 0),
            )
        )
        lines.append(
            metric(
                "image_security_unexcepted_fixable_critical",
                labels,
                int(
                    current.get("unexcepted_fixable_critical_count")
                    or 0
                ),
            )
        )
        lines.append(
            metric(
                "image_security_policy_violations",
                labels,
                int(current.get("policy_violations") or 0),
            )
        )
        lines.append(
            metric(
                "image_security_service_info",
                {
                    "service": service,
                    "architecture": current.get("platform") or "unknown",
                    "image_digest": current.get("scan_reference") or "unknown",
                },
                1,
            )
        )
    return "\n".join(lines) + "\n"


def active_exception_expiry(exceptions: list[ExceptionRule], today: date) -> int:
    active = [rule.expires for rule in exceptions if rule.expires >= today]
    if not active:
        return 0
    earliest = min(active)
    return int(
        datetime.combine(earliest, datetime.min.time(), timezone.utc).timestamp()
    )


def scan_succeeded(
    operational_success: bool,
    enforce: bool,
    policy_failed: bool,
    coverage_failed: bool,
) -> bool:
    return operational_success and not (
        enforce and (policy_failed or coverage_failed)
    )


def initial_service_state(
    previous: object, *, full_scope: bool
) -> dict[str, object]:
    if full_scope or not isinstance(previous, dict):
        return {}
    return {
        service: dict(current)
        for service, current in previous.items()
        if isinstance(service, str) and isinstance(current, dict)
    }


def update_failure_state(
    state: dict[str, object], timestamp: int, service: str, message: str
) -> None:
    services = state.setdefault("services", {})
    assert isinstance(services, dict)
    previous = services.get(service)
    current = dict(previous) if isinstance(previous, dict) else {}
    current.update(
        {
            "status": "scan-failed",
            "scan_success": False,
            "error": message[:500],
            "last_attempt_timestamp": timestamp,
        }
    )
    current.setdefault("last_success_timestamp", 0)
    current.setdefault("counts", {})
    current.setdefault("kev_count", 0)
    current.setdefault("policy_violations", 0)
    services[service] = current


def invalidate_retained_services(
    state: dict[str, object], timestamp: int, message: str
) -> None:
    services = state.get("services")
    if not isinstance(services, dict):
        return
    for service, current in list(services.items()):
        if (
            isinstance(current, dict)
            and current.get("status") != "not-applicable"
        ):
            update_failure_state(state, timestamp, service, message)


def run_scan(arguments: argparse.Namespace) -> tuple[dict[str, object], bool]:
    requested = selected_services(arguments)
    require_docker(compose_plugin=False)
    timeout = parse_non_negative_int("IMAGE_SECURITY_COMMAND_TIMEOUT_SECONDS", 900)
    if timeout == 0:
        fail("IMAGE_SECURITY_COMMAND_TIMEOUT_SECONDS must be greater than zero")
    state_root = private_root(
        "IMAGE_SECURITY_STATE_DIR", ROOT / "security-state"
    )
    report_root = private_root(
        "IMAGE_SECURITY_REPORT_DIR", ROOT / "security-reports"
    )
    output_root = metrics_root()
    state_path = state_root / "state.json"
    policy_path = Path(
        os.environ.get("IMAGE_SECURITY_EXCEPTIONS_FILE")
        or ROOT / "security" / "exceptions.toml"
    ).expanduser()
    if not policy_path.is_absolute():
        policy_path = ROOT / policy_path
    policy_path = policy_path.resolve()
    with exclusive_lock(state_root):
        state = load_state(state_path)
        timestamp = int(time.time())
        state["last_run"] = {"timestamp": timestamp, "success": False}
        operational_success = False
        policy_failed = False
        coverage_failed = False
        try:
            exceptions = load_exceptions(policy_path)
            today = datetime.now(timezone.utc).date()
            state["exception_earliest_expiry"] = active_exception_expiry(
                exceptions, today
            )
            cache = state_root / "trivy-cache"
            cache.mkdir(mode=0o700, exist_ok=True)
            if cache.is_symlink():
                fail("Trivy cache must not be a symbolic link")
            os.chmod(cache, 0o700)
            db_timestamp, version = update_trivy_database(cache, timeout)
            kev_identifiers, kev_body, kev_timestamp = fetch_kev(
                min(timeout, 60)
            )
            feeds = state.setdefault("feeds", {})
            assert isinstance(feeds, dict)
            feeds.update(
                {
                    "trivy_db_timestamp": db_timestamp,
                    "kev_feed_timestamp": kev_timestamp,
                    "trivy_version": version,
                }
            )
            inventory = inventory_running_images(arguments.project, timeout)
            services = sorted(inventory) if requested is None else list(requested)
            if not services:
                fail(
                    f"no running services found for Compose project '{arguments.project}'"
                )
            previous_services = state.get("services")
            current_services = initial_service_state(
                previous_services,
                full_scope=requested is None,
            )
            if not inventory:
                fail(
                    f"no running containers found for Compose project '{arguments.project}'"
                )
            report = report_root / f"image-security-{utc_timestamp()}"
            report.mkdir(mode=0o700)
            atomic_write(report / "kev.json", kev_body.decode("utf-8"), 0o600)
            failures: list[str] = []
            for service in services:
                image = inventory.get(service)
                if image is None:
                    current_services[service] = {
                        "status": "not-applicable",
                        "reason": "not-running",
                    }
                    coverage_failed = True
                    continue
                if not IMAGE_REF_RE.fullmatch(image.configured_reference):
                    current_services[service] = {
                        "status": "not-applicable",
                        "reason": "not-immutable",
                        "platform": image.platform,
                    }
                    coverage_failed = True
                    continue
                state["services"] = current_services
                try:
                    scan_reference = platform_reference(image, timeout)
                    payload, raw_json = scan_image(
                        cache, service, scan_reference, timeout
                    )
                    (
                        counts,
                        kev_count,
                        unexcepted_kev_count,
                        unexcepted_fixable_critical_count,
                        violations,
                        finding_count,
                    ) = summarize_scan(
                        service,
                        payload,
                        kev_identifiers,
                        exceptions,
                        today,
                    )
                    atomic_write(
                        report / report_filename(service),
                        raw_json.rstrip() + "\n",
                        0o600,
                    )
                    current_services[service] = {
                        "status": "scanned-ok",
                        "scan_success": True,
                        "last_attempt_timestamp": timestamp,
                        "last_success_timestamp": timestamp,
                        "configured_reference": image.configured_reference,
                        "scan_reference": scan_reference,
                        "platform": image.platform,
                        "counts": counts,
                        "kev_count": kev_count,
                        "unexcepted_kev_count": unexcepted_kev_count,
                        "unexcepted_fixable_critical_count": (
                            unexcepted_fixable_critical_count
                        ),
                        "policy_violations": violations,
                        "finding_count": finding_count,
                    }
                    policy_failed = policy_failed or violations > 0
                    print(
                        f"PASS: {service} scanned ({finding_count} findings, "
                        f"{violations} policy violations)"
                    )
                except ScriptError as error:
                    state["services"] = current_services
                    update_failure_state(
                        state, timestamp, service, str(error)
                    )
                    current_services = state["services"]
                    assert isinstance(current_services, dict)
                    failures.append(service)
                    print(f"FAIL: {error}", file=sys.stderr)
            state["services"] = current_services
            operational_success = not failures
            state["last_run"] = {
                "timestamp": timestamp,
                "success": operational_success,
            }
            summary = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "project": arguments.project,
                "services": {
                    service: {
                        key: value
                        for key, value in current.items()
                        if key not in {"error"}
                    }
                    for service, current in current_services.items()
                    if isinstance(current, dict)
                },
                "operational_success": operational_success,
                "policy_failed": policy_failed,
                "coverage_failed": coverage_failed,
                "enforced": arguments.enforce,
            }
            atomic_write(
                report / "summary.json",
                json.dumps(summary, indent=2, sort_keys=True) + "\n",
                0o600,
            )
            retain_reports(
                report_root,
                parse_non_negative_int("IMAGE_SECURITY_RETENTION_DAYS", 30),
            )
        except ScriptError as error:
            print(f"FAIL: {error}", file=sys.stderr)
            state["last_run"] = {"timestamp": timestamp, "success": False}
            invalidate_retained_services(state, timestamp, str(error))
        save_state(state_path, state)
        atomic_write(
            output_root / "image-security.prom",
            render_metrics(state),
            0o644,
        )
        return state, scan_succeeded(
            operational_success,
            arguments.enforce,
            policy_failed,
            coverage_failed,
        )


def main() -> None:
    arguments = parse_args()
    state, success = run_scan(arguments)
    services = state.get("services")
    scanned = sum(
        1
        for current in services.values()
        if isinstance(current, dict) and current.get("status") == "scanned-ok"
    ) if isinstance(services, dict) else 0
    unknown = sum(
        1
        for current in services.values()
        if isinstance(current, dict) and current.get("status") == "not-applicable"
    ) if isinstance(services, dict) else 0
    print(f"Image security state published: {scanned} covered, {unknown} unknown")
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    run_main(main)
