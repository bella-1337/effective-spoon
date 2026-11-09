#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Operate the optional LiteLLM Lens profile."""

from __future__ import annotations

import json
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from maintenance_common import (
    LOG,
    ROOT,
    compose,
    compose_capture,
    env_file_is_present,
    fail,
    require_docker,
    run_main,
    write_private_file,
)

USAGE = """Usage: scripts/lens.py COMMAND [OPTIONS]

Operate the optional LiteLLM Lens profile (agent-trace analysis).

Commands:
  secrets [--env-file FILE] [--optional-env-file FILE]
      Generate every empty Lens secret: LITELLM_LENS_SERVICE_TOKEN and
      LENS_GATEWAY_SECRET in .env, LENS_ADMIN_TOKEN and
      LENS_CLICKHOUSE_PASSWORD in .env.optional. Existing values are never
      rotated or printed; the files are rewritten with mode 0600.

  status
      Check that Lens and its ClickHouse store are healthy, that Lens answers
      /health/ready, and that every running LiteLLM gateway is connected to
      Lens (or, without the lens profile, that no gateway tries to reach it).

  smoke
      Run status, then create a temporary tracing key, send one OTLP trace,
      read it back with the administrator token, and revoke the key. The
      trace stays in Lens under the agent name litellm-stack-smoke until its
      retention period ends.

  help
      Show this help text.
"""

LENS_SERVICE = "lens"
LENS_STORE = "lens-clickhouse"
GATEWAYS = ("litellm-prod", "litellm-dev")
SMOKE_AGENT = "litellm-stack-smoke"
SECRET_LENGTH = range(32, 513)
LENS_PUBLIC_CONTRACT = 1


# (variable, env file): core secrets are shared with the LiteLLM gateways.
SECRETS = (
    ("LITELLM_LENS_SERVICE_TOKEN", "core"),
    ("LENS_GATEWAY_SECRET", "core"),
    ("LENS_ADMIN_TOKEN", "optional"),
    ("LENS_CLICKHOUSE_PASSWORD", "optional"),
)
DISTINCT_SECRETS = ("LENS_ADMIN_TOKEN", "LITELLM_LENS_SERVICE_TOKEN", "LENS_GATEWAY_SECRET")
ENV_LINE_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")

# Runs inside the Lens container. Credentials reach curl through a private
# header file on the container's tmpfs, never through a process argument.
LENS_REQUEST_SCRIPT = r"""
set -eu
umask 077
headers=$(mktemp)
trap 'rm -f "$headers"' EXIT
method=$1
path=$2
auth=$3
case "$auth" in
  admin) printf 'Authorization: Bearer %s\n' "$LENS_ADMIN_TOKEN" > "$headers" ;;
  key) IFS= read -r key; printf 'Authorization: Bearer %s\n' "$key" > "$headers" ;;
  none) : > "$headers" ;;
  *) exit 64 ;;
esac
printf 'Content-Type: application/json\n' >> "$headers"
if [ "$method" = POST ]; then set -- --data-binary @-; else set --; fi
# No exec: the EXIT trap must still remove the credential header file.
curl --silent --show-error --max-time 30 --request "$method" \
  --header @"$headers" --write-out '\n%{http_code}' "$@" "http://127.0.0.1:4318$path"
""".strip()

# Runs inside a LiteLLM container and reads its own master key.
GATEWAY_STATUS_PROGRAM = """
import json, os, urllib.error, urllib.request
request = urllib.request.Request(
    "http://127.0.0.1:4000/lens/service",
    headers={"Authorization": "Bearer " + os.environ.get("LITELLM_MASTER_KEY", "")},
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        status, body = response.status, response.read().decode("utf-8", "replace")
except urllib.error.HTTPError as error:
    status, body = error.code, error.read().decode("utf-8", "replace")
except OSError:
    status, body = 0, ""
print(json.dumps({"status": status, "body": body}))
""".strip()


def usage(stream: object = sys.stdout) -> None:
    print(USAGE, file=stream, end="")


def usage_error(message: str) -> None:
    LOG.error(f"ERROR: {message}\n")
    usage(sys.stderr)
    raise SystemExit(2)


# --------------------------------------------------------------------------
# secrets
# --------------------------------------------------------------------------


