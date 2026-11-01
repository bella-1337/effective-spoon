#!/usr/bin/env python3
"""Prepare a LiteLLM config with optional Langfuse callbacks."""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from typing import Any

import yaml

LANGFUSE_CALLBACKS = {
    "langfuse_otel",
    "modules.langfuse_user_attribution.proxy_handler_instance",
}
LANGFUSE_HOST = "langfuse-web"
REQUIRED_LANGFUSE_ENV = (
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_USER_ID_SALT",
)


def langfuse_is_available(host: str = LANGFUSE_HOST) -> bool:
    try:
        socket.getaddrinfo(host, 3000, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    return True


def without_langfuse_callbacks(config: dict[str, Any]) -> dict[str, Any]:
    settings = config.get("litellm_settings")
    if not isinstance(settings, dict):
        raise ValueError("LiteLLM config must contain a litellm_settings mapping")

    callbacks = settings.get("callbacks", [])
    if not isinstance(callbacks, list) or not all(
        isinstance(callback, str) for callback in callbacks
    ):
        raise ValueError("litellm_settings.callbacks must be a list of strings")

    remaining = [
        callback for callback in callbacks if callback not in LANGFUSE_CALLBACKS
    ]
    if remaining:
        settings["callbacks"] = remaining
    else:
        settings.pop("callbacks", None)
    return config


def validate_langfuse_environment() -> None:
    missing = [name for name in REQUIRED_LANGFUSE_ENV if not os.environ.get(name)]
    if missing:
        raise ValueError(
            "Langfuse is running but LiteLLM is missing required environment variables: "
            + ", ".join(missing)
        )


def prepare_config(source: Path, destination: Path) -> bool:
    with source.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    if not isinstance(config, dict):
        raise ValueError("LiteLLM config must be a YAML mapping")

    enabled = langfuse_is_available()
    if enabled:
        validate_langfuse_environment()
    else:
        without_langfuse_callbacks(config)

    with destination.open("w", encoding="utf-8") as stream:
        yaml.safe_dump(config, stream, sort_keys=False)
    return enabled


def main() -> int:
    if len(sys.argv) != 3:
        print(
            f"Usage: {Path(sys.argv[0]).name} SOURCE DESTINATION",
            file=sys.stderr,
        )
        return 64

    source, destination = (Path(value) for value in sys.argv[1:])
    try:
        enabled = prepare_config(source, destination)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"Could not prepare LiteLLM runtime config: {error}", file=sys.stderr)
        return 1

    status = "enabled" if enabled else "disabled"
    print(f"Langfuse callbacks are {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
