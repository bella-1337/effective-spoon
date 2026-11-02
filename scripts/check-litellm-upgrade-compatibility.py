#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Assess an immutable LiteLLM image without altering the live stack."""

from __future__ import annotations

import json
import os
import re
import signal
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from maintenance_common import (
    ROOT,
    ScriptError,
    compose,
    fail,
    require_docker,
    run,
    run_main,
)
from maintenance_contracts import load_compose_config

COMPOSE_FILE = ROOT / "docker-compose.yml"
LITELLM_DIR = ROOT / "litellm"
MODULES = LITELLM_DIR / "modules"
PROD = LITELLM_DIR / "config.prod.yaml"
DEV = LITELLM_DIR / "config.dev.yaml"
SEARCH = MODULES / "search.yaml"
GUARDRAILS = MODULES / "guardrails" / "presidio.yaml"
POLICIES = MODULES / "policies" / "eu-data-protection.yaml"
ATTRIBUTION = MODULES / "langfuse_user_attribution.py"
PROMETHEUS = LITELLM_DIR / "prometheus.yml"
IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]+)?(/[A-Za-z0-9._-]+)+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$")

USAGE = """Usage:
  scripts/check-litellm-upgrade-compatibility.py --candidate-image IMAGE_REF [--pull]

Assesses a candidate LiteLLM proxy image against this repository's
docker-compose.yml, litellm/config.prod.yaml, litellm/config.dev.yaml, and
litellm/modules/* before it is pinned for a real upgrade. Every check is
read-only: the live stack's services and databases are never started,
stopped, or modified, and no secret from .env is read or printed.

Required:
  --candidate-image IMAGE_REF  Fully pinned image reference to assess, in
                               registry/repository:tag@sha256:<64-hex-digest>
                               form. Floating tags without a digest are
                               rejected because they are not immutable.

Options:
  --pull       Allow a read-only 'docker pull' of IMAGE_REF if it is not
               already present locally. Without this flag, a missing image
               is reported as a failed check instead of being fetched.
  -h, --help   Show this help text.

Exit status:
  0  every check passed
  1  one or more checks failed
  2  usage error or an unsafe/incomplete environment (fails closed)
"""


class Reporter:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def pass_(self, message: str) -> None:
        print(f"PASS: {message}")
        self.passed += 1

    def fail_(self, message: str, detail: str = "") -> None:
        print(f"FAIL: {message}", file=sys.stderr)
        if detail:
            print(f"      {detail}", file=sys.stderr)
        self.failed += 1

    def note(self, message: str) -> None:
        print(f"NOTE: {message}")


def usage(stream: object = sys.stdout) -> None:
    print(USAGE, file=stream, end="")


@lru_cache(maxsize=1)
def compose_model() -> dict[str, Any]:
    """Rendered Compose model with every profile, resolved from the example env files.

    Reading the merged model instead of docker-compose.yml text keeps these
    checks independent of file layout, includes, and YAML anchors.
    """
    return load_compose_config(ROOT / ".env.example", ROOT / ".env.optional.example")


def service(name: str) -> dict[str, Any]:
    services = compose_model().get("services", {})
    value = services.get(name, {}) if isinstance(services, dict) else {}
    return value if isinstance(value, dict) else {}


def service_image(name: str) -> str:
    return str(service(name).get("image", ""))


def service_environment(name: str) -> dict[str, str]:
    environment = service(name).get("environment") or {}
    if not isinstance(environment, dict):
        return {}
    return {key: "" if value is None else str(value) for key, value in environment.items()}


def mounts_read_only(name: str, target: str) -> bool:
    return any(
        isinstance(volume, dict) and volume.get("target") == target and volume.get("read_only") is True
        for volume in service(name).get("volumes") or []
    )


def routes_host(name: str, host: str) -> bool:
    labels = service(name).get("labels") or {}
    return bool(host) and isinstance(labels, dict) and f"Host(`{host}`)" in labels.values()


def contains_exact(text: str, value: str) -> bool:
    return bool(re.search(rf"(^|[^A-Za-z0-9_.-]){re.escape(value)}([^A-Za-z0-9_.-]|$)", text))


