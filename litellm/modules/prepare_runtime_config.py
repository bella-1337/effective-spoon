#!/usr/bin/env python3
"""Prepare a LiteLLM config with region-locked models, Langfuse, and Lens.

Usage: prepare_runtime_config.py SOURCE DESTINATION [-- COMMAND [ARGUMENT...]]

Cloud models come only from the environment: sovereign_models.py turns the
provider variables into model entries locked to DATA_SOVEREIGNTY_REGION and
appends them to the source config's static model list. With no provider models
set, the gateway starts with only its static self-hosted models.

Each optional integration is enabled only while its profile's service resolves
on the Compose network, and then fails closed when its credentials are
missing. LiteLLM enables its Lens exporter whenever LITELLM_LENS_URL is set,
so with a COMMAND the launcher replaces itself with that command after
removing the Lens connection variables when Lens is not running.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence
from urllib.parse import urlsplit

import yaml
from sovereign_models import sovereign_model_list

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
# D3-mini receives every coding-router prompt. A remote host is reached only
# over HTTPS; plain HTTP is allowed only for the optional on-host d3 profile,
# whose service is reachable solely on the internal d3-local network.
D3_API_BASE_ENV = "D3_DECISION_API_BASE"
D3_LOCAL_API_BASE = "http://d3-mini:8000"


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


def validate_d3_endpoint(environ: Mapping[str, str] = os.environ) -> None:
    value = environ.get(D3_API_BASE_ENV, "").strip()
    if value == D3_LOCAL_API_BASE:
        return
    parsed = urlsplit(value)
    try:
        port_is_valid = parsed.port is None or parsed.port > 0
    except ValueError:
        port_is_valid = False
    if (
        not port_is_valid
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/")
    ):
        raise ValueError(
            f"{D3_API_BASE_ENV} must be an https:// base URL without a path, or "
            f"{D3_LOCAL_API_BASE} for the on-host d3 profile"
        )


def with_sovereign_models(config: dict[str, Any], environ: Mapping[str, str]) -> tuple[str, list[str]]:
    """Append the environment's provider models; return (region, model names)."""
    region, entries = sovereign_model_list(environ)
    for entry in entries:
        credentials = entry["litellm_params"].get("vertex_credentials")
        if credentials and not Path(credentials).is_file():
            raise ValueError(
                f"Vertex AI credentials file {credentials} is missing; put "
                "VERTEX_CREDENTIALS_FILE in PROVIDER_SECRETS_DIR"
            )
    static = config.get("model_list") or []
    if not isinstance(static, list):
        raise ValueError("model_list must be a list")
    clashes = sorted(
        {item.get("model_name") for item in static if isinstance(item, dict)}
        & {entry["model_name"] for entry in entries}
    )
    if clashes:
        raise ValueError(f"model names already defined in the static config: {', '.join(clashes)}")
    config["model_list"] = [*entries, *static]
    return region, [entry["model_name"] for entry in entries]


PII_ASSIST_MODEL_NAME = "pii-assist"
PII_ASSIST_API_BASE_ENV = "PII_ASSIST_API_BASE"
PII_ASSIST_API_KEY_ENV = "PII_ASSIST_API_KEY"
PII_ASSIST_VLLM_MODEL_ENV = "PII_ASSIST_VLLM_MODEL"
# The name the vLLM server serves (vllm serve ... --served-model-name gemma4).
PII_ASSIST_DEFAULT_VLLM_MODEL = "gemma4"
# A served model name or a Hugging Face repository id (organisation/name).
VLLM_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,95})?")
API_KEY_RE = re.compile(r"[A-Za-z0-9._~-]{32,512}")


def validate_local_api_base(value: str) -> str:
    """Accept only an on-host or private-network model server, never a cloud API."""
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.path not in {"", "/", "/v1", "/v1/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError(
            f"{PII_ASSIST_API_BASE_ENV} must be an http(s) base URL, optionally ending in /v1, "
            "without other path segments or credentials"
        )
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    local = (
        host == "host.docker.internal"
        or (address is None and "." not in host)  # a Compose service name
        or (address is not None and address.is_private and not address.is_link_local)
    )
    if not local:
        raise ValueError(
            f"{PII_ASSIST_API_BASE_ENV} must name host.docker.internal, a Compose service, or a "
            "private address: personal data is sent to this model unmasked, so it must not be a "
            "cloud API (cloud models come only from the region-locked provider variables)"
        )
    # vLLM serves its OpenAI-compatible API under /v1.
    return f"{parsed.scheme}://{parsed.netloc}/v1"


def with_pii_assist(config: dict[str, Any], environ: Mapping[str, str]) -> str | None:
    """Add the local pii-assist model when configured; return its vLLM model name."""
    api_base = environ.get(PII_ASSIST_API_BASE_ENV, "").strip()
    if not api_base:
        return None
    api_base = validate_local_api_base(api_base)
    model = environ.get(PII_ASSIST_VLLM_MODEL_ENV, "").strip() or PII_ASSIST_DEFAULT_VLLM_MODEL
    if not VLLM_MODEL_RE.fullmatch(model):
        raise ValueError(
            f"{PII_ASSIST_VLLM_MODEL_ENV} must be the model name the vLLM server serves, such as gemma4"
        )
    # The model reads prompts unmasked, so its server must require a key.
    api_key = environ.get(PII_ASSIST_API_KEY_ENV, "")
    if not API_KEY_RE.fullmatch(api_key) or api_key.startswith("replace_with_"):
        raise ValueError(
            f"{PII_ASSIST_API_KEY_ENV} must be 32-512 URL-safe random characters, the key the vLLM "
            "server was started with (vllm serve ... --api-key); generate one with 'openssl rand -hex 32'"
        )
    static = config.get("model_list") or []
    if any(isinstance(item, dict) and item.get("model_name") == PII_ASSIST_MODEL_NAME for item in static):
        raise ValueError(f"model name {PII_ASSIST_MODEL_NAME} is reserved for the PII assist model")
    config["model_list"] = [
        *static,
        {
            "model_name": PII_ASSIST_MODEL_NAME,
            "litellm_params": {
                "model": f"hosted_vllm/{model}",
                "api_base": api_base,
                # Resolved by LiteLLM at start-up, so the key is not written
                # into the runtime config file.
                "api_key": f"os.environ/{PII_ASSIST_API_KEY_ENV}",
            },
            "model_info": {
                "mode": "chat",
                "description": "Local language and personal-data detection for the PII guardrail",
            },
        },
    ]
    return model


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
    validate_d3_endpoint(current)
    region, models = with_sovereign_models(config, current)
    assist_model = with_pii_assist(config, current)

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
    print(
        f"Data sovereignty region: {region}; provider models: "
        + (", ".join(models) if models else "none")
    )
    print(
        "PII assist model: "
        + (f"{PII_ASSIST_MODEL_NAME} (vLLM {assist_model})" if assist_model else "not configured; Presidio runs alone")
    )
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