def env_assignments(lines: Sequence[str]) -> dict[str, list[int]]:
    positions: dict[str, list[int]] = {}
    for index, line in enumerate(lines):
        match = ENV_LINE_RE.match(line)
        if match:
            positions.setdefault(match.group(1), []).append(index)
    return positions


def fill_secrets(
    content: str,
    names: Sequence[str],
    generate: Callable[[], str],
) -> tuple[str, list[str], dict[str, str]]:
    """Fill empty or missing assignments; return content, generated names, values."""
    lines = content.splitlines()
    positions = env_assignments(lines)
    generated: list[str] = []
    values: dict[str, str] = {}
    for name in names:
        indexes = positions.get(name, [])
        if len(indexes) > 1:
            fail(f"{name} is assigned more than once; keep a single assignment")
        if indexes:
            current = ENV_LINE_RE.match(lines[indexes[0]]).group(2)  # type: ignore[union-attr]
            if current:
                values[name] = current
                continue
            value = generate()
            lines[indexes[0]] = f"{name}={value}"
        else:
            value = generate()
            lines.append(f"{name}={value}")
        generated.append(name)
        values[name] = value
    updated = "\n".join(lines) + "\n" if lines else ""
    return updated, generated, values


def validate_secret_values(values: dict[str, str]) -> None:
    literal = {
        name: value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"" else value
        for name, value in values.items()
    }
    for name, value in literal.items():
        if len(value.encode()) not in SECRET_LENGTH:
            fail(f"{name} must contain between 32 and 512 characters")
    distinct = [literal[name] for name in DISTINCT_SECRETS if name in literal]
    if len(set(distinct)) != len(distinct):
        fail("LENS_ADMIN_TOKEN, LITELLM_LENS_SERVICE_TOKEN, and LENS_GATEWAY_SECRET must differ")


def generate_secrets(env_file: Path, optional_env_file: Path) -> None:
    files = {"core": env_file, "optional": optional_env_file}
    examples = {"core": ".env.example", "optional": ".env.optional.example"}
    for kind, path in files.items():
        if not env_file_is_present(path):
            fail(f"{path} is missing; copy {examples[kind]} to it first and keep it private")
    all_values: dict[str, str] = {}
    updates: dict[Path, str] = {}
    for kind, path in files.items():
        names = [name for name, file in SECRETS if file == kind]
        content = path.read_text(encoding="utf-8")
        updated, generated, values = fill_secrets(
            content, names, lambda: secrets.token_hex(32)
        )
        all_values.update(values)
        if generated:
            updates[path] = updated
        for name in names:
            state = "generated" if name in generated else "kept the existing value of"
            LOG.info(f"PASS: {state} {name} in {path.name}")
    validate_secret_values(all_values)
    for path, content in updates.items():
        write_private_file(path, content)
    if updates:
        LOG.info(
            "Recreate lens and both LiteLLM gateways to apply new secrets, for example: "
            "docker compose --env-file .env --env-file .env.optional --profile prod "
            "--profile dev --profile lens up -d"
        )


# --------------------------------------------------------------------------
# status and smoke
# --------------------------------------------------------------------------


def compose_states() -> dict[str, dict[str, str]]:
    output = compose_capture(["ps", "--all", "--format", "json"])
    try:
        parsed = json.loads(output) if output.strip() else []
        items = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        items = [json.loads(line) for line in output.splitlines() if line.strip()]
    return {
        str(item.get("Service")): {
            "state": str(item.get("State", "")),
            "health": str(item.get("Health", "")),
        }
        for item in items
        if isinstance(item, dict)
    }


def lens_request(
    method: str,
    path: str,
    *,
    auth: str = "admin",
    payload: object | None = None,
    key: str | None = None,
) -> tuple[int, Any]:
    stdin = ""
    if auth == "key":
        if not key:
            fail("a tracing key is required for this Lens request")
        stdin = key + "\n"
    if payload is not None:
        stdin += json.dumps(payload, separators=(",", ":"))
    result = compose(
        ["exec", "-T", LENS_SERVICE, "sh", "-c", LENS_REQUEST_SCRIPT, "lens-request", method, path, auth],
        input_data=stdin,
        capture_output=True,
        check=False,
    )
    output = result.stdout if isinstance(result.stdout, str) else ""
    body, _, code = output.rpartition("\n")
    if result.returncode or not code.strip().isdigit():
        return 0, None
    try:
        return int(code), json.loads(body) if body.strip() else None
    except json.JSONDecodeError:
        return int(code), body


