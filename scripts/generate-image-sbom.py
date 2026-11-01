#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Generate CycloneDX SBOMs and licence evidence for running Compose images."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

from maintenance_common import (
    IMAGE_REF_RE,
    ROOT,
    ScriptError,
    capture,
    command_exists,
    fail,
    require_docker,
    run,
    run_main,
    utc_timestamp,
)


def load_monitor() -> ModuleType:
    name = "monitor_image_vulnerabilities"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().with_name("monitor-image-vulnerabilities.py")
    )
    if spec is None or spec.loader is None:
        fail("could not load the image-security monitor helpers")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


monitor = load_monitor()

STACK_SPEC_VERSION = "1.6"
IMAGE_KEY_RE = re.compile(r"^(?:registry|local)-sha256-[0-9a-f]{64}$")
RUN_FILE_RE = re.compile(r"^stack-\d{8}T\d{6}Z\.cdx\.json$")
IMAGE_FILE_RE = re.compile(
    r"^((?:registry|local)-sha256-[0-9a-f]{64})\.(?:cdx|licences|meta)\.json$"
)
INPUT_FILE_RE = re.compile(r"^(?:local)-sha256-[0-9a-f]{64}\.tar$")
SERIAL_RE = re.compile(
    r"^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
REVIEW_CATEGORIES = ("forbidden", "restricted", "reciprocal", "unknown")
MAX_REVIEW_NAMES = 50
PROPERTY_PREFIX = "litellm-stack:"


@dataclass
class ImageEntry:
    key: str
    source: str
    digest: str
    configured_reference: str
    scan_reference: str
    local_image_id: str
    platform: str
    services: list[str] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate CycloneDX SBOMs and licence evidence for the image digests "
            "actually running in the Compose project, using the pinned Trivy."
        )
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--all",
        action="store_true",
        help="cover every running service in the Compose project (default)",
    )
    scope.add_argument(
        "--services",
        metavar="LIST",
        help="cover only a comma-separated service list",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="regenerate SBOMs even when evidence for a digest already exists",
    )
    parser.add_argument(
        "--project",
        default=os.environ.get("COMPOSE_PROJECT_NAME") or "litellm-stack",
        help="Compose project label to inventory (default: litellm-stack)",
    )
    return parser.parse_args()


