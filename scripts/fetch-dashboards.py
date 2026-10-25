#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Fetch the Grafana dashboard definitions used by this repository."""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

from maintenance_common import ROOT, fail, run_main

DASHBOARDS = (
    ("https://grafana.com/api/dashboards/17346/revisions/latest/download", "traefik/traefik.json"),
    ("https://grafana.com/api/dashboards/9628/revisions/latest/download", "postgres/postgres.json"),
    ("https://grafana.com/api/dashboards/1860/revisions/latest/download", "node/node.json"),
    (
        "https://raw.githubusercontent.com/BerriAI/litellm/main/cookbook/litellm_proxy_server/"
        "grafana_dashboard/dashboard_all_metrics/grafana_dashboard.json",
        "litellm-official/grafana_dashboard.json",
    ),
)

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
    for _, relative in DASHBOARDS:
        (base / relative).parent.mkdir(parents=True, exist_ok=True)
    for url, relative in DASHBOARDS:
        destination = base / relative
        request = urllib.request.Request(url, headers={"User-Agent": "litellm-stack-dashboard-fetcher"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                content = response.read()
        except urllib.error.URLError as error:
            fail(f"failed to fetch {url}: {error.reason}")
        destination.write_bytes(content)
    print(f"Dashboards fetched into {base}")


if __name__ == "__main__":
    run_main(main)