def static_checks(report: Reporter) -> None:
    search_text, guard_text, policy_text = (path.read_text(encoding="utf-8") for path in (SEARCH, GUARDRAILS, POLICIES))
    prod_text, dev_text = PROD.read_text(encoding="utf-8"), DEV.read_text(encoding="utf-8")
    search_name = next((line.split(":", 1)[1].strip() for line in search_text.splitlines() if "search_tool_name:" in line), "")
    if search_name and "search_tools:" in search_text and all(re.search(r"^\s*-\s*modules/search\.yaml\s*$", text, re.M) for text in (prod_text, dev_text)):
        report.pass_("Modular search module (modules/search.yaml) is declared and included by both profiles")
    else:
        report.fail_("Modular search module (modules/search.yaml) is declared and included by both profiles")
    guard_name = next((line.split(":", 1)[1].strip() for line in guard_text.splitlines() if "guardrail_name:" in line), "")
    policy_name = next((line.strip().rstrip(":") for line in policy_text.splitlines() if re.fullmatch(r"  [A-Za-z0-9_-]+:", line)), "")
    policy_entries = {
        match.group(1)
        for match in re.finditer(r"^\s*-\s*([A-Za-z0-9_.-]+)\s*$", policy_text, re.M)
    }
    guard_ok = all((
        bool(guard_name), bool(policy_name), "guardrails:" in guard_text, "policies:" in policy_text,
        "policy_attachments:" in policy_text, guard_name in policy_entries,
        *[re.search(fr"^\s*-\s*modules/{module}\s*$", text, re.M) is not None
          for text in (prod_text, dev_text)
          for module in ("guardrails/presidio.yaml", "policies/eu-data-protection.yaml")],
    ))
    if guard_ok:
        report.pass_(f"Guardrail and policy modules are declared, cross-referenced ('{guard_name}' in policy '{policy_name}'), and included by both profiles")
    else:
        report.fail_("Guardrail and policy modules are declared, cross-referenced, and included by both profiles")
    vector_section = re.search(r"^vector_store_registry:\n(?P<body>(?:^(?!\S).*$\n?)*)", dev_text, re.M)
    vector_ok = (
        vector_section is not None
        and "custom_llm_provider: pg_vector" in dev_text
        and "vector_store_registry:" not in prod_text
        and not any(
            re.match(r"\s*api_(key|base):", line) and "os.environ/" not in line
            for line in vector_section.group("body").splitlines()
        )
    )
    (report.pass_ if vector_ok else report.fail_)("Vector-store registry is declared for development only and uses environment-sourced credentials")
    semantic_cache_ok = all(
        re.search(pattern, dev_text, re.M) is not None
        for pattern in (
            r"^\s*type:\s*qdrant-semantic\s*$",
            r"^\s*mode:\s*default_off\s*$",
            r"^\s*semantic_cache_scope:\s*end_user\s*$",
            r"^\s*qdrant_api_base:\s*os\.environ/QDRANT_API_BASE\s*$",
            r"^\s*qdrant_api_key:\s*os\.environ/QDRANT_API_KEY\s*$",
        )
    ) and re.search(r"^\s*type:\s*qdrant-semantic\s*$", prod_text, re.M) is None
    (report.pass_ if semantic_cache_ok else report.fail_)(
        "Qdrant semantic caching is development-only, opt-in, end-user scoped, and uses environment-sourced credentials"
    )
    prometheus = PROMETHEUS.read_text(encoding="utf-8")
    compose_text = json.dumps(compose_model())
    decisions_router_ok = all(
        item in text
        for text in (prod_text, dev_text)
        for item in (
            "model_name: d3-mini",
            "model: hosted_vllm/vllm-sr/d3-mini",
            "api_base: os.environ/D3_DECISION_API_BASE",
            "api_key: os.environ/D3_DECISION_API_KEY",
            "mode: decisions",
            "model_name: coding-router",
            "model: auto_router/complexity_router",
            "deployment_name: d3-mini",
            "classifier_fallback: default_model",
        )
    ) and all(
        stale not in compose_text + prometheus + dev_text + prod_text
        for stale in (
            "vllm-semantic-router-envoy",
            "semantic-fast",
            "semantic-reasoning",
            "VLLM_SR_HOST",
        )
    ) and all(
        mounts_read_only(proxy, "/etc/ssl/certs/d3-decision-ca-bundle.pem")
        and service_environment(proxy).get("SSL_CERT_FILE") == "/etc/ssl/certs/d3-decision-ca-bundle.pem"
        for proxy in ("litellm-prod", "litellm-dev")
    )
    (report.pass_ if decisions_router_ok else report.fail_)(
        "Both profiles use the authenticated remote D3 Decisions deployment through LiteLLM Auto Router"
    )
    required_settings = (
        r"^\s*disable_spend_logs:\s*false\s*$", r"^\s*store_prompts_in_spend_logs:\s*false\s*$",
        r"^\s*maximum_spend_logs_retention_period:\s*180d\s*$",
        r"^\s*maximum_health_check_retention_period:\s*7d\s*$",
        r"^\s*turn_off_message_logging:\s*true\s*$", r"^\s*store_audit_logs:\s*true\s*$",
        r"^\s*callbacks:\s*$", r"^\s*-\s*modules\.langfuse_user_attribution\.proxy_handler_instance\s*$",
        r"^\s*-\s*langfuse_otel\s*$",
    )
    attribution = ATTRIBUTION.read_text(encoding="utf-8")
    retention_ok = all(
        re.search(pattern, config, re.M) is not None
        for config in (prod_text, dev_text)
        for pattern in required_settings
    ) and all(
        all(
            (
                environment.get("LITELLM_OTEL_BAGGAGE_PROMOTED_KEYS") == "",
                environment.get("LITELLM_OTEL_BAGGAGE_METADATA_KEYS") == "",
                environment.get("LITELLM_OTEL_BAGGAGE_TEAM_METADATA_KEYS") == "",
                environment.get("LITELLM_OTEL_V2") == "true",
                environment.get("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS") == ".*",
            )
        )
        for environment in (service_environment("litellm-prod"), service_environment("litellm-dev"))
    ) and all(
        item in attribution
        for item in (
            'metadata.pop("trace_user_id", None)', 'litellm_metadata["trace_user_id"]',
            "user_api_key_dict.user_id", "LANGFUSE_USER_ID_SALT",
            'key.lower().startswith("langfuse_")',
        )
    )
    (report.pass_ if retention_ok else report.fail_)("Privacy, retention, and authenticated pseudonymous Langfuse attribution settings match both profiles")
    expected_profiles = {
        "litellm-prod": ["prod"],
        "litellm-dev": ["dev"],
        "litellm-pgvector": ["dev"],
        "litellm-demo-data-prod": ["demo"],
        "bge-m3-vllm": ["dev"],
    }
    prod_env, dev_env = service_environment("litellm-prod"), service_environment("litellm-dev")
    prod_text_model, dev_text_model = json.dumps(service("litellm-prod")), json.dumps(service("litellm-dev"))
    prod_host = prod_env.get("PROXY_BASE_URL", "").removeprefix("https://")
    dev_host = dev_env.get("PROXY_BASE_URL", "").removeprefix("https://")
    isolation_ok = all(service(name) for name in expected_profiles) and all((
        *(service(name).get("profiles") == profiles for name, profiles in expected_profiles.items()),
        "litellm_prod" in prod_env.get("DATABASE_URL", ""), "litellm_dev" in dev_env.get("DATABASE_URL", ""),
        routes_host("litellm-prod", prod_host), routes_host("litellm-dev", dev_host), prod_host != dev_host,
        "litellm_dev" not in prod_text_model, "litellm_prod" not in dev_text_model,
        "hosted_vllm/bge-m3" not in prod_text,
        "test-model" not in prod_text,
        not service("bge-m3-vllm").get("ports"),
        "vllm-semantic-router" not in compose_text,
        "envoyproxy/envoy" not in compose_text,
    ))
    (report.pass_ if isolation_ok else report.fail_)("Development and production services, databases, hostnames, and model lists remain isolated in the rendered Compose model")


