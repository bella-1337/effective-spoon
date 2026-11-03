#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Fetch the Grafana dashboard definitions used by this repository."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from maintenance_common import LOG, ROOT, fail, run_main

DASHBOARDS = (
    (
        "https://grafana.com/api/dashboards/17346/revisions/9/download",
        "traefik/traefik.json",
        "3ad329d2737120f32f67aab083f245b554ea5c4ec8378feee7196ef6bb9f7da9",
    ),
    (
        "https://grafana.com/api/dashboards/9628/revisions/8/download",
        "postgres/postgres.json",
        "521bac34065bcadedf255fc384550ae0528e9a74e91b4475e583b6afe9c39cc7",
    ),
    (
        "https://grafana.com/api/dashboards/1860/revisions/45/download",
        "node/node.json",
        "184c6b7409f306da75525d7772f71945b10cea23ad16b5d78c4698ea0ea51986",
    ),
    (
        "https://raw.githubusercontent.com/BerriAI/litellm/"
        "dab39d7402cb1e772045c5a702abe74f69f66970/"
        "cookbook/litellm_proxy_server/"
        "grafana_dashboard/dashboard_all_metrics/grafana_dashboard.json",
        "litellm-official/grafana_dashboard.json",
        "df016510fe233c413089aa772bc887bf0b4d1e89ff77029d85a71ae32c24b087",
    ),
)
MAX_DASHBOARD_BYTES = 10 * 1024 * 1024

USAGE = """Usage: scripts/fetch-dashboards.py

Fetches the repository's Traefik, PostgreSQL, Node, and LiteLLM Grafana
dashboard definitions into grafana/dashboards.
"""


def main() -> None:
    if sys.argv[1:] in (["-h"], ["--help"]):
        print(USAGE, end="")
        return
    if len(sys.argv) != 1:
        fail("fetch-dashboards accepts no command-line arguments", 2)
    base = ROOT / "grafana" / "dashboards"
    for _, relative, _ in DASHBOARDS:
        (base / relative).parent.mkdir(parents=True, exist_ok=True)
    for url, relative, expected_sha256 in DASHBOARDS:
        destination = base / relative
        if destination.is_symlink():
            fail(f"refusing to replace symbolic-link dashboard: {destination}")
        request = urllib.request.Request(url, headers={"User-Agent": "litellm-stack-dashboard-fetcher"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                content = response.read(MAX_DASHBOARD_BYTES + 1)
        except urllib.error.URLError as error:
            fail(f"failed to fetch {url}: {error.reason}")
        if len(content) > MAX_DASHBOARD_BYTES:
            fail(f"dashboard exceeds {MAX_DASHBOARD_BYTES} bytes: {url}")
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != expected_sha256:
            fail(
                f"dashboard checksum mismatch for {url}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as error:
            fail(f"dashboard is not valid JSON: {url}: {error}")
        if not isinstance(parsed, dict):
            fail(f"dashboard root must be a JSON object: {url}")
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as target:
                target.write(content)
            os.chmod(temporary, 0o644)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
    LOG.info(f"Dashboards fetched into {base}")


if __name__ == "__main__":
    run_main(main)
