"""Build the LiteLLM model list from environment variables, locked to one region.

The stack's central setting is DATA_SOVEREIGNTY_REGION. Every cloud model is
declared in the environment, and none is required: with no provider models set,
the gateway starts without cloud models. Each provider is enabled only when its
model list is set, and then fails closed when its credentials are missing or
its location would let a prompt leave the chosen region.

The generated entries hold only os.environ/ references, never secret values,
so the runtime config written to disk contains no credentials.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping

REGION_ENV = "DATA_SOVEREIGNTY_REGION"
SECRETS_DIR = PurePosixPath("/app/secrets")
DEFAULT_AZURE_API_VERSION = "2025-04-01-preview"
TIERS = ("SIMPLE", "MEDIUM", "COMPLEX", "REASONING")
# Names the static configs already use for self-hosted and internal models.
RESERVED_MODEL_NAMES = frozenset({"d3-mini", "bge-m3", "test-model", "coding-router"})
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
BEDROCK_MODEL_ID = re.compile(r"[a-z0-9-]+\.[A-Za-z0-9._:-]+")
AZURE_DEPLOYMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
VERTEX_MODEL_ID = re.compile(r"[a-z0-9][a-z0-9.@_-]{0,127}")
CREDENTIALS_FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
# Bedrock profile prefixes that route across geographies (or worldwide).
BEDROCK_ROUTING_PREFIXES = ("global.", "us.", "eu.", "apac.", "jp.", "au.", "ca.", "us-gov.")


@dataclass(frozen=True)
class RegionPolicy:
    """Provider locations whose processing stays inside one region."""

    description: str
    # Bedrock geographic inference-profile prefix for this region.
    bedrock_profile: str
    # Source Regions from which that profile routes only to Regions in this
    # region. Some sources add outside Regions (for example eu-west-2 adds
    # London and eu-central-2 adds Zurich to the "eu" profile).
    bedrock_source_regions: frozenset[str]
    # Azure regions whose Data Zone deployments process inside this region.
    azure_regions: frozenset[str]
    # Vertex AI multi-region and single-region locations inside this region.
    vertex_locations: frozenset[str]


REGION_POLICIES: Mapping[str, RegionPolicy] = {
    "eu": RegionPolicy(
        description="European Union member states",
        bedrock_profile="eu",
        bedrock_source_regions=frozenset({
            "eu-central-1", "eu-north-1", "eu-south-1", "eu-south-2", "eu-west-1", "eu-west-3",
        }),
        azure_regions=frozenset({
            "francecentral", "germanywestcentral", "italynorth", "northeurope",
            "polandcentral", "spaincentral", "swedencentral", "westeurope",
        }),
        vertex_locations=frozenset({
            "eu", "europe-central2", "europe-north1", "europe-north2", "europe-southwest1",
            "europe-west1", "europe-west3", "europe-west4", "europe-west8", "europe-west9",
            "europe-west10", "europe-west12",
        }),
    ),
    "us": RegionPolicy(
        description="United States",
        bedrock_profile="us",
        bedrock_source_regions=frozenset({"us-east-1", "us-east-2", "us-west-1", "us-west-2"}),
        azure_regions=frozenset({
            "centralus", "eastus", "eastus2", "northcentralus", "southcentralus",
            "westus", "westus2", "westus3",
        }),
        vertex_locations=frozenset({
            "us", "us-central1", "us-east1", "us-east4", "us-east5", "us-south1",
            "us-west1", "us-west2", "us-west3", "us-west4",
        }),
    ),
}


def region_policy(environ: Mapping[str, str]) -> tuple[str, RegionPolicy]:
    region = environ.get(REGION_ENV, "").strip().lower()
    if region not in REGION_POLICIES:
        raise ValueError(
            f"{REGION_ENV} must be one of: {', '.join(sorted(REGION_POLICIES))}"
        )
    return region, REGION_POLICIES[region]


def parse_list(environ: Mapping[str, str], name: str, pattern: re.Pattern[str]) -> list[tuple[str | None, str]]:
    """Parse a comma-separated list of ``value`` or ``model-name=value`` items."""
    items: list[tuple[str | None, str]] = []
    for raw in environ.get(name, "").split(","):
        item = raw.strip()
        if not item:
            continue
        alias, separator, value = item.partition("=")
        alias, value = (alias.strip(), value.strip()) if separator else (None, alias.strip())
        if alias is not None and not MODEL_NAME.fullmatch(alias):
            raise ValueError(f"{name}: invalid model name {alias!r}")
        if not pattern.fullmatch(value):
            raise ValueError(f"{name}: invalid entry {value!r}")
        items.append((alias, value))
    return items


def require(environ: Mapping[str, str], provider: str, names: tuple[str, ...]) -> None:
    missing = [name for name in names if not environ.get(name, "").strip()]
    if missing:
        raise ValueError(f"{provider} models are set but these variables are missing: {', '.join(missing)}")


def require_location(provider: str, name: str, value: str, allowed: frozenset[str], region: str) -> None:
    if value not in allowed:
        raise ValueError(
            f"{name}={value!r} can process {provider} requests outside {REGION_ENV}={region}; "
            f"use one of: {', '.join(sorted(allowed))}"
        )


def bedrock_models(environ: Mapping[str, str], region: str, policy: RegionPolicy) -> list[dict[str, Any]]:
    models = parse_list(environ, "BEDROCK_MODELS", BEDROCK_MODEL_ID)
    if not models:
        return []
    require(environ, "Bedrock", ("BEDROCK_AWS_REGION", "BEDROCK_ACCESS_KEY_ID", "BEDROCK_SECRET_ACCESS_KEY"))
    source = environ["BEDROCK_AWS_REGION"].strip()
    require_location("Bedrock", "BEDROCK_AWS_REGION", source, policy.bedrock_source_regions, region)
    entries = []
    for alias, model_id in models:
        if model_id.startswith(BEDROCK_ROUTING_PREFIXES):
            raise ValueError(
                f"BEDROCK_MODELS: give {model_id!r} without its routing prefix; "
                f"the {region!r} inference profile is added for you"
            )
        entries.append({
            "model_name": alias or model_id.split(".", 1)[1],
            "litellm_params": {
                "model": f"bedrock/converse/{policy.bedrock_profile}.{model_id}",
                "aws_region_name": source,
                "aws_access_key_id": "os.environ/BEDROCK_ACCESS_KEY_ID",
                "aws_secret_access_key": "os.environ/BEDROCK_SECRET_ACCESS_KEY",
            },
        })
    return entries


def azure_models(environ: Mapping[str, str], region: str, policy: RegionPolicy) -> list[dict[str, Any]]:
    deployments = parse_list(environ, "AZURE_OPENAI_DEPLOYMENTS", AZURE_DEPLOYMENT)
    if not deployments:
        return []
    require(environ, "Azure OpenAI", ("AZURE_OPENAI_REGION", "AZURE_OPENAI_API_BASE", "AZURE_OPENAI_API_KEY"))
    resource_region = environ["AZURE_OPENAI_REGION"].strip().lower()
    require_location("Azure OpenAI", "AZURE_OPENAI_REGION", resource_region, policy.azure_regions, region)
    if not environ["AZURE_OPENAI_API_BASE"].strip().startswith("https://"):
        raise ValueError("AZURE_OPENAI_API_BASE must be an https:// URL")
    api_version = "os.environ/AZURE_OPENAI_API_VERSION" if environ.get("AZURE_OPENAI_API_VERSION", "").strip() else DEFAULT_AZURE_API_VERSION
    return [
        {
            "model_name": alias or deployment,
            "litellm_params": {
                "model": f"azure/{deployment}",
                "api_base": "os.environ/AZURE_OPENAI_API_BASE",
                "api_key": "os.environ/AZURE_OPENAI_API_KEY",
                "api_version": api_version,
            },
        }
        for alias, deployment in deployments
    ]


def vertex_models(
    environ: Mapping[str, str], region: str, policy: RegionPolicy, secrets_dir: PurePosixPath
) -> list[dict[str, Any]]:
    models = parse_list(environ, "VERTEX_MODELS", VERTEX_MODEL_ID)
    if not models:
        return []
    require(environ, "Vertex AI", ("VERTEX_LOCATION", "VERTEX_PROJECT", "VERTEX_CREDENTIALS_FILE"))
    location = environ["VERTEX_LOCATION"].strip().lower()
    require_location("Vertex AI", "VERTEX_LOCATION", location, policy.vertex_locations, region)
    file_name = environ["VERTEX_CREDENTIALS_FILE"].strip()
    if not CREDENTIALS_FILE_NAME.fullmatch(file_name):
        raise ValueError("VERTEX_CREDENTIALS_FILE must be a file name inside PROVIDER_SECRETS_DIR, not a path")
    credentials = str(secrets_dir / file_name)
    return [
        {
            "model_name": alias or model_id,
            "litellm_params": {
                "model": f"vertex_ai/{model_id}",
                "vertex_location": location,
                "vertex_project": "os.environ/VERTEX_PROJECT",
                "vertex_credentials": credentials,
            },
        }
        for alias, model_id in models
    ]


def coding_router(environ: Mapping[str, str], available: set[str]) -> list[dict[str, Any]]:
    """Return the coding-router entry when CODING_ROUTER_DEFAULT_MODEL is set."""
    default = environ.get("CODING_ROUTER_DEFAULT_MODEL", "").strip()
    tier_variables = {tier: f"CODING_ROUTER_{tier}_MODEL" for tier in TIERS}
    if not default:
        stray = [name for name in tier_variables.values() if environ.get(name, "").strip()]
        if stray:
            raise ValueError(f"{', '.join(stray)} need CODING_ROUTER_DEFAULT_MODEL")
        return []
    tiers = {tier: environ.get(name, "").strip() or default for tier, name in tier_variables.items()}
    for name, model in (("CODING_ROUTER_DEFAULT_MODEL", default), *((tier_variables[t], m) for t, m in tiers.items())):
        if model not in available:
            raise ValueError(f"{name}={model!r} is not a configured provider model")
    return [{
        "model_name": "coding-router",
        "litellm_params": {
            "model": "auto_router/complexity_router",
            "complexity_router_default_model": default,
            "complexity_router_config": {
                "classifier_type": "oss_classifier",
                "opensource_classifier_config": {"deployment_name": "d3-mini", "timeout_ms": 15000},
                "tiers": tiers,
                "classifier_fallback": "default_model",
            },
        },
    }]


def sovereign_model_list(
    environ: Mapping[str, str], secrets_dir: PurePosixPath = SECRETS_DIR
) -> tuple[str, list[dict[str, Any]]]:
    """Return (region, generated model entries) or raise ValueError."""
    region, policy = region_policy(environ)
    entries = [
        *bedrock_models(environ, region, policy),
        *azure_models(environ, region, policy),
        *vertex_models(environ, region, policy, secrets_dir),
    ]
    names = [entry["model_name"] for entry in entries]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"model names are configured twice: {', '.join(duplicates)}; set model-name=ID aliases")
    reserved = sorted(RESERVED_MODEL_NAMES.intersection(names))
    if reserved:
        raise ValueError(f"model names are reserved by the stack: {', '.join(reserved)}")
    return region, [*entries, *coding_router(environ, set(names))]