def docker_run_validation(image: str, config: Path, network: str, extra_environment: list[str]) -> tuple[int, str]:
    name = f"litellm-upgrade-check-{os.getpid()}-{time.time_ns()}"
    args = [
        "docker", "run", "-d", "--name", name, "--network", network, "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--pids-limit", "256", "--memory", "512m",
        "--tmpfs", "/tmp:mode=1777", "-v", f"{config}:/app/config.yaml:ro",
        "-v", f"{MODULES}:/app/modules:ro",
        "-e", "DATABASE_URL=postgresql://upgrade-check-placeholder@127.0.0.1:5432/upgrade-check",
        "-e", "LITELLM_MASTER_KEY=sk-upgrade-check-placeholder",
        "-e", "OPENAI_API_KEY=sk-upgrade-check-placeholder",
        "-e", "LITELLM_LOCAL_MODEL_COST_MAP=True", *extra_environment, "--entrypoint", "litellm", image,
        "--config", "/app/config.yaml", "--validate_config",
    ]
    if run(args, check=False).returncode:
        return 125, "candidate validation container could not start"
    output = ""
    try:
        for _ in range(121):
            logs = run(["docker", "logs", name], capture=True, check=False)
            output = logs.stdout + logs.stderr
            running = run(["docker", "inspect", "-f", "{{.State.Running}}", name], capture=True, check=False).stdout.strip()
            if running != "true":
                status = run(["docker", "inspect", "-f", "{{.State.ExitCode}}", name], capture=True, check=False).stdout.strip()
                if not status.isdigit():
                    return 125, output
                exit_code = int(status)
                if exit_code != 0 or "LiteLLM: config OK" not in output:
                    return exit_code or 125, output
                return 0, output
            if re.search(r"LiteLLM: config OK|LiteLLM: Proxy initialized with Config, Set models:", output):
                return 0, output
            time.sleep(1)
        return 124, output
    finally:
        run(["docker", "rm", "-f", name], check=False, capture=True)