def environment_boolean(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    fail(f"{name} must be true or false")


def private_directory(path: Path) -> Path:
    if path.is_symlink():
        fail(f"SBOM directory must not be a symbolic link: {path}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        fail(f"SBOM path is not a directory: {path}")
    os.chmod(path, 0o700)
    return path


def image_key(source: str, digest: str) -> str:
    if source not in {"registry", "local"} or not monitor.IMAGE_DIGEST_RE.fullmatch(
        digest
    ):
        fail(f"invalid image identity: {source} {digest}")
    return f"{source}-{digest.replace(':', '-')}"


def split_reference(reference: str) -> tuple[str, str]:
    repository = monitor.repository_without_tag(reference)
    tagged = reference.partition("@")[0]
    final_component = tagged.rsplit("/", 1)[-1]
    tag = final_component.rsplit(":", 1)[1] if ":" in final_component else ""
    return repository, tag


def trivy_visible_path(cache: Path, path: Path) -> str:
    """Return the path at which the configured Trivy process can read a file."""
    if os.environ.get("IMAGE_SECURITY_TRIVY_EXECUTABLE"):
        return str(path)
    try:
        relative = path.relative_to(cache)
    except ValueError:
        fail(f"Trivy input must live below the Trivy cache: {path}")
    return f"/cache/{relative.as_posix()}"


def run_trivy(cache: Path, arguments: list[str], timeout: int, label: str) -> str:
    result = run(
        monitor.trivy_command(cache, arguments),
        capture=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode:
        assert isinstance(result.stderr, str)
        detail = " ".join(result.stderr.strip().splitlines()[-1:])
        fail(f"Trivy failed for {label}" + (f": {detail}" if detail else ""))
    assert isinstance(result.stdout, str)
    return result.stdout


def trivy_version(cache: Path, timeout: int) -> str:
    raw = run_trivy(cache, ["version", "--format", "json"], timeout, "version")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        fail("Trivy returned invalid version JSON")
    version = payload.get("Version") if isinstance(payload, dict) else None
    if not isinstance(version, str) or not version:
        fail("Trivy did not report its version")
    return version


def validate_cyclonedx(raw: str, label: str) -> tuple[str, str]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        fail(f"Trivy returned invalid CycloneDX JSON for {label}")
    if not isinstance(payload, dict) or payload.get("bomFormat") != "CycloneDX":
        fail(f"Trivy output for {label} is not a CycloneDX document")
    serial = payload.get("serialNumber")
    version = payload.get("version")
    if not isinstance(serial, str) or not SERIAL_RE.fullmatch(serial):
        fail(f"CycloneDX document for {label} has no valid serial number")
    if not isinstance(version, int) or version < 1:
        fail(f"CycloneDX document for {label} has no valid version")
    spec_version = payload.get("specVersion")
    return (
        f"urn:cdx:{serial.removeprefix('urn:uuid:')}/{version}",
        spec_version if isinstance(spec_version, str) else "unknown",
    )


def summarize_licences(raw: str, label: str) -> dict[str, object]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        fail(f"Trivy returned invalid licence JSON for {label}")
    if not isinstance(payload, dict):
        fail(f"Trivy licence output for {label} is not an object")
    results = payload.get("Results") or []
    if not isinstance(results, list):
        fail(f"Trivy licence output for {label} has invalid results")
    categories: dict[str, int] = {}
    review: dict[str, set[str]] = {category: set() for category in REVIEW_CATEGORIES}
    for result in results:
        if not isinstance(result, dict):
            continue
        licences = result.get("Licenses") or []
        if not isinstance(licences, list):
            continue
        for licence in licences:
            if not isinstance(licence, dict):
                continue
            category = str(licence.get("Category") or "unknown").lower()
            categories[category] = categories.get(category, 0) + 1
            name = str(licence.get("Name") or "").strip()
            if category in review and name:
                review[category].add(name[:120])
    return {
        "categories": dict(sorted(categories.items())),
        "review": {
            category: sorted(names)[:MAX_REVIEW_NAMES]
            for category, names in review.items()
            if names
        },
    }


def image_labels(local_image_id: str, timeout: int) -> dict[str, str]:
    raw = capture(
        ["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", local_image_id],
        timeout=timeout,
    ).strip()
    try:
        labels = json.loads(raw) if raw and raw != "null" else {}
    except json.JSONDecodeError:
        fail("docker returned invalid image labels")
    if not isinstance(labels, dict):
        return {}
    return {
        str(key): str(value)[:500]
        for key, value in sorted(labels.items())
        if str(key).startswith("org.opencontainers.image.")
    }


def trivy_target(
    entry: ImageEntry, cache: Path, input_root: Path, timeout: int
) -> tuple[list[str], Path | None]:
    if entry.source == "registry":
        return ["--image-src", "remote", entry.scan_reference], None
    archive = input_root / f"{entry.key}.tar"
    if archive.exists() or archive.is_symlink():
        archive.unlink()
    run(
        ["docker", "image", "save", "--output", archive, entry.local_image_id],
        timeout=timeout,
    )
    if not archive.is_file() or archive.is_symlink():
        fail(f"docker did not export local image for {entry.key}")
    os.chmod(archive, 0o600)
    return ["--input", trivy_visible_path(cache, archive)], archive


def generate_evidence(
    entry: ImageEntry,
    *,
    cache: Path,
    input_root: Path,
    image_root: Path,
    timeout: int,
    licence_scan: bool,
    refresh: bool,
    version: str,
) -> dict[str, object]:
    sbom_path = image_root / f"{entry.key}.cdx.json"
    licence_path = image_root / f"{entry.key}.licences.json"
    meta_path = image_root / f"{entry.key}.meta.json"
    previous = read_json(meta_path) if meta_path.is_file() else None
    need_sbom = refresh or not sbom_path.is_file() or not isinstance(previous, dict)
    need_licences = licence_scan and (refresh or not licence_path.is_file())
    if not need_sbom and not need_licences:
        assert isinstance(previous, dict)
        print(f"PASS: {entry.key} evidence already present")
        return previous

    label = ", ".join(entry.services)
    target, archive = trivy_target(entry, cache, input_root, timeout)
    try:
        options = ["image", "--no-progress", "--quiet"]
        meta: dict[str, object] = (
            dict(previous) if isinstance(previous, dict) and not need_sbom else {}
        )
        if need_sbom:
            raw_sbom = run_trivy(
                cache, [*options, "--format", "cyclonedx", *target], timeout, label
            )
            bom_link, spec_version = validate_cyclonedx(raw_sbom, label)
            monitor.atomic_write(sbom_path, raw_sbom.rstrip() + "\n", 0o600)
            meta.update(
                {
                    "key": entry.key,
                    "source": entry.source,
                    "digest": entry.digest,
                    "scan_reference": entry.scan_reference,
                    "platform": entry.platform,
                    "bom_link": bom_link,
                    "spec_version": spec_version,
                    "sbom_file": sbom_path.name,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "trivy_version": version,
                }
            )
        if need_licences:
            raw_licences = run_trivy(
                cache,
                [
                    *options,
                    "--scanners",
                    "license",
                    "--license-full",
                    "--format",
                    "json",
                    *target,
                ],
                timeout,
                label,
            )
            meta["licences"] = summarize_licences(raw_licences, label)
            meta["licence_file"] = licence_path.name
            monitor.atomic_write(licence_path, raw_licences.rstrip() + "\n", 0o600)
        monitor.atomic_write(
            meta_path, json.dumps(meta, indent=2, sort_keys=True) + "\n", 0o600
        )
        print(f"PASS: generated evidence for {entry.key} ({label})")
        return meta
    finally:
        if archive is not None and archive.exists() and not archive.is_symlink():
            archive.unlink()


def read_json(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def property_list(values: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"name": PROPERTY_PREFIX + name, "value": value} for name, value in values]


def repository_revision() -> str:
    configured = os.environ.get("STACK_REPOSITORY_REVISION", "").strip()
    if configured:
        return configured[:80]
    if (ROOT / ".git").exists() and command_exists("git"):
        result = run(
            ["git", "-C", ROOT, "rev-parse", "--short=12", "HEAD"],
            capture=True,
            check=False,
            timeout=30,
        )
        if result.returncode == 0 and isinstance(result.stdout, str):
            return result.stdout.strip()
    return "unknown"


def stack_component(
    entry: ImageEntry, meta: dict[str, object] | None, labels: dict[str, str], error: str
) -> dict[str, object]:
    name, tag = split_reference(entry.configured_reference)
    properties: list[tuple[str, str]] = [
        ("image-key", entry.key),
        ("source", entry.source),
        ("configured-reference", entry.configured_reference),
        ("platform", entry.platform),
        ("local-image-id", entry.local_image_id),
    ]
    properties.extend(("service", service) for service in sorted(entry.services))
    if entry.source == "registry":
        properties.append(("scan-reference", entry.scan_reference))
    properties.extend(("label:" + key, value) for key, value in labels.items())
    component: dict[str, object] = {
        "type": "container",
        "bom-ref": entry.key,
        "name": name,
        "version": tag or entry.digest,
        "hashes": [{"alg": "SHA-256", "content": entry.digest.partition(":")[2]}],
    }
    if meta is not None:
        component["externalReferences"] = [
            {
                "type": "bom",
                "url": str(meta.get("bom_link")),
                "comment": f"images/{meta.get('sbom_file')}",
            }
        ]
        licences = meta.get("licences")
        if isinstance(licences, dict):
            categories = licences.get("categories")
            if isinstance(categories, dict) and categories:
                properties.append(
                    (
                        "licence-categories",
                        ",".join(f"{key}={value}" for key, value in categories.items()),
                    )
                )
            review = licences.get("review")
            if isinstance(review, dict):
                for category, names in review.items():
                    if isinstance(names, list) and names:
                        properties.append(
                            (f"licence-review:{category}", "; ".join(map(str, names)))
                        )
        properties.append(("sbom-status", "ok"))
    else:
        properties.append(("sbom-status", "failed"))
        properties.append(("sbom-error", error[:300]))
    component["properties"] = property_list(properties)
    return component


def review_lines(components: list[dict[str, object]]) -> list[str]:
    """Summarize licence categories that need comparison with the register."""
    lines: list[str] = []
    for component in components:
        properties = {
            str(item.get("name")): str(item.get("value"))
            for item in component.get("properties", [])
            if isinstance(item, dict)
        }
        counts = dict(
            pair.split("=", 1)
            for pair in properties.get(PROPERTY_PREFIX + "licence-categories", "").split(",")
            if "=" in pair
        )
        flagged = [f"{category}={counts[category]}" for category in REVIEW_CATEGORIES if category in counts]
        if not flagged:
            continue
        key = properties.get(PROPERTY_PREFIX + "image-key", "")
        lines.append(
            f"REVIEW: {component['name']}: {', '.join(flagged)} "
            f"(details in sbom/images/{key}.licences.json)"
        )
        forbidden = properties.get(PROPERTY_PREFIX + "licence-review:forbidden")
        if forbidden:
            lines.append(f"REVIEW: {component['name']} forbidden-category licences: {forbidden}")
    return lines


def build_stack_bom(
    *,
    project: str,
    scope: str,
    version: str,
    components: list[dict[str, object]],
) -> dict[str, object]:
    references = [str(component["bom-ref"]) for component in components]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": STACK_SPEC_VERSION,
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "group": "litellm-stack",
                        "name": "generate-image-sbom",
                    },
                    {
                        "type": "application",
                        "group": "aquasecurity",
                        "name": "trivy",
                        "version": version,
                    },
                ]
            },
            "component": {
                "type": "application",
                "bom-ref": "litellm-stack",
                "name": project,
                "version": repository_revision(),
            },
            "properties": property_list([("scope", scope)]),
        },
        "components": components,
        "dependencies": [
            {"ref": "litellm-stack", "dependsOn": references},
            *({"ref": reference} for reference in references),
        ],
    }


