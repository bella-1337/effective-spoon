#!/usr/bin/env python3
"""Build, label-check, and vulnerability-scan the images this repository builds.

CI runs this on GitHub Actions and GitLab CI with the committed example
environment files, so no deployment secret is needed:

    python3 scripts/ci/images.py build
    python3 scripts/ci/images.py scan

`build` builds every Compose service that has a `build` section (all profiles)
and fails when an image label differs from the rendered Compose `build.labels`.
`scan` scans those images with the pinned Trivy release from the image-security
monitor and applies the same release-gate policy as
`maintenance.py security-scan --enforce`: an unexcepted CISA KEV match or
fixable Critical finding fails the job. Exceptions come from
`security/exceptions.toml` and must cover every service that runs the image.
With `scan --report-only`, the same findings are warnings instead; CI uses this
for pull requests and branch pushes so new upstream CVEs do not block unrelated
changes, while nightly, manual, and release scans enforce.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maintenance_common import (  # noqa: E402
    LOG,
    ROOT,
    capture,
    compose,
    fail,
    require_docker,
    run,
    run_main,
)
from maintenance_contracts import (  # noqa: E402
    compose_profile_arguments,
    load_compose_config,
)

ENV_FILE = ROOT / ".env.example"
OPTIONAL_ENV_FILE = ROOT / ".env.optional.example"
EXCEPTIONS_FILE = ROOT / "security" / "exceptions.toml"
DEFAULT_TRIVY_CACHE_VOLUME = "litellm-stack-ci-trivy-cache"
DEFAULT_DOCKER_SOCKET = "/var/run/docker.sock"
HTTPS_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9._~-]+)*$")
GITHUB_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")


def load_monitor() -> ModuleType:
    name = "monitor_image_vulnerabilities"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / "monitor-image-vulnerabilities.py"
    )
    if spec is None or spec.loader is None:
        fail("could not load the image-security monitor helpers")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


monitor = load_monitor()


@dataclass(frozen=True)
class LocalImage:
    reference: str
    build_service: str
    services: tuple[str, ...]
    labels: Mapping[str, str]


@dataclass(frozen=True)
class Violation:
    vulnerability: str
    package: str
    installed: str
    fixed: str
    severity: str
    kev: bool
    services: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build, label-check, and scan the images this repository builds."
    )
    parser.add_argument("command", choices=("build", "scan"))
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="scan: report policy findings as warnings without failing",
    )
    return parser.parse_args()


def set_repository_metadata() -> None:
    """Record the checked-out commit and CI project URL in the image labels."""
    if not os.environ.get("STACK_REPOSITORY_REVISION"):
        revision = (
            os.environ.get("GITHUB_SHA")
            or os.environ.get("CI_COMMIT_SHA")
            or capture(["git", "rev-parse", "HEAD"]).strip()
        )
        os.environ["STACK_REPOSITORY_REVISION"] = revision
    if not os.environ.get("STACK_REPOSITORY_URL"):
        server = os.environ.get("GITHUB_SERVER_URL", "")
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        if HTTPS_URL_RE.fullmatch(server) and GITHUB_REPOSITORY_RE.fullmatch(repository):
            url = f"{server}/{repository}"
        else:
            url = os.environ.get("CI_PROJECT_URL", "")
        # A malformed value (for example from a local CI emulator) is left
        # out rather than recorded as the image's source.
        if HTTPS_URL_RE.fullmatch(url):
            os.environ["STACK_REPOSITORY_URL"] = url


def local_images(config: Mapping[str, Any]) -> list[LocalImage]:
    services = config.get("services")
    if not isinstance(services, dict):
        fail("Docker Compose returned no services")
    builds: dict[str, tuple[str, dict[str, Any]]] = {}
    for name in sorted(services):
        service = services[name]
        build = service.get("build") if isinstance(service, dict) else None
        if not isinstance(build, dict):
            continue
        reference = service.get("image")
        if not isinstance(reference, str) or not reference:
            fail(f"locally built service '{name}' has no image tag")
        if reference in builds and builds[reference][1] != build:
            fail(
                f"services '{builds[reference][0]}' and '{name}' build {reference} "
                "with different settings"
            )
        builds.setdefault(reference, (name, build))
    if not builds:
        fail("Docker Compose defines no locally built images")
    images: list[LocalImage] = []
    for reference, (build_service, build) in sorted(builds.items()):
        labels = build.get("labels") or {}
        if not isinstance(labels, dict):
            fail(f"build labels of {reference} must be a mapping")
        consumers = tuple(
            sorted(
                name
                for name, service in services.items()
                if isinstance(service, dict) and service.get("image") == reference
            )
        )
        images.append(
            LocalImage(
                reference=reference,
                build_service=build_service,
                services=consumers,
                labels={str(key): str(value) for key, value in labels.items()},
            )
        )
    return images


def image_labels(reference: str) -> dict[str, str]:
    result = run(
        ["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", reference],
        capture=True,
        check=False,
    )
    if result.returncode:
        fail(f"image {reference} is not available locally; run the build step first")
    assert isinstance(result.stdout, str)
    labels = json.loads(result.stdout or "null") or {}
    if not isinstance(labels, dict):
        fail(f"image {reference} has unreadable labels")
    return {str(key): str(value) for key, value in labels.items()}


def label_mismatches(expected: Mapping[str, str], actual: Mapping[str, str]) -> list[str]:
    return [
        f"{key}: expected {value!r}, found {actual.get(key)!r}"
        for key, value in sorted(expected.items())
        if actual.get(key) != value
    ]


def build(images: list[LocalImage]) -> None:
    LOG.info("Building: " + ", ".join(image.reference for image in images))
    compose(
        [
            "--env-file",
            ENV_FILE,
            "--env-file",
            OPTIONAL_ENV_FILE,
            *compose_profile_arguments(),
            "build",
            *(image.build_service for image in images),
        ]
    )
    failures: list[str] = []
    for image in images:
        mismatches = label_mismatches(image.labels, image_labels(image.reference))
        failures.extend(f"{image.reference}: {mismatch}" for mismatch in mismatches)
        if not mismatches:
            LOG.info(f"PASS: {image.reference} carries its {len(image.labels)} Compose labels")
    if failures:
        fail("built image labels differ from Compose:\n" + "\n".join(failures))


def trivy_scan_command(reference: str) -> list[str]:
    cache_volume = os.environ.get("CI_TRIVY_CACHE_VOLUME") or DEFAULT_TRIVY_CACHE_VOLUME
    socket = os.environ.get("CI_DOCKER_SOCKET") or DEFAULT_DOCKER_SOCKET
    # The socket path is resolved by the Docker daemon, so the same command
    # works on a GitHub runner and inside a GitLab docker:dind service.
    return [
        "docker",
        "run",
        "--rm",
        "--volume",
        f"{socket}:/var/run/docker.sock",
        "--volume",
        f"{cache_volume}:/cache",
        monitor.TRIVY_IMAGE,
        "--cache-dir",
        "/cache",
        "image",
        "--image-src",
        "docker",
        "--scanners",
        "vuln",
        "--no-progress",
        "--quiet",
        "--format",
        "json",
        reference,
    ]


def scan_image(reference: str, timeout: int) -> object:
    result = run(trivy_scan_command(reference), capture=True, check=False, timeout=timeout)
    if result.returncode:
        assert isinstance(result.stderr, str)
        detail = " ".join(result.stderr.strip().splitlines()[-1:])
        fail(f"Trivy scan failed for {reference}" + (f": {detail}" if detail else ""))
    assert isinstance(result.stdout, str)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        fail(f"Trivy returned invalid JSON for {reference}")


def policy_violations(
    image: LocalImage,
    payload: object,
    kev_identifiers: set[str],
    exceptions: list[Any],
    today: date,
) -> list[Violation]:
    """Return findings the release gate rejects for at least one consumer service."""
    if not isinstance(payload, dict) or not isinstance(payload.get("Results"), list):
        fail(f"Trivy returned invalid scan JSON for {image.reference}")
    violations: list[Violation] = []
    for result in payload["Results"]:
        if not isinstance(result, dict):
            fail(f"Trivy returned invalid result data for {image.reference}")
        for vulnerability in result.get("Vulnerabilities") or []:
            if not isinstance(vulnerability, dict):
                fail(f"Trivy returned an invalid vulnerability for {image.reference}")
            severity = str(vulnerability.get("Severity") or "UNKNOWN").upper()
            fixed = str(vulnerability.get("FixedVersion") or "")
            is_kev = bool(monitor.vulnerability_aliases(vulnerability) & kev_identifiers)
            if not is_kev and not (severity == "CRITICAL" and fixed):
                continue
            unexcepted = tuple(
                service
                for service in image.services
                if not any(
                    monitor.exception_matches(rule, service, vulnerability, today)
                    for rule in exceptions
                )
            )
            if unexcepted:
                violations.append(
                    Violation(
                        vulnerability=str(vulnerability.get("VulnerabilityID") or "unknown"),
                        package=str(vulnerability.get("PkgName") or "unknown"),
                        installed=str(vulnerability.get("InstalledVersion") or "unknown"),
                        fixed=fixed or "none",
                        severity=severity,
                        kev=is_kev,
                        services=unexcepted,
                    )
                )
    return violations


def format_violation(reference: str, violation: Violation) -> str:
    return (
        f"{reference} {violation.vulnerability} "
        f"{violation.severity}{' KEV' if violation.kev else ''} "
        f"{violation.package} {violation.installed} -> {violation.fixed} "
        f"(services: {', '.join(violation.services)})"
    )


def scan(images: list[LocalImage], *, report_only: bool) -> None:
    timeout = monitor.parse_non_negative_int("IMAGE_SECURITY_COMMAND_TIMEOUT_SECONDS", 900)
    exceptions = monitor.load_exceptions(EXCEPTIONS_FILE)
    kev_identifiers, _body, _fetched = monitor.fetch_kev(timeout)
    LOG.info(f"Loaded {len(kev_identifiers)} CISA KEV entries and {len(exceptions)} exceptions")
    today = date.today()
    rejected = 0
    for image in images:
        image_labels(image.reference)  # fail early when the build step did not run
        violations = policy_violations(
            image, scan_image(image.reference, timeout), kev_identifiers, exceptions, today
        )
        if not violations:
            LOG.info(f"PASS: {image.reference} has no unexcepted KEV or fixable Critical finding")
            continue
        rejected += len(violations)
        for violation in violations:
            message = format_violation(image.reference, violation)
            if report_only:
                LOG.warning(f"WARNING: {message}")
            else:
                LOG.error(f"FAIL: {message}")
    summary = (
        f"{rejected} unexcepted KEV or fixable Critical finding(s); update the "
        "image or add a reviewed, time-limited entry to security/exceptions.toml"
    )
    if rejected and report_only:
        LOG.warning(f"WARNING: {summary} (report-only scan; release and nightly scans enforce)")
    elif rejected:
        fail(summary)


def main() -> None:
    arguments = parse_args()
    require_docker(daemon=True)
    set_repository_metadata()
    images = local_images(load_compose_config(ENV_FILE, OPTIONAL_ENV_FILE))
    if arguments.command == "build":
        build(images)
    else:
        scan(images, report_only=arguments.report_only)


if __name__ == "__main__":
    run_main(main)
