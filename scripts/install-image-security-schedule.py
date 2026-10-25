#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Manage the Linux systemd image-security scan schedule."""

from __future__ import annotations

import argparse
import grp
import os
import pwd
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from maintenance_common import ROOT, fail, run, run_main

UNIT_BASENAME = "litellm-stack-image-security"
SERVICE_NAME = f"{UNIT_BASENAME}.service"
TIMER_NAME = f"{UNIT_BASENAME}.timer"
SYSTEMD_DIRECTORY = Path("/etc/systemd/system")
PROJECT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass(frozen=True)
class ServiceAccount:
    name: str
    group: str
    uid: int
    gid: int
    home: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install, remove, or inspect the nightly Linux systemd timer."
    )
    parser.add_argument("action", choices=("install", "uninstall", "status"))
    parser.add_argument(
        "--hour",
        type=int,
        default=2,
        help="local hour for the nightly scan (default: 2)",
    )
    parser.add_argument(
        "--minute",
        type=int,
        default=45,
        help="local minute for the nightly scan (default: 45)",
    )
    parser.add_argument(
        "--service-user",
        help="non-root Linux account that can access the Docker daemon",
    )
    parser.add_argument(
        "--uv-path",
        type=Path,
        help="absolute path to the uv executable used by systemd",
    )
    parser.add_argument("--project", help="Docker Compose project name")
    parser.add_argument("--state-dir", type=Path, help="private scanner state directory")
    parser.add_argument("--report-dir", type=Path, help="private JSON report directory")
    parser.add_argument("--metrics-dir", type=Path, help="Prometheus textfile directory")
    parser.add_argument(
        "--exceptions-file",
        type=Path,
        help="image-security exception policy",
    )
    return parser.parse_args()


def require_linux() -> None:
    if not sys.platform.startswith("linux"):
        fail("security-schedule is supported only on Linux with systemd")


def require_root() -> None:
    if os.geteuid() != 0:
        fail("security-schedule install and uninstall must run as root")


def service_account(requested: str | None) -> ServiceAccount:
    name = requested or os.environ.get("SUDO_USER")
    if not name:
        fail("--service-user is required when the installer is not run through sudo")
    try:
        record = pwd.getpwnam(name)
    except KeyError:
        fail(f"Linux service account does not exist: {name}")
    if record.pw_uid == 0:
        fail("the image-security scanner must not run as root")
    home = Path(record.pw_dir).resolve()
    if not home.is_dir():
        fail(f"service account home directory does not exist: {home}")
    try:
        group = grp.getgrgid(record.pw_gid).gr_name
    except KeyError:
        fail(f"primary group does not exist for service account: {name}")
    return ServiceAccount(record.pw_name, group, record.pw_uid, record.pw_gid, home)


def executable_path(requested: Path | None, command: str) -> Path:
    candidate = str(requested) if requested else shutil.which(command)
    if not candidate:
        suffix = " or pass --uv-path" if command == "uv" else ""
        fail(f"{command} is required; install it system-wide{suffix}")
    resolved = Path(candidate).expanduser().resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        fail(f"{command} executable is missing or not executable: {resolved}")
    return resolved


def systemctl_path() -> Path:
    return executable_path(None, "systemctl")


def unit_paths() -> tuple[Path, Path]:
    return (
        SYSTEMD_DIRECTORY / SERVICE_NAME,
        SYSTEMD_DIRECTORY / TIMER_NAME,
    )


def systemd_quote(value: str) -> str:
    if any(character in value for character in ("\0", "\n", "\r")):
        fail("systemd unit values must not contain control characters")
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("%", "%%")
        .replace("$", "$$")
    )
    return f'"{escaped}"'


def systemd_path(path: Path) -> str:
    value = str(path)
    if (
        not path.is_absolute()
        or any(character.isspace() for character in value)
        or any(character in value for character in ('"', "\\", "\0"))
    ):
        fail(f"path is not safe for a systemd directive: {path}")
    return value.replace("%", "%%")