def gateway_connection(service: str) -> tuple[int, Any]:
    result = compose(
        ["exec", "-T", service, "python", "-c", GATEWAY_STATUS_PROGRAM],
        capture_output=True,
        check=False,
    )
    output = result.stdout if isinstance(result.stdout, str) else ""
    try:
        payload = json.loads(output.strip().splitlines()[-1])
        body = json.loads(payload["body"]) if payload["body"] else None
        return int(payload["status"]), body
    except (IndexError, KeyError, ValueError, json.JSONDecodeError):
        return 0, None


def connection_problems(body: Any) -> list[str]:
    """Explain why a /lens/service response is not a ready connection."""
    if not isinstance(body, dict):
        return ["the gateway returned no Lens service description"]
    status = body.get("status") if isinstance(body.get("status"), dict) else {}
    problems = []
    if body.get("configured") is not True:
        problems.append("LITELLM_LENS_URL is not set in the running gateway")
    if body.get("connected") is not True:
        problems.append("the gateway is not connected (check both secrets and the lens logs)")
    if status.get("storage_ready") is not True:
        problems.append("Lens storage is not ready")
    if status.get("credentials_ready") is not True:
        problems.append("Lens credentials are not ready")
    if status.get("public_contract") != LENS_PUBLIC_CONTRACT:
        problems.append(
            f"Lens public contract is {status.get('public_contract')!r}, "
            f"expected {LENS_PUBLIC_CONTRACT}"
        )
    return problems


def check_status(states: dict[str, dict[str, str]] | None = None) -> bool:
    """Log PASS/FAIL lines for the Lens profile and return whether all passed."""
    states = compose_states() if states is None else states
    ok = True
    lens_running = states.get(LENS_SERVICE, {}).get("state") == "running"
    if not lens_running:
        LOG.info("INFO: the lens service is not running; checking that gateways do not use Lens")
    else:
        for service in (LENS_STORE, LENS_SERVICE):
            item = states.get(service, {})
            if item.get("state") == "running" and item.get("health") == "healthy":
                LOG.info(f"PASS: {service} is running and healthy")
            else:
                LOG.error(
                    f"FAIL: {service} is {item.get('state') or 'missing'}"
                    f" (health: {item.get('health') or 'unknown'})"
                )
                ok = False
        status, _ = lens_request("GET", "/health/ready", auth="none")
        if status == 200:
            LOG.info("PASS: Lens /health/ready returned HTTP 200")
        else:
            LOG.error(f"FAIL: Lens /health/ready returned HTTP {status} (expected 200)")
            ok = False
        status, _ = lens_request("GET", "/lens/tracing/keys", auth="none")
        if status == 401:
            LOG.info("PASS: Lens rejects unauthenticated API requests")
        else:
            LOG.error(f"FAIL: unauthenticated Lens API request returned HTTP {status} (expected 401)")
            ok = False

    for gateway in GATEWAYS:
        if states.get(gateway, {}).get("state") != "running":
            continue
        status, body = gateway_connection(gateway)
        if status != 200:
            LOG.error(f"FAIL: {gateway} GET /lens/service returned HTTP {status} (expected 200)")
            ok = False
            continue
        if lens_running:
            problems = connection_problems(body)
            if problems:
                LOG.error(f"FAIL: {gateway} is not connected to Lens: " + "; ".join(problems))
                LOG.error(
                    "      A gateway started before Lens keeps Lens disabled; recreate it "
                    "with the lens profile enabled."
                )
                ok = False
            else:
                release = body.get("release") or "unknown"
                LOG.info(f"PASS: {gateway} is connected to Lens (release {release})")
        elif isinstance(body, dict) and body.get("configured") is False:
            LOG.info(f"PASS: {gateway} runs without Lens while the lens profile is stopped")
        else:
            LOG.error(
                f"FAIL: {gateway} still has a Lens connection configured although Lens "
                "is not running; recreate the gateway"
            )
            ok = False
    return ok


