#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Run the image-security monitor on a daily in-container schedule."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from maintenance_common import fail, run_main

STOP_EVENT = threading.Event()
ACTIVE_PROCESS: subprocess.Popen[str] | None = None


def bounded_environment_integer(name: str, default: int, maximum: int) -> int:
    raw = os.environ.get(name)
    try:
        value = int(raw) if raw is not None else default
    except ValueError:
        fail(f"{name} must be an integer")
    if not 0 <= value <= maximum:
        fail(f"{name} must be between 0 and {maximum}")
    return value


def environment_boolean(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    fail(f"{name} must be true or false")


def local_timezone() -> ZoneInfo:
    configured = os.environ.get("TZ")
    if configured:
        try:
            return ZoneInfo(configured)
        except ZoneInfoNotFoundError:
            fail(f"TZ must be a valid IANA timezone: {configured}")

    localtime = Path("/etc/localtime")
    if not localtime.is_file():
        fail("TZ is unset and /etc/localtime must be a regular file")
    with localtime.open("rb") as source:
        return ZoneInfo.from_file(source)


def next_run(now: datetime, hour: int, minute: int) -> datetime:
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def handle_signal(_signum: int, _frame: object) -> None:
    STOP_EVENT.set()


def run_monitor(scanner: Path, arguments: list[str]) -> int:
    global ACTIVE_PROCESS
    ACTIVE_PROCESS = subprocess.Popen(
        [sys.executable, str(scanner), *arguments],
        shell=False,
        text=True,
    )
    try:
        while ACTIVE_PROCESS.poll() is None:
            if STOP_EVENT.wait(1):
                ACTIVE_PROCESS.terminate()
                try:
                    return ACTIVE_PROCESS.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    ACTIVE_PROCESS.kill()
                    return ACTIVE_PROCESS.wait()
        return ACTIVE_PROCESS.returncode or 0
    finally:
        ACTIVE_PROCESS = None


def run_scheduled_jobs(
    scanner: Path, scanner_arguments: list[str], sbom_generator: Path | None
) -> None:
    result = run_monitor(scanner, scanner_arguments)
    if result:
        print(
            f"Image-security scan failed with exit code {result}",
            file=sys.stderr,
            flush=True,
        )
    if sbom_generator is None or STOP_EVENT.is_set():
        return
    result = run_monitor(sbom_generator, [])
    if result:
        print(
            f"Image SBOM generation failed with exit code {result}",
            file=sys.stderr,
            flush=True,
        )


def main() -> None:
    hour = bounded_environment_integer("IMAGE_SECURITY_SCHEDULE_HOUR", 2, 23)
    minute = bounded_environment_integer("IMAGE_SECURITY_SCHEDULE_MINUTE", 45, 59)
    run_on_start = environment_boolean("IMAGE_SECURITY_RUN_ON_START", True)
    sbom_enabled = environment_boolean("IMAGE_SECURITY_SBOM_ENABLED", True)
    scanner = Path(__file__).resolve().with_name(
        "monitor-image-vulnerabilities.py"
    )
    if not scanner.is_file() or scanner.is_symlink():
        fail(f"image-security monitor is missing or unsafe: {scanner}")
    sbom_generator: Path | None = None
    if sbom_enabled:
        sbom_generator = scanner.with_name("generate-image-sbom.py")
        if not sbom_generator.is_file() or sbom_generator.is_symlink():
            fail(f"image SBOM generator is missing or unsafe: {sbom_generator}")

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, handle_signal)

    scanner_arguments = sys.argv[1:]
    timezone = local_timezone()
    if run_on_start:
        run_scheduled_jobs(scanner, scanner_arguments, sbom_generator)

    while not STOP_EVENT.is_set():
        now = datetime.now(timezone)
        scheduled = next_run(now, hour, minute)
        delay = max(0.0, scheduled.timestamp() - now.timestamp())
        print(
            f"Next image-security scan: {scheduled.isoformat()}",
            flush=True,
        )
        if STOP_EVENT.wait(delay):
            break
        run_scheduled_jobs(scanner, scanner_arguments, sbom_generator)


if __name__ == "__main__":
    run_main(main)