def start_validation_mcp_server(network: str) -> str:
    """Start the digest-pinned Xberg dependency on the isolated network."""
    xberg_image = service_image("xberg")
    if not re.search(r"@sha256:[0-9a-f]{64}$", xberg_image):
        fail("xberg must use a digest-pinned image before candidate validation", 2)
    name = f"litellm-upgrade-check-xberg-{os.getpid()}-{time.time_ns()}"
    command = [
        "docker", "run", "-d", "--name", name, "--network", network, "--network-alias", "xberg",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--pids-limit", "256", "--memory", "1g", "--tmpfs", "/tmp:mode=1777", xberg_image,
        "mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8001",
        "--allowed-host", "xberg", "--allowed-host", "xberg:8001",
    ]
    if run(command, check=False).returncode:
        fail("could not start the isolated Xberg MCP validator", 2)
    for _ in range(10):
        result = run(["docker", "logs", name], capture=True, check=False)
        if "Starting MCP HTTP server on http://0.0.0.0:8001" in (
            result.stdout + result.stderr
        ):
            return name
        time.sleep(1)
    run(["docker", "rm", "-f", name], capture=True, check=False)
    fail("isolated Xberg MCP validator did not become ready", 2)


def start_validation_qdrant(network: str) -> str:
    """Start the digest-pinned Qdrant dependency on the isolated network."""
    qdrant_image = service_image("qdrant")
    if not re.search(r"@sha256:[0-9a-f]{64}$", qdrant_image):
        fail("qdrant must use a digest-pinned image before candidate validation", 2)
    name = f"litellm-upgrade-check-qdrant-{os.getpid()}-{time.time_ns()}"
    command = [
        "docker", "run", "-d", "--name", name, "--network", network,
        "--network-alias", "qdrant", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--pids-limit", "256",
        "--memory", "512m", "--tmpfs", "/qdrant/storage:rw,noexec,nosuid,size=256m",
        "-e", "QDRANT__SERVICE__API_KEY=upgrade-check-placeholder",
        "-e", "QDRANT__TELEMETRY_DISABLED=true", qdrant_image,
    ]
    if run(command, check=False).returncode:
        fail("could not start the isolated Qdrant cache validator", 2)
    for _ in range(20):
        result = run(
            ["docker", "exec", name, "bash", "-ec", ": >/dev/tcp/127.0.0.1/6333"],
            capture=True,
            check=False,
        )
        if result.returncode == 0:
            return name
        time.sleep(1)
    run(["docker", "rm", "-f", name], capture=True, check=False)
    fail("isolated Qdrant cache validator did not become ready", 2)


def identifier_counts(image: str, identifiers: list[str]) -> dict[str, int]:
    program = (
        "import importlib.util,pathlib,sys\n"
        "spec=importlib.util.find_spec('litellm')\n"
        "locations=list(spec.submodule_search_locations or ()) if spec else []\n"
        "root=pathlib.Path(locations[0]) if locations else None\n"
        "if root is None:\n"
        " roots=[p for p in pathlib.Path('/app').rglob('litellm') if p.is_dir()]\n"
        " root=next((p for p in roots if 'site-packages' in p.parts), next(iter(roots), None))\n"
        "for term in sys.argv[1:]:\n"
        " count=sum(1 for p in root.rglob('*') if p.is_file() and term in p.read_text(errors='ignore')) if root else 0\n"
        " print(f'{term}:{count}')\n"
    )
    name = f"litellm-upgrade-check-identifiers-{os.getpid()}-{time.time_ns()}"
    try:
        result = run(["docker", "run", "--rm", "--name", name, "--network", "none", "--read-only",
                      "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--pids-limit", "256",
                      "--memory", "512m", "--tmpfs", "/tmp:mode=1777", "--entrypoint", "python", image,
                      "-c", program, *identifiers], capture=True, check=False)
    finally:
        run(["docker", "rm", "-f", name], capture=True, check=False)
    if result.returncode:
        return {item: 0 for item in identifiers}
    counts: dict[str, int] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition(":")
        if separator and value.isdigit():
            counts[key] = int(value)
    return counts


