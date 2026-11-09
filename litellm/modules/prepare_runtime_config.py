#!/usr/bin/env python3
"""Prepare a LiteLLM config with optional Langfuse callbacks and Lens tracing.

Usage: prepare_runtime_config.py SOURCE DESTINATION [-- COMMAND [ARGUMENT...]]

Each optional integration is enabled only while its profile's service resolves
on the Compose network, and then fails closed when its credentials are
missing. LiteLLM enables its Lens exporter whenever LITELLM_LENS_URL is set,
so with a COMMAND the launcher replaces itself with that command after
removing the Lens connection variables when Lens is not running.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence
from urllib.parse import urlsplit

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
LENS_HOST = "lens"
LENS_PORT = 4318
LENS_ENV = (
    "LITELLM_LENS_URL",
    "LITELLM_LENS_PUBLIC_URL",
    "LITELLM_LENS_SERVICE_TOKEN",
    "LENS_GATEWAY_SECRET",
)
LENS_SECRETS = ("LITELLM_LENS_SERVICE_TOKEN", "LENS_GATEWAY_SECRET")
# Lens and LiteLLM both reject connection secrets outside this length range.
LENS_SECRET_LENGTH = range(32, 513)


def service_resolves(host: str, port: int) -> bool:
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    return True


def langfuse_is_available(host: str = LANGFUSE_HOST) -> bool:
    return service_resolves(host, 3000)


def lens_is_available(host: str = LENS_HOST) -> bool:
    return service_resolves(host, LENS_PORT)


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


def validate_langfuse_environment(environ: Mapping[str, str] = os.environ) -> None:
    missing = [name for name in REQUIRED_LANGFUSE_ENV if not environ.get(name)]
    if missing:
        raise ValueError(
            "Langfuse is running but LiteLLM is missing required environment variables: "
            + ", ".join(missing)
        )


def general_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = config.setdefault("general_settings", {})
    if not isinstance(settings, dict):
        raise ValueError("general_settings must be a mapping")
    return settings


def with_lens_tracing(config: dict[str, Any]) -> dict[str, Any]:
    general_settings(config)["tracing"] = {"store": {"type": "lens"}}
    return config


def without_lens_tracing(config: dict[str, Any]) -> dict[str, Any]:
    settings = config.get("general_settings")
    if isinstance(settings, dict):
        settings.pop("tracing", None)
    return config


def validate_lens_environment(environ: Mapping[str, str] = os.environ) -> None:
    missing = [name for name in LENS_ENV if not environ.get(name)]
    if missing:
        raise ValueError(
            "Lens is running but LiteLLM is missing required environment variables: "
            + ", ".join(missing)
        )
    for name in LENS_SECRETS:
        if len(environ[name].encode()) not in LENS_SECRET_LENGTH:
            raise ValueError(f"{name} must contain between 32 and 512 characters")
    if environ["LITELLM_LENS_SERVICE_TOKEN"] == environ["LENS_GATEWAY_SECRET"]:
        raise ValueError(
            "LITELLM_LENS_SERVICE_TOKEN and LENS_GATEWAY_SECRET must be different secrets"
        )
    for name in ("LITELLM_LENS_URL", "LITELLM_LENS_PUBLIC_URL"):
        parsed = urlsplit(environ[name])
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                f"{name} must be an http(s) URL with a host; set LENS_HOST in .env.optional"
            )


def prepare_config(
    source: Path,
    destination: Path,
    environ: MutableMapping[str, str] | None = None,
) -> tuple[bool, bool]:
    """Write the runtime config and return (Langfuse enabled, Lens enabled).

    When ``environ`` is given, the Lens connection variables are removed from it
    while Lens is not running, so a launched gateway cannot start its exporter.
    """
    with source.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    if not isinstance(config, dict):
        raise ValueError("LiteLLM config must be a YAML mapping")
    current = os.environ if environ is None else environ

    langfuse_enabled = langfuse_is_available()
    if langfuse_enabled:
        validate_langfuse_environment(current)
    else:
        without_langfuse_callbacks(config)

    lens_enabled = lens_is_available()
    if lens_enabled:
        validate_lens_environment(current)
        with_lens_tracing(config)
    else:
        without_lens_tracing(config)
        if environ is not None:
            for name in LENS_ENV:
                environ.pop(name, None)

    with destination.open("w", encoding="utf-8") as stream:
        yaml.safe_dump(config, stream, sort_keys=False)
    return langfuse_enabled, lens_enabled


def parse_arguments(arguments: Sequence[str]) -> tuple[Path, Path, list[str]] | None:
    if len(arguments) < 2:
        return None
    source, destination, *rest = arguments
    if not rest:
        return Path(source), Path(destination), []
    if rest[0] != "--" or len(rest) < 2:
        return None
    return Path(source), Path(destination), list(rest[1:])


def main(arguments: Sequence[str] | None = None) -> int:
    parsed = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    if parsed is None:
        print(
            f"Usage: {Path(sys.argv[0]).name} SOURCE DESTINATION [-- COMMAND [ARGUMENT...]]",
            file=sys.stderr,
        )
        return 64

    source, destination, command = parsed
    environment = dict(os.environ)
    try:
        langfuse_enabled, lens_enabled = prepare_config(source, destination, environment)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"Could not prepare LiteLLM runtime config: {error}", file=sys.stderr)
        return 1

    print(f"Langfuse callbacks are {'enabled' if langfuse_enabled else 'disabled'}")
    print(f"LiteLLM Lens tracing is {'enabled' if lens_enabled else 'disabled'}")
    if not command:
        return 0
    sys.stdout.flush()
    try:
        os.execvpe(command[0], command, environment)
    except OSError as error:
        print(f"Could not start {command[0]}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