def otlp_trace(trace_id: str, span_id: str, now_ns: int) -> dict[str, Any]:
    def text(key: str, value: str) -> dict[str, Any]:
        return {"key": key, "value": {"stringValue": value}}

    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [text("service.name", SMOKE_AGENT)]},
                "scopeSpans": [
                    {
                        "scope": {"name": "litellm-stack/scripts/lens.py"},
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_id,
                                "name": "LiteLLM stack Lens smoke trace",
                                "kind": 1,
                                "startTimeUnixNano": str(now_ns),
                                "endTimeUnixNano": str(now_ns + 1_000_000),
                                "attributes": [
                                    text("gen_ai.operation.name", "invoke_agent"),
                                    text("gen_ai.agent.name", SMOKE_AGENT),
                                ],
                                "status": {"code": 1},
                            }
                        ],
                    }
                ],
            }
        ]
    }


def retry(
    action: Callable[[], tuple[int, Any]],
    accept: Callable[[int, Any], bool],
    attempts: int = 30,
    delay: float = 1.0,
) -> tuple[int, Any]:
    status, body = action()
    for _ in range(attempts - 1):
        if accept(status, body):
            break
        time.sleep(delay)
        status, body = action()
    return status, body


def smoke() -> None:
    states = compose_states()
    if states.get(LENS_SERVICE, {}).get("state") != "running":
        fail("the lens service is not running; start the lens profile first", 2)
    if not check_status(states):
        fail("Lens status checks failed; fix them before the smoke test")

    status, created = lens_request(
        "POST",
        "/lens/tracing/keys",
        payload={"name": f"{SMOKE_AGENT} {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}"},
    )
    if status != 200 or not isinstance(created, dict) or not created.get("key"):
        fail(f"could not create a temporary tracing key (HTTP {status})")
    key = str(created["key"])
    record = created.get("record") if isinstance(created.get("record"), dict) else {}
    key_id = str(record.get("id", ""))
    LOG.info("PASS: created a temporary tracing key")
    try:
        trace_id, span_id = secrets.token_hex(16), secrets.token_hex(8)
        payload = otlp_trace(trace_id, span_id, time.time_ns())
        status, _ = retry(
            lambda: lens_request("POST", "/v1/traces", auth="key", key=key, payload=payload),
            lambda code, _body: 200 <= code < 300,
        )
        if not 200 <= status < 300:
            fail(f"Lens rejected the smoke trace (HTTP {status})")
        LOG.info(f"PASS: Lens accepted OTLP trace {trace_id}")
        status, body = retry(
            lambda: lens_request("GET", f"/v1/traces/{trace_id}"),
            lambda code, _body: code == 200,
        )
        if status != 200 or trace_id not in json.dumps(body):
            fail(f"the smoke trace could not be read back (HTTP {status})")
        LOG.info(f"PASS: read trace {trace_id} back from Lens storage")
    finally:
        if key_id:
            status, _ = lens_request("DELETE", f"/lens/tracing/keys/{key_id}")
            if status == 200:
                LOG.info("PASS: revoked the temporary tracing key")
            else:
                LOG.error(
                    f"FAIL: could not revoke tracing key {key_id} (HTTP {status}); "
                    "revoke it under Manage tracing keys"
                )
    LOG.info("PASS: Lens smoke test completed")


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def parse_secret_options(arguments: list[str]) -> tuple[Path, Path]:
    env_file, optional_env_file = ROOT / ".env", ROOT / ".env.optional"
    index = 0
    while index < len(arguments):
        option = arguments[index]
        if option not in {"--env-file", "--optional-env-file"}:
            usage_error(f"unsupported secrets option: {option}")
        if index + 1 >= len(arguments) or not arguments[index + 1]:
            usage_error(f"{option} requires a non-empty value")
        value = Path(arguments[index + 1])
        if option == "--env-file":
            env_file = value
        else:
            optional_env_file = value
        index += 2
    return env_file, optional_env_file


def main() -> None:
    args = sys.argv[1:]
    command = args.pop(0) if args else "help"
    if command in {"help", "-h", "--help"}:
        usage()
    elif command == "secrets":
        generate_secrets(*parse_secret_options(args))
    elif command == "status":
        if args:
            usage_error("status accepts no additional arguments")
        require_docker()
        if not check_status():
            fail("Lens status checks failed")
        LOG.info("PASS: Lens status checks completed")
    elif command == "smoke":
        if args:
            usage_error("smoke accepts no additional arguments")
        require_docker()
        smoke()
    else:
        usage_error(f"unknown command: {command}")


if __name__ == "__main__":
    run_main(main)