ATTRIBUTION_CHECK = r'''
import asyncio
import importlib.util
from datetime import datetime
from types import SimpleNamespace

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.util.http import get_excluded_urls

from litellm.integrations.otel.logger import build_otel_v2_logger
from litellm.integrations.otel.model.config import OpenTelemetryV2Config
from litellm.litellm_core_utils.litellm_logging import create_dummy_standard_logging_payload
from litellm.integrations.prometheus import PrometheusLogger

spec = importlib.util.spec_from_file_location(
    "langfuse_user_attribution",
    "/upgrade-check/langfuse_user_attribution.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
prometheus_logger = PrometheusLogger()
exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
otel_logger = build_otel_v2_logger(
    OpenTelemetryV2Config(
        exporters=[],
        mapper_names=["genai", "langfuse"],
        baggage_promoted_keys=[],
        baggage_metadata_keys=[],
        baggage_team_metadata_keys=[],
        capture_message_content="no_content",
    ),
    callback_name="langfuse_otel",
    tracer_provider=provider,
)
auth = SimpleNamespace(
    user_id="raw-user@example.test",
    api_key="raw-key-hash",
    key_alias="raw-key-alias",
    org_id="raw-org",
    end_user_id="raw-end-user",
    team_id="raw-team",
    team_alias="Raw Team",
    team_metadata={"owner": "raw-user@example.test"},
)

async def apply_hook(data, call_type):
    return await module.proxy_handler_instance.async_pre_call_hook(
        auth,
        None,
        data,
        call_type,
    )

def export_v2_span(call_id, result, call_type):
    request = dict(result)
    request["litellm_call_id"] = call_id
    request["model"] = "upgrade-check"
    otel_logger.seed_request_identity(
        auth,
        model="upgrade-check",
        request_metadata=result.get("litellm_metadata") or result.get("metadata"),
    )
    asyncio.run(otel_logger.async_pre_call_hook(auth, None, request, call_type))

    payload = create_dummy_standard_logging_payload()
    payload["litellm_call_id"] = call_id
    payload["model"] = "upgrade-check"
    payload["metadata"].update(
        {
            "user_api_key_user_id": "raw-user@example.test",
            "user_api_key_user_email": "raw-user@example.test",
            "user_api_key_alias": "raw-key-alias",
            "user_api_key_hash": "raw-key-hash",
            "user_api_key_team_id": "raw-team",
            "user_api_key_team_alias": "Raw Team",
            "requester_ip_address": "192.0.2.1",
        }
    )
    params = {
        "metadata": result["metadata"],
        "proxy_server_request": result["proxy_server_request"],
    }
    if "litellm_metadata" in result:
        params["litellm_metadata"] = result["litellm_metadata"]
    kwargs = {
        "litellm_call_id": call_id,
        "call_type": call_type,
        "model": "upgrade-check",
        "api_call_start_time": datetime.now(),
        "standard_logging_object": payload,
        "litellm_params": params,
        "messages": [],
    }
    now = datetime.now()
    asyncio.run(otel_logger.async_log_success_event(kwargs, {}, now, now))
    asyncio.run(prometheus_logger.async_log_success_event(kwargs, {}, now, now))
    assert payload["metadata"]["user_api_key_team_id"] == "raw-team"

headers = {
    "langfuse_trace_user_id": "spoofed-user",
    "Langfuse_existing_trace_id": "spoofed-trace",
    "x-request-id": "preserved",
}
result = asyncio.run(
    apply_hook(
        {
            "metadata": {
                "trace_user_id": "caller-metadata",
                "session_id": "caller-session",
            },
            "user": "caller-user",
            "proxy_server_request": {"headers": headers},
        },
        "completion",
    )
)
assert result["metadata"]["trace_user_id"].startswith("litellm-user-")
assert result["metadata"]["trace_user_id"] not in {"caller-metadata", "spoofed-user"}
assert result["proxy_server_request"]["headers"] == {"x-request-id": "preserved"}
assert result["user"].startswith("litellm-user-")
assert result["metadata"]["session_id"].startswith("litellm-session-")
assert result["metadata"]["session_id"] != "caller-session"
export_v2_span("upgrade-check-completion", result, "completion")

result = asyncio.run(
    apply_hook(
        {
            "metadata": {
                "trace_user_id": "provider-metadata-spoof",
                "provider-field": "preserved",
            },
            "litellm_metadata": {
                "trace_user_id": "litellm-metadata-spoof",
                "conversation_id": "agent-conversation",
                "logging-field": "preserved",
            },
            "user": "caller-user",
            "proxy_server_request": {"headers": headers},
        },
        "responses",
    )
)
assert result["litellm_metadata"]["trace_user_id"].startswith("litellm-user-")
assert result["litellm_metadata"]["trace_user_id"] != "litellm-metadata-spoof"
assert result["litellm_metadata"]["logging-field"] == "preserved"
assert result["litellm_metadata"]["session_id"].startswith("litellm-session-")
assert "conversation_id" not in result["litellm_metadata"]
assert "trace_user_id" not in result["metadata"]
assert result["metadata"]["provider-field"] == "preserved"
assert result["proxy_server_request"]["headers"] == {"x-request-id": "preserved"}
assert result["user"].startswith("litellm-user-")
export_v2_span("upgrade-check-responses", result, "responses")

provider.force_flush()
spans = [
    dict(span.attributes)
    for span in exporter.get_finished_spans()
    if span.attributes.get("langfuse.observation.type") == "generation"
]
assert len(spans) == 2
for attributes in spans:
    assert attributes["user.id"].startswith("litellm-user-")
    assert attributes["session.id"].startswith("litellm-session-")
    assert attributes["user.id"] not in {
        "raw-user@example.test",
        "raw-end-user",
        "caller-user",
        "spoofed-user",
    }
    for forbidden_key in (
        "litellm.api_key.hash",
        "litellm.metadata.requester_ip_address",
        "litellm.metadata.user_api_key_alias",
        "litellm.metadata.user_api_key_end_user_id",
        "litellm.metadata.user_api_key_org_id",
        "litellm.metadata.user_api_key_user_id",
        "litellm.team.alias",
        "litellm.team.id",
    ):
        assert forbidden_key not in attributes
    serialized = repr(attributes)
    assert "raw-user@example.test" not in serialized
    assert "raw-key-hash" not in serialized
    assert "raw-key-alias" not in serialized
    assert "192.0.2.1" not in serialized
    assert "caller-session" not in serialized
    assert "agent-conversation" not in serialized

excluded_urls = get_excluded_urls("FASTAPI")
assert excluded_urls.url_disabled("https://llm.example.test/key/info?key=raw-secret")
'''