def referenced_keys(run_file: Path) -> set[str] | None:
    payload = read_json(run_file)
    if not isinstance(payload, dict):
        return None
    keys: set[str] = set()
    components = payload.get("components")
    if not isinstance(components, list):
        return keys
    for component in components:
        reference = component.get("bom-ref") if isinstance(component, dict) else None
        if isinstance(reference, str) and IMAGE_KEY_RE.fullmatch(reference):
            keys.add(reference)
    return keys


def prune_evidence(
    run_root: Path, image_root: Path, retention_days: int, now: float
) -> None:
    """Delete expired stack runs and image evidence no retained run references."""
    if retention_days == 0:
        return
    runs = sorted(
        (path for path in run_root.iterdir() if RUN_FILE_RE.fullmatch(path.name)),
        key=lambda path: path.name,
    )
    cutoff = now - retention_days * 86400
    for path in runs[:-1]:
        if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
            path.unlink()
    retained: set[str] = set()
    for path in run_root.iterdir():
        if not RUN_FILE_RE.fullmatch(path.name):
            continue
        keys = referenced_keys(path)
        if keys is None:
            print(f"WARNING: kept all image evidence; unreadable run {path.name}", file=sys.stderr)
            return
        retained |= keys
    for path in image_root.iterdir():
        match = IMAGE_FILE_RE.fullmatch(path.name)
        if (
            match is None
            or match.group(1) in retained
            or path.is_symlink()
            or not path.is_file()
            or path.stat().st_mtime >= cutoff
        ):
            continue
        path.unlink()


