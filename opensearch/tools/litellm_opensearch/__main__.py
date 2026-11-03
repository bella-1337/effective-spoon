"""Command-line entry point: python -m litellm_opensearch COMMAND."""

from __future__ import annotations

import logging
import os
import sys

from pythonjsonlogger.orjson import OrjsonFormatter

USAGE = """Usage: python -m litellm_opensearch COMMAND

Commands:
  setup-cluster      Provision OpenSearch users, roles, templates, ISM, data sources, and monitors
  setup-dashboards   Import OpenSearch Dashboards index patterns, visualizations, and dashboards
  bridge             Run the Prometheus and LiteLLM PostgreSQL to OpenSearch bridge
  healthcheck        Exit non-zero when the bridge has not completed a recent cycle
"""


def configure_logging() -> None:
    """Write one JSON object per line so a traceback stays in one log entry.

    The level comes first because the log collectors take the first
    level-like token on a line as the entry's level.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        OrjsonFormatter(
            "{levelname}{name}{message}",
            style="{",
            rename_fields={"levelname": "level", "name": "logger"},
            timestamp="time",
        )
    )
    logging.basicConfig(
        level=os.environ.get("OPENSEARCH_TOOLS_LOG_LEVEL", "INFO").upper(),
        handlers=[handler],
        force=True,
    )


def main(argv: list[str]) -> int:
    configure_logging()
    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        print(USAGE, end="")
        return 0 if argv and argv[0] in ("-h", "--help") else 2
    command = argv[0]
    if command == "setup-cluster":
        from .setup import setup_cluster

        return setup_cluster()
    if command == "setup-dashboards":
        from .setup import setup_dashboards

        return setup_dashboards()
    if command == "bridge":
        from .bridge import run

        return run()
    if command == "healthcheck":
        from .bridge import healthcheck

        return healthcheck()
    print(USAGE, end="", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