def check_langfuse_attribution(image: str) -> bool:
    """Run the trusted attribution and session exporter contract in a sealed container."""
    name = f"litellm-upgrade-check-attribution-{os.getpid()}-{time.time_ns()}"
    try:
        result = run(
            [
                "docker", "run", "--rm", "-i", "--name", name, "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--pids-limit", "256",
                "--memory", "512m", "--tmpfs", "/tmp:mode=1777",
                "--env", "LANGFUSE_USER_ID_SALT=upgrade-check-placeholder",
                "--env", "LITELLM_OTEL_BAGGAGE_PROMOTED_KEYS=",
                "--env", "LITELLM_OTEL_BAGGAGE_METADATA_KEYS=",
                "--env", "LITELLM_OTEL_BAGGAGE_TEAM_METADATA_KEYS=",
                "--env", "OTEL_PYTHON_FASTAPI_EXCLUDED_URLS=.*",
                "--volume", f"{ATTRIBUTION}:/upgrade-check/langfuse_user_attribution.py:ro",
                "--entrypoint", "python", image, "-",
            ],
            input_data=ATTRIBUTION_CHECK,
            capture=True,
            check=False,
            timeout=120,
        )
        return result.returncode == 0
    finally:
        run(["docker", "rm", "-f", name], capture=True, check=False)