def collect_entries(
    inventory: dict[str, object],
    services: list[str],
    timeout: int,
) -> tuple[dict[str, ImageEntry], list[str]]:
    entries: dict[str, ImageEntry] = {}
    failures: list[str] = []
    for service in services:
        image = inventory.get(service)
        if image is None:
            failures.append(f"service '{service}' is not running")
            continue
        try:
            if IMAGE_REF_RE.fullmatch(image.configured_reference):
                scan_reference = monitor.platform_reference(image, timeout)
                source, digest = "registry", scan_reference.rpartition("@")[2]
            else:
                scan_reference, source, digest = "", "local", image.local_image_id
            key = image_key(source, digest)
        except ScriptError as error:
            failures.append(str(error))
            continue
        entry = entries.setdefault(
            key,
            ImageEntry(
                key=key,
                source=source,
                digest=digest,
                configured_reference=image.configured_reference,
                scan_reference=scan_reference,
                local_image_id=image.local_image_id,
                platform=image.platform,
            ),
        )
        entry.services.append(service)
    return entries, failures


def run_sbom(arguments: argparse.Namespace) -> bool:
    requested = monitor.selected_services(arguments)
    require_docker(compose_plugin=False)
    timeout = monitor.parse_non_negative_int("IMAGE_SECURITY_COMMAND_TIMEOUT_SECONDS", 900)
    if timeout == 0:
        fail("IMAGE_SECURITY_COMMAND_TIMEOUT_SECONDS must be greater than zero")
    retention_days = monitor.parse_non_negative_int("IMAGE_SECURITY_SBOM_RETENTION_DAYS", 400)
    licence_scan = environment_boolean("IMAGE_SECURITY_SBOM_LICENCE_SCAN", True)
    state_root = monitor.private_root("IMAGE_SECURITY_STATE_DIR", ROOT / "security-state")
    report_root = monitor.private_root("IMAGE_SECURITY_REPORT_DIR", ROOT / "security-reports")
    sbom_root = private_directory(report_root / "sbom")
    image_root = private_directory(sbom_root / "images")
    run_root = private_directory(sbom_root / "runs")
    with monitor.exclusive_lock(state_root):
        cache = private_directory(state_root / "trivy-cache")
        input_root = private_directory(cache / "sbom-input")
        for leftover in input_root.iterdir():
            if INPUT_FILE_RE.fullmatch(leftover.name) and not leftover.is_symlink():
                leftover.unlink()
        version = trivy_version(cache, timeout)
        inventory = monitor.inventory_running_images(arguments.project, timeout)
        if not inventory:
            fail(f"no running containers found for Compose project '{arguments.project}'")
        services = sorted(inventory) if requested is None else list(requested)
        entries, failures = collect_entries(inventory, services, timeout)
        components: list[dict[str, object]] = []
        for key in sorted(entries):
            entry = entries[key]
            meta: dict[str, object] | None = None
            error = ""
            try:
                meta = generate_evidence(
                    entry,
                    cache=cache,
                    input_root=input_root,
                    image_root=image_root,
                    timeout=timeout,
                    licence_scan=licence_scan,
                    refresh=arguments.refresh,
                    version=version,
                )
                labels = image_labels(entry.local_image_id, timeout)
            except ScriptError as error_value:
                error = str(error_value)
                labels = {}
                failures.append(error)
                print(f"FAIL: {error}", file=sys.stderr)
            components.append(stack_component(entry, meta, labels, error))
        stack = build_stack_bom(
            project=arguments.project,
            scope="all-running" if requested is None else "selected-services",
            version=version,
            components=components,
        )
        run_file = run_root / f"stack-{utc_timestamp()}.cdx.json"
        while run_file.exists() or run_file.is_symlink():
            time.sleep(1)
            run_file = run_root / f"stack-{utc_timestamp()}.cdx.json"
        monitor.atomic_write(run_file, json.dumps(stack, indent=2) + "\n", 0o600)
        prune_evidence(run_root, image_root, retention_days, time.time())
        print(f"Stack SBOM written: {run_file} ({len(components)} images)")
        for line in review_lines(components):
            print(line)
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return not failures


def main() -> None:
    if not run_sbom(parse_args()):
        raise SystemExit(1)


if __name__ == "__main__":
    run_main(main)