def runtime_path(
    requested: Path | None,
    environment_name: str,
    default: Path,
) -> Path:
    path = Path(requested or os.environ.get(environment_name) or default)
    path = path.expanduser().resolve()
    if path == Path("/"):
        fail(f"{environment_name} must not be the filesystem root")
    return path


def prepare_runtime_directory(
    path: Path,
    account: ServiceAccount,
    mode: int,
    name: str,
) -> None:
    if path.is_symlink():
        fail(f"{name} must not be a symbolic link: {path}")
    path.mkdir(mode=mode, parents=True, exist_ok=True)
    if not path.is_dir() or path.is_symlink():
        fail(f"{name} is not a safe directory: {path}")
    os.chown(path, account.uid, account.gid)
    os.chmod(path, mode)


def atomic_write(path: Path, payload: str, mode: int = 0o644) -> None:
    if path.is_symlink():
        fail(f"refusing to replace symbolic link: {path}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()


def build_units(
    *,
    account: ServiceAccount,
    uv: Path,
    docker: Path,
    hour: int,
    minute: int,
    state_root: Path,
    report_root: Path,
    metrics_root: Path,
    policy: Path,
    project: str,
) -> tuple[str, str]:
    scanner = ROOT / "scripts" / "monitor-image-vulnerabilities.py"
    if not scanner.is_file() or scanner.is_symlink():
        fail("the image-security monitor script is missing or unsafe")
    path_directories: list[str] = []
    for directory in (
        uv.parent,
        docker.parent,
        Path("/usr/local/bin"),
        Path("/usr/bin"),
        Path("/bin"),
        Path("/usr/local/sbin"),
        Path("/usr/sbin"),
        Path("/sbin"),
    ):
        value = str(directory)
        if value not in path_directories:
            path_directories.append(value)

    environment = {
        "HOME": str(account.home),
        "PATH": ":".join(path_directories),
        "XDG_CACHE_HOME": str(state_root / "xdg-cache"),
        "XDG_DATA_HOME": str(state_root / "xdg-data"),
        "UV_CACHE_DIR": str(state_root / "uv-cache"),
        "UV_PYTHON_INSTALL_DIR": str(state_root / "uv-python"),
        "COMPOSE_PROJECT_NAME": project,
        "IMAGE_SECURITY_STATE_DIR": str(state_root),
        "IMAGE_SECURITY_REPORT_DIR": str(report_root),
        "IMAGE_SECURITY_METRICS_DIR": str(metrics_root),
        "IMAGE_SECURITY_EXCEPTIONS_FILE": str(policy),
    }
    environment_lines = "\n".join(
        f"Environment={systemd_quote(f'{key}={value}')}"
        for key, value in environment.items()
    )
    exec_start = " ".join(
        systemd_quote(str(argument))
        for argument in (uv, "run", "--script", scanner)
    )
    writable_paths = " ".join(
        systemd_quote(str(path))
        for path in (state_root, report_root, metrics_root)
    )
    service = f"""[Unit]
Description=Scan deployed LiteLLM container images for vulnerabilities
Documentation=https://aquasecurity.github.io/trivy/
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
User={account.name}
Group={account.group}
WorkingDirectory={systemd_path(ROOT)}
{environment_lines}
ExecStart={exec_start}
TimeoutStartSec=2h
UMask=0027
Nice=10
IOSchedulingClass=idle
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={writable_paths}
ProtectControlGroups=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectKernelLogs=true
ProtectClock=true
ProtectHostname=true
LockPersonality=true
MemoryDenyWriteExecute=true
RestrictRealtime=true
RestrictSUIDSGID=true
"""
    timer = f"""[Unit]
Description=Run the LiteLLM image-security scan nightly

[Timer]
OnCalendar=*-*-* {hour:02d}:{minute:02d}:00
Persistent=true
AccuracySec=1min
Unit={SERVICE_NAME}

[Install]
WantedBy=timers.target
"""
    return service, timer


def install(
    hour: int,
    minute: int,
    requested_user: str | None,
    requested_uv: Path | None,
    requested_project: str | None,
    requested_state: Path | None,
    requested_report: Path | None,
    requested_metrics: Path | None,
    requested_policy: Path | None,
) -> None:
    require_linux()
    require_root()
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        fail("--hour must be 0-23 and --minute must be 0-59")
    account = service_account(requested_user)
    uv = executable_path(requested_uv, "uv")
    docker = executable_path(None, "docker")
    systemctl = systemctl_path()
    systemd_analyze = executable_path(None, "systemd-analyze")
    project = (
        requested_project
        or os.environ.get("COMPOSE_PROJECT_NAME")
        or "litellm-stack"
    )
    if not PROJECT_NAME_RE.fullmatch(project):
        fail("Compose project must match [a-z0-9][a-z0-9_-]*")
    state_root = runtime_path(
        requested_state,
        "IMAGE_SECURITY_STATE_DIR", ROOT / "security-state"
    )
    report_root = runtime_path(
        requested_report,
        "IMAGE_SECURITY_REPORT_DIR", ROOT / "security-reports"
    )
    metrics_root = runtime_path(
        requested_metrics,
        "IMAGE_SECURITY_METRICS_DIR", ROOT / "security-metrics"
    )
    policy = runtime_path(
        requested_policy,
        "IMAGE_SECURITY_EXCEPTIONS_FILE", ROOT / "security" / "exceptions.toml"
    )
    if not policy.is_file() or policy.is_symlink():
        fail(f"exception policy is missing or unsafe: {policy}")
    prepare_runtime_directory(state_root, account, 0o700, "state directory")
    prepare_runtime_directory(report_root, account, 0o700, "report directory")
    prepare_runtime_directory(metrics_root, account, 0o755, "metrics directory")
    service, timer = build_units(
        account=account,
        uv=uv,
        docker=docker,
        hour=hour,
        minute=minute,
        state_root=state_root,
        report_root=report_root,
        metrics_root=metrics_root,
        policy=policy,
        project=project,
    )
    service_path, timer_path = unit_paths()
    atomic_write(service_path, service)
    atomic_write(timer_path, timer)
    run([systemd_analyze, "verify", service_path, timer_path], capture=True)
    run([systemctl, "daemon-reload"])
    run([systemctl, "enable", "--now", TIMER_NAME])
    print(f"Installed {TIMER_NAME} at {hour:02d}:{minute:02d} local time")
    print(f"Service account: {account.name}")
    print(f"Compose project: {project}")
    print(f"State: {state_root}")
    print(f"Reports: {report_root}")
    print(f"Metrics: {metrics_root}")
    print(f"Exceptions: {policy}")
    print(f"Units: {service_path}, {timer_path}")


def uninstall() -> None:
    require_linux()
    require_root()
    systemctl = systemctl_path()
    run([systemctl, "disable", "--now", TIMER_NAME], check=False)
    run([systemctl, "stop", SERVICE_NAME], check=False)
    for path in unit_paths():
        if path.exists():
            if not path.is_file() or path.is_symlink():
                fail(f"refusing to remove unsafe systemd unit path: {path}")
            path.unlink()
    run([systemctl, "daemon-reload"])
    run([systemctl, "reset-failed", SERVICE_NAME], check=False)
    print(
        f"Uninstalled {TIMER_NAME}; scan reports, state, and metrics were retained"
    )


def status() -> None:
    require_linux()
    result = run(
        [systemctl_path(), "status", TIMER_NAME, "--no-pager"],
        check=False,
    )
    if result.returncode:
        raise SystemExit(result.returncode)


def main() -> None:
    arguments = parse_args()
    if arguments.action == "install":
        install(
            arguments.hour,
            arguments.minute,
            arguments.service_user,
            arguments.uv_path,
            arguments.project,
            arguments.state_dir,
            arguments.report_dir,
            arguments.metrics_dir,
            arguments.exceptions_file,
        )
    elif arguments.action == "uninstall":
        uninstall()
    else:
        status()


if __name__ == "__main__":
    run_main(main)