def main() -> None:
    candidate = ""
    pull = False
    args = sys.argv[1:]
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--candidate-image":
            if index + 1 >= len(args) or not args[index + 1]:
                fail("--candidate-image requires a value", 2)
            candidate, index = args[index + 1], index + 2
        elif arg.startswith("--candidate-image="):
            candidate, index = arg.partition("=")[2], index + 1
        elif arg == "--pull":
            pull, index = True, index + 1
        elif arg in {"-h", "--help"}:
            usage()
            return
        else:
            usage(sys.stderr)
            fail(f"unknown argument: {arg}", 2)
    if not candidate:
        usage(sys.stderr)
        fail("--candidate-image is required", 2)
    try:
        require_docker(daemon=True)
    except ScriptError as error:
        fail(str(error), 2)
    for path in (COMPOSE_FILE, PROD, DEV, SEARCH, GUARDRAILS, POLICIES, ATTRIBUTION):
        if not path.is_file() or path.is_symlink():
            fail(f"required repository file is missing: {path}", 2)
    if compose(["--env-file", str(ROOT / ".env.example"), "config", "--quiet"], check=False).returncode:
        fail("docker-compose.yml does not pass static validation with .env.example", 2)
    report = Reporter()
    print("== Candidate image ==")
    print(candidate)
    try:
        compose_model()
    except (ScriptError, ValueError) as error:
        fail(f"could not render the Compose model: {error}", 2)
    current_prod = service_image("litellm-prod")
    current_dev = service_image("litellm-dev")
    print(f"Currently pinned production image: {current_prod}")
    print(f"Currently pinned development image: {current_dev}")
    if candidate == current_prod:
        report.note("candidate image matches the production pin; this is a re-verification for that profile")
    if candidate == current_dev:
        report.note("candidate image matches the development pin; this is a re-verification for that profile")
    print()
    print("== Repository configuration checks ==")
    static_checks(report)
    print("\n== Candidate image compatibility checks ==")
    valid = bool(IMAGE_PATTERN.fullmatch(candidate))
    if valid:
        report.pass_("Candidate image reference is pinned to an immutable digest")
    else:
        report.fail_(
            "Candidate image reference is pinned to an immutable digest",
            "expected registry/repository:tag@sha256:<64-hex-digest>; "
            f"got: {candidate} (a floating tag alone is not reproducible and must be rejected)",
        )
    available = False
    if valid and run(["docker", "image", "inspect", candidate], check=False, capture=True).returncode == 0:
        report.pass_("Candidate image is available locally")
        available = True
    elif valid and pull:
        print("Candidate image is not present locally; pulling read-only (no service is started)...")
        if run(["docker", "pull", candidate], check=False).returncode == 0:
            report.pass_("Candidate image was retrieved read-only via 'docker pull'")
            available = True
        else:
            report.fail_(
                "Candidate image is available locally",
                f"'docker pull {candidate}' failed; verify registry access, authentication, and that the digest exists",
            )
    elif valid:
        report.fail_(
            "Candidate image is available locally",
            f"image not present locally; re-run with --pull to fetch it read-only, or run 'docker pull {candidate}' yourself first",
        )
    else:
        report.fail_("Candidate image is available locally", "skipped: the image reference failed immutability validation")
    if available:
        network = f"litellm-upgrade-check-{os.getpid()}-{time.time_ns()}"
        if run(["docker", "network", "create", "--internal", network], check=False).returncode:
            fail("could not create the isolated candidate-validation network", 2)
        xberg_container = ""
        qdrant_container = ""
        previous_handlers = {
            signal.SIGINT: signal.getsignal(signal.SIGINT),
            signal.SIGTERM: signal.getsignal(signal.SIGTERM),
            signal.SIGHUP: signal.getsignal(signal.SIGHUP),
        }

        def terminate(signum: int, _frame: object) -> None:
            raise SystemExit({signal.SIGINT: 130, signal.SIGTERM: 143, signal.SIGHUP: 129}[signum])

        for sig in previous_handlers:
            signal.signal(sig, terminate)
        try:
            xberg_container = start_validation_mcp_server(network)
            qdrant_container = start_validation_qdrant(network)
            decision_environment = [
                "-e", "D3_DECISION_API_BASE=https://d3-mini.example.test:8443",
                "-e", "D3_DECISION_API_KEY=upgrade-check-placeholder-key",
            ]
            prod_code, prod_output = docker_run_validation(
                candidate, PROD, network, decision_environment
            )
            dev_code, dev_output = docker_run_validation(candidate, DEV, network,
                                                          [*decision_environment,
                                                           "-e", "PG_VECTOR_API_BASE=http://upgrade-check-placeholder:8000",
                                                           "-e", "PG_VECTOR_API_KEY=upgrade-check-placeholder",
                                                           "-e", "QDRANT_API_BASE=http://qdrant:6333",
                                                           "-e", "QDRANT_API_KEY=upgrade-check-placeholder"])
        finally:
            if xberg_container:
                run(["docker", "rm", "-f", xberg_container], check=False, capture=True)
            if qdrant_container:
                run(["docker", "rm", "-f", qdrant_container], check=False, capture=True)
            run(["docker", "network", "rm", network], check=False, capture=True)
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
        success_output = re.compile(r"LiteLLM: config OK|LiteLLM: Proxy initialized with Config, Set models:")
        if prod_code == 0 and success_output.search(prod_output):
            report.pass_("Candidate image loads litellm/config.prod.yaml without error")
        else:
            report.fail_(
                "Candidate image loads litellm/config.prod.yaml without error",
                f"'litellm --validate_config' exited {prod_code}; last output line: "
                f"{prod_output.splitlines()[-1] if prod_output.splitlines() else ''}",
            )
        if dev_code == 0 and success_output.search(dev_output):
            report.pass_("Candidate image loads litellm/config.dev.yaml without error")
        else:
            report.fail_(
                "Candidate image loads litellm/config.dev.yaml without error",
                f"'litellm --validate_config' exited {dev_code}; last output line: "
                f"{dev_output.splitlines()[-1] if dev_output.splitlines() else ''}",
            )
        search_name = next((line.split(":", 1)[1].strip() for line in SEARCH.read_text().splitlines() if "search_tool_name:" in line), "")
        guard_name = next((line.split(":", 1)[1].strip() for line in GUARDRAILS.read_text().splitlines()
                           if "guardrail_name:" in line), "")
        policy_name = next((line.strip().rstrip(":") for line in POLICIES.read_text().splitlines()
                            if re.fullmatch(r"  [A-Za-z0-9_-]+:", line)), "")
        evidence = all(
            "Search Tools:" in output
            and (not search_name or contains_exact(output, search_name))
            and re.search(r"Policy Engine", output, re.I) is not None
            and (not policy_name or contains_exact(output, policy_name))
            and (not guard_name or contains_exact(output, guard_name))
            for output in (prod_output, dev_output)
        )
        if evidence:
            report.pass_("Candidate image's own output confirms it recognizes the modular search tool and the guardrail/policy modules in both profiles")
        else:
            report.fail_(
                "Candidate image's own output confirms it recognizes the modular search tool and the guardrail/policy modules in both profiles",
                f"expected 'Search Tools:' with '{search_name}' and 'Policy Engine' with policy "
                f"'{policy_name}' and guardrail '{guard_name}' in the validator output for both configs",
            )
        identifiers = ["pg_vector", "presidio", "searxng", "qdrant-semantic", "semantic_cache_scope",
                       "langfuse_otel", "maximum_spend_logs_retention_period",
                       "maximum_health_check_retention_period", "store_prompts_in_spend_logs", "disable_spend_logs",
                       "turn_off_message_logging", "store_audit_logs", "LITELLM_OTEL_V2",
                       "LITELLM_OTEL_BAGGAGE_PROMOTED_KEYS", "LITELLM_OTEL_BAGGAGE_METADATA_KEYS",
                       "LITELLM_OTEL_BAGGAGE_TEAM_METADATA_KEYS",
                       "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"]
        counts = identifier_counts(candidate, identifiers)
        identifiers_ok = True
        for identifier in identifiers:
            if counts.get(identifier, 0) == 0:
                identifiers_ok = False
                report.note(f"candidate image has no reference to '{identifier}' in its installed package")
        if identifiers_ok:
            report.pass_("Candidate image's installed package recognizes the provider, Langfuse OTel, and privacy/retention identifiers this repository depends on")
        else:
            report.fail_(
                "Candidate image's installed package recognizes the provider, Langfuse OTel, and privacy/retention identifiers this repository depends on",
                "one or more identifiers used by litellm/config.*.yaml and litellm/modules/* were not found "
                "anywhere in the candidate image's litellm package; it may silently ignore that setting or provider",
            )
        if check_langfuse_attribution(candidate):
            report.pass_("Candidate image preserves trusted pseudonymous Langfuse user and session attribution without exporting raw identifiers")
        else:
            report.fail_(
                "Candidate image preserves trusted pseudonymous Langfuse user and session attribution without exporting raw identifiers",
                "the attribution hook did not replace caller identity fields, strip langfuse_* headers and raw "
                "principal/session metadata, or remain compatible with the candidate image's Langfuse OTel exporter",
            )
        isolated = not any(
            item in prod_output
            for item in ("test-model", "bge-m3", "vector_store")
        )
        if isolated:
            report.pass_("Production and development candidate-image validations ran in fully isolated containers without leaking one profile's models into the other")
        else:
            report.fail_(
                "Production and development candidate-image validations ran in fully isolated containers without leaking one profile's models into the other",
                "the production config's validator output unexpectedly referenced development-only models or the vector store",
            )
    else:
        for message in (
            "Candidate image loads litellm/config.prod.yaml without error",
            "Candidate image loads litellm/config.dev.yaml without error",
            "Candidate image's own output confirms it recognizes the modular search tool and the guardrail/policy modules in both profiles",
            "Candidate image's installed package recognizes the provider, Langfuse OTel, and privacy/retention identifiers this repository depends on",
            "Candidate image preserves trusted pseudonymous Langfuse user and session attribution without exporting raw identifiers",
            "Production and development candidate-image validations ran in fully isolated containers without leaking one profile's models into the other",
        ):
            report.fail_(message, "skipped: candidate image is unavailable")
    print("\n== Summary ==")
    print(f"Passed: {report.passed}\nFailed: {report.failed}")
    if report.failed:
        print("Result: NOT compatible for upgrade. Resolve every FAIL above, then re-run this check before pinning the new digest in docker-compose.yml.")
        raise SystemExit(1)
    print("Result: compatible. Still review upstream release notes, back up the databases (scripts/backup-databases.py), update the pinned digest, and re-run scripts/verify-schema.sql and scripts/verify-pgvector.py after deploying.")


if __name__ == "__main__":
    run_main(main)
