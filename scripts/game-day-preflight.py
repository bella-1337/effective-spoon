#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Validate the static safety contracts required before an operations game day."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from maintenance_common import ROOT, ScriptError
from maintenance_contracts import (
    load_compose_config,
    validate_compose_contracts,
    validate_repository_contracts,
)


def validate_contracts(config: dict[str, object]) -> list[str]:
    return [
        *validate_compose_contracts(config),
        *validate_repository_contracts(),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=ROOT / ".env",
        help="environment file used to render every Compose profile",
    )
    parser.add_argument(
        "--optional-env-file",
        type=Path,
        default=(
            ROOT / ".env.optional"
            if (ROOT / ".env.optional").is_file()
            else ROOT / ".env.optional.example"
        ),
        help="optional-profile environment file used after the core environment",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_file = args.env_file.expanduser().resolve()
    if not env_file.is_file() or env_file.is_symlink():
        print(f"ERROR: environment file is missing or unsafe: {env_file}", file=sys.stderr)
        return 2
    optional_env_file = args.optional_env_file.expanduser().resolve()
    if not optional_env_file.is_file() or optional_env_file.is_symlink():
        print(
            f"ERROR: optional environment file is missing or unsafe: {optional_env_file}",
            file=sys.stderr,
        )
        return 2

    try:
        config = load_compose_config(env_file, optional_env_file)
        failures = validate_contracts(config)
    except (OSError, ValueError, ScriptError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1

    print("PASS: game-day static safety contracts are satisfied")
    print("NEXT: execute the isolated and live drills in OPERATIONS-GAME-DAY.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
