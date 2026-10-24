#!/usr/bin/env bash
#
# Safely assess a candidate, immutably pinned LiteLLM image against this
# repository's configs and modules before it is ever adopted in
# docker-compose.yml. The script never starts, stops, or reconfigures the
# live stack and never touches its databases: every candidate-image check
# runs in a disposable, network-isolated container built only from
# read-only mounts of the repository's own config and module files, using
# non-secret placeholder credentials. Real secrets in .env are never read.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
COMPOSE_FILE="$ROOT/docker-compose.yml"
LITELLM_DIR="$ROOT/litellm"
MODULES_DIR="$LITELLM_DIR/modules"
SEARCH_MODULE="$MODULES_DIR/search.yaml"
GUARDRAILS_MODULE="$MODULES_DIR/guardrails/presidio.yaml"
POLICIES_MODULE="$MODULES_DIR/policies/eu-data-protection.yaml"
LANGFUSE_USER_MODULE="$MODULES_DIR/langfuse_user_attribution.py"
PROD_CONFIG="$LITELLM_DIR/config.prod.yaml"
DEV_CONFIG="$LITELLM_DIR/config.dev.yaml"

IMAGE_REF_REGEX='^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]+)?(/[A-Za-z0-9._-]+)+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$'

CANDIDATE_IMAGE=""
ALLOW_PULL=0

PASS_COUNT=0
FAIL_COUNT=0
declare -a RUNNING_CONTAINERS=()
VALIDATION_NETWORK=""

usage() {
  cat <<'EOF'
Usage:
  scripts/check-litellm-upgrade-compatibility.sh --candidate-image IMAGE_REF [--pull]

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

Checks performed (grouped):
  Repository configuration (static, no image required):
    - Modular search module is declared and included by both profiles
    - Guardrail and policy modules are declared, cross-referenced, and
      included by both profiles
    - Vector-store registry is development-only and uses env-sourced
      credentials, never literal secrets
    - Privacy, retention, content-redacted Langfuse OTel settings, and trusted
      pseudonymous attribution match the documented values in both profiles
    - Development and production services, databases, hostnames, and model
      lists remain isolated in docker-compose.yml

  Candidate image compatibility (requires the image):
    - The image reference is pinned to an immutable digest
    - The image is available locally (or pulled read-only with --pull)
    - The image loads litellm/config.prod.yaml without error
    - The image loads litellm/config.dev.yaml without error
    - The image's installed package still recognizes the provider and
      setting identifiers this repository depends on
    - Production and development loads ran in fully isolated containers
      without leaking one profile's models into the other

Exit status:
  0  every check passed
  1  one or more checks failed
  2  usage error or an unsafe/incomplete environment (fails closed)
EOF
}

info() {
  printf '%s\n' "$1"
}

note() {
  printf 'NOTE: %s\n' "$1"
}

pass() {
  printf 'PASS: %s\n' "$1"
  PASS_COUNT=$((PASS_COUNT + 1))
}

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  if [[ -n "${2:-}" ]]; then
    printf '      %s\n' "$2" >&2
  fi
  FAIL_COUNT=$((FAIL_COUNT + 1))
}

abort() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 2
}

# shellcheck disable=SC2329  # invoked indirectly via 'trap cleanup EXIT' below
cleanup() {
  local status=$?
  trap - EXIT

  local container
  for container in "${RUNNING_CONTAINERS[@]:-}"; do
    [[ -n "$container" ]] || continue
    docker rm -f "$container" >/dev/null 2>&1 || true
  done
  if [[ -n "$VALIDATION_NETWORK" ]]; then
    docker network rm "$VALIDATION_NETWORK" >/dev/null 2>&1 || true
  fi

  exit "$status"
}

# Runs the candidate image's own config validator in a disposable, read-only,
# privilege-dropped container on an isolated internal Docker network. The only
# peer is a disposable Xberg MCP container; neither container can reach the
# internet or live Compose services.
run_validate_config() {
  local config_path="$1"
  shift
  # Remaining positional parameters ("$@") are extra 'docker run -e ...'
  # arguments. "$@" (unlike a local array) stays safe under 'set -u' even
  # when empty, which matters for the production call that passes none.
  local container_name="litellm-upgrade-check-$$-$RANDOM"
  local elapsed=0
  local output
  local status

  RUNNING_CONTAINERS+=("$container_name")
  if ! docker run -d \
      --name "$container_name" \
      --network "$VALIDATION_NETWORK" \
      --read-only \
      --cap-drop ALL \
      --security-opt no-new-privileges:true \
      --pids-limit 256 \
      --memory 512m \
      --tmpfs /tmp:mode=1777 \
      -v "$config_path:/app/config.yaml:ro" \
      -v "$MODULES_DIR:/app/modules:ro" \
      -e DATABASE_URL="postgresql://upgrade-check:upgrade-check@127.0.0.1:5432/upgrade-check" \
      -e LITELLM_MASTER_KEY="sk-upgrade-check-placeholder" \
      -e OPENAI_API_KEY="sk-upgrade-check-placeholder" \
      -e LITELLM_LOCAL_MODEL_COST_MAP=True \
      "$@" \
      --entrypoint litellm \
      "$CANDIDATE_IMAGE" \
      --config /app/config.yaml --validate_config >/dev/null; then
    RUNNING_CONTAINERS=("${RUNNING_CONTAINERS[@]/$container_name/}")
    printf '125\x1e%s' "candidate validation container could not start"
    return
  fi

  while [[ "$(docker inspect -f '{{.State.Running}}' "$container_name" 2>/dev/null)" == "true" ]]; do
    output="$(docker logs "$container_name" 2>&1)"
    if grep -Eq 'LiteLLM: config OK|LiteLLM: Proxy initialized with Config, Set models:' <<<"$output"; then
      docker rm -f "$container_name" >/dev/null 2>&1 || true
      RUNNING_CONTAINERS=("${RUNNING_CONTAINERS[@]/$container_name/}")
      printf '0\x1e%s' "$output"
      return
    fi
    if ((elapsed >= 120)); then
      docker rm -f "$container_name" >/dev/null 2>&1 || true
      RUNNING_CONTAINERS=("${RUNNING_CONTAINERS[@]/$container_name/}")
      printf '124\x1e%s' "$output"
      return
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done

  status="$(docker inspect -f '{{.State.ExitCode}}' "$container_name" 2>/dev/null || printf '125')"
  output="$(docker logs "$container_name" 2>&1)"
  docker rm "$container_name" >/dev/null 2>&1 || true
  RUNNING_CONTAINERS=("${RUNNING_CONTAINERS[@]/$container_name/}")

  printf '%s\x1e%s' "$status" "$output"
}

start_validation_mcp_server() {
  local xberg_image
  local container_name="litellm-upgrade-check-xberg-$$-$RANDOM"

  xberg_image="$(
    compose_service_block xberg |
      awk '$1 == "image:" { print $2; exit }'
  )"
  [[ "$xberg_image" =~ @sha256:[0-9a-f]{64}$ ]] ||
    abort "xberg must use a digest-pinned image before candidate validation"

  VALIDATION_NETWORK="litellm-upgrade-check-$$-$RANDOM"
  docker network create --internal "$VALIDATION_NETWORK" >/dev/null ||
    abort "could not create the isolated candidate-validation network"

  RUNNING_CONTAINERS+=("$container_name")
  docker run -d \
    --name "$container_name" \
    --network "$VALIDATION_NETWORK" \
    --network-alias xberg \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --pids-limit 256 \
    --memory 1g \
    --tmpfs /tmp:mode=1777 \
    "$xberg_image" \
    mcp --transport http --host 0.0.0.0 --port 8001 \
      --allowed-host xberg --allowed-host xberg:8001 >/dev/null ||
    abort "could not start the isolated Xberg MCP validator"

  local attempt=0
  while ((attempt < 10)); do
    if docker logs "$container_name" 2>&1 |
      grep -Fq 'Starting MCP HTTP server on http://0.0.0.0:8001'; then
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 1
  done
  abort "isolated Xberg MCP validator did not become ready"
}

# Searches the candidate image's own installed package for literal
# identifiers this repository's configs depend on (provider names and
# general_settings/litellm_settings keys), all in one container. This is a
# best-effort signal: litellm's config loader ignores unrecognized keys
# instead of raising an error, so validate_config alone cannot prove a
# renamed or removed setting would still be honored. Runs read-only,
# network-isolated, no secrets. Prints one "identifier:count" line per
# argument, in order.
check_image_identifiers() {
  local container_name="litellm-upgrade-check-$$-$RANDOM"
  local output

  RUNNING_CONTAINERS+=("$container_name")
  output="$(
    docker run --rm \
      --name "$container_name" \
      --network none \
      --read-only \
      --cap-drop ALL \
      --security-opt no-new-privileges:true \
      --pids-limit 256 \
      --memory 512m \
      --tmpfs /tmp:mode=1777 \
      --entrypoint sh \
      "$CANDIDATE_IMAGE" \
      -c 'pkg_dir="/app/.venv/lib/python3.13/site-packages/litellm"
          [ -d "$pkg_dir" ] || pkg_dir="$(find /app -maxdepth 6 -type d -iname litellm 2>/dev/null | head -n1)"
          for term in "$@"; do
            if [ -n "$pkg_dir" ]; then
              count="$(grep -rl -- "$term" "$pkg_dir" 2>/dev/null | wc -l)"
            else
              count=0
            fi
            printf "%s:%s\n" "$term" "$count"
          done' \
      sh "$@" 2>/dev/null
  )"
  RUNNING_CONTAINERS=("${RUNNING_CONTAINERS[@]/$container_name/}")
  printf '%s' "$output"
}

check_langfuse_attribution() {
  local container_name="litellm-upgrade-check-$$-$RANDOM"
  local status

  RUNNING_CONTAINERS+=("$container_name")
  docker run --rm -i \
    --name "$container_name" \
    --network none \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --pids-limit 256 \
    --memory 512m \
    --tmpfs /tmp:mode=1777 \
    --env LANGFUSE_USER_ID_SALT=upgrade-check-placeholder \
    --env LITELLM_OTEL_BAGGAGE_PROMOTED_KEYS= \
    --env LITELLM_OTEL_BAGGAGE_METADATA_KEYS= \
    --env LITELLM_OTEL_BAGGAGE_TEAM_METADATA_KEYS= \
    --env 'OTEL_PYTHON_FASTAPI_EXCLUDED_URLS=.*' \
    --volume "$LANGFUSE_USER_MODULE:/upgrade-check/langfuse_user_attribution.py:ro" \
    --entrypoint python \
    "$CANDIDATE_IMAGE" - >/dev/null 2>&1 <<'PY'
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
            "metadata": {"trace_user_id": "caller-metadata"},
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

excluded_urls = get_excluded_urls("FASTAPI")
assert excluded_urls.url_disabled("https://llm.example.test/key/info?key=raw-secret")
PY
  status=$?
  RUNNING_CONTAINERS=("${RUNNING_CONTAINERS[@]/$container_name/}")
  return "$status"
}

# Matches $2 as a whole identifier inside $1, not merely as a substring, so
# e.g. "presidio-pii" never matches an unrelated "presidio-pii-renamed". The
# identifiers this script compares only ever contain [A-Za-z0-9_.-], so they
# are safe to splice directly into the extended regex below.
contains_exact_token() {
  local haystack="$1"
  local token="$2"

  grep -Eq "(^|[^A-Za-z0-9_.-])${token}([^A-Za-z0-9_.-]|\$)" <<<"$haystack"
}

# Extracts the text between a top-level Compose service key and the next
# top-level key (or end of file), so later checks can reason about one
# service's block without matching lines that belong to a different one.
compose_service_block() {
  local service="$1"

  awk -v svc="  $service:" '
    $0 == svc { capture = 1; print; next }
    capture && /^  [A-Za-z0-9_-]+:$/ { exit }
    capture { print }
  ' "$COMPOSE_FILE"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --candidate-image)
      [[ $# -ge 2 ]] || abort "--candidate-image requires a value"
      CANDIDATE_IMAGE="$2"
      shift 2
      ;;
    --candidate-image=*)
      CANDIDATE_IMAGE="${1#*=}"
      shift
      ;;
    --pull)
      ALLOW_PULL=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      abort "unknown argument: $1"
      ;;
  esac
done

[[ -n "$CANDIDATE_IMAGE" ]] || {
  usage >&2
  abort "--candidate-image is required"
}

command -v docker >/dev/null 2>&1 || abort "docker is required"
docker info >/dev/null 2>&1 || abort "docker daemon is not reachable"
docker compose version >/dev/null 2>&1 || abort "docker compose is required"
command -v awk >/dev/null 2>&1 || abort "awk is required"
command -v grep >/dev/null 2>&1 || abort "grep is required"
command -v sed >/dev/null 2>&1 || abort "sed is required"

for required_file in "$COMPOSE_FILE" "$PROD_CONFIG" "$DEV_CONFIG" \
  "$SEARCH_MODULE" "$GUARDRAILS_MODULE" "$POLICIES_MODULE" "$LANGFUSE_USER_MODULE"; do
  [[ -f "$required_file" ]] ||
    abort "required repository file is missing: $required_file"
done

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

cd "$ROOT" || abort "could not change into repository root: $ROOT"

# Static syntax check only. Use the committed placeholder contract so validation
# neither depends on nor reads a developer's real .env credentials.
docker compose --env-file "$ROOT/.env.example" config --quiet ||
  abort "docker-compose.yml does not pass static validation with .env.example"

info "== Candidate image =="
info "$CANDIDATE_IMAGE"
CURRENT_PROD_IMAGE="$(compose_service_block litellm-prod | awk '$1 == "image:" { print $2; exit }')"
CURRENT_DEV_IMAGE="$(compose_service_block litellm-dev | awk '$1 == "image:" { print $2; exit }')"
info "Currently pinned production image: $CURRENT_PROD_IMAGE"
info "Currently pinned development image: $CURRENT_DEV_IMAGE"
if [[ "$CANDIDATE_IMAGE" == "$CURRENT_PROD_IMAGE" ]]; then
  note "candidate image matches the production pin; this is a re-verification for that profile"
fi
if [[ "$CANDIDATE_IMAGE" == "$CURRENT_DEV_IMAGE" ]]; then
  note "candidate image matches the development pin; this is a re-verification for that profile"
fi
info ""

### --- Repository configuration checks (static; no image required) --- ###

info "== Repository configuration checks =="

# Modular search
search_ok=1
SEARCH_TOOL_NAME="$(
  grep -m1 'search_tool_name:' "$SEARCH_MODULE" |
    sed -E 's/^[[:space:]]*-?[[:space:]]*search_tool_name:[[:space:]]*//'
)"
[[ -n "$SEARCH_TOOL_NAME" ]] || search_ok=0
grep -q '^search_tools:' "$SEARCH_MODULE" || search_ok=0
for cfg in "$PROD_CONFIG" "$DEV_CONFIG"; do
  grep -Eq '^\s*-\s*modules/search\.yaml\s*$' "$cfg" || search_ok=0
done
if ((search_ok)); then
  pass "Modular search module (modules/search.yaml) is declared and included by both profiles"
else
  fail "Modular search module (modules/search.yaml) is declared and included by both profiles" \
    "expected a search_tools list with a search_tool_name, included via 'modules/search.yaml' in both config.prod.yaml and config.dev.yaml"
fi

# Guardrails/policies
guardrails_ok=1
GUARDRAIL_NAME="$(
  grep -m1 'guardrail_name:' "$GUARDRAILS_MODULE" |
    sed -E 's/^[[:space:]]*-?[[:space:]]*guardrail_name:[[:space:]]*//'
)"
POLICY_NAME="$(
  grep -m1 -E '^  [A-Za-z0-9_-]+:' "$POLICIES_MODULE" |
    sed -E 's/^[[:space:]]*//; s/:.*$//'
)"
[[ -n "$GUARDRAIL_NAME" ]] || guardrails_ok=0
[[ -n "$POLICY_NAME" ]] || guardrails_ok=0
grep -q '^guardrails:' "$GUARDRAILS_MODULE" || guardrails_ok=0
grep -q '^policies:' "$POLICIES_MODULE" || guardrails_ok=0
grep -q '^policy_attachments:' "$POLICIES_MODULE" || guardrails_ok=0
if [[ -n "$GUARDRAIL_NAME" ]]; then
  guardrail_listed=0
  while IFS= read -r policy_list_line; do
    policy_list_entry="$(
      sed -E 's/^[[:space:]]*-[[:space:]]*//; s/[[:space:]]*$//' <<<"$policy_list_line"
    )"
    if [[ "$policy_list_entry" == "$GUARDRAIL_NAME" ]]; then
      guardrail_listed=1
      break
    fi
  done < <(grep -E '^[[:space:]]*-[[:space:]]*[A-Za-z0-9_.-]+[[:space:]]*$' "$POLICIES_MODULE")
  ((guardrail_listed)) || guardrails_ok=0
fi
for cfg in "$PROD_CONFIG" "$DEV_CONFIG"; do
  grep -Eq '^\s*-\s*modules/guardrails/presidio\.yaml\s*$' "$cfg" || guardrails_ok=0
  grep -Eq '^\s*-\s*modules/policies/eu-data-protection\.yaml\s*$' "$cfg" || guardrails_ok=0
done
if ((guardrails_ok)); then
  pass "Guardrail and policy modules are declared, cross-referenced ('$GUARDRAIL_NAME' in policy '$POLICY_NAME'), and included by both profiles"
else
  fail "Guardrail and policy modules are declared, cross-referenced, and included by both profiles" \
    "expected modules/guardrails/presidio.yaml's guardrail_name to be listed under modules/policies/eu-data-protection.yaml's guardrails.add, and both modules included in config.prod.yaml and config.dev.yaml"
fi

# Vector-store configuration (development only, no literal secrets)
vector_ok=1
grep -q '^vector_store_registry:' "$DEV_CONFIG" || vector_ok=0
grep -q 'custom_llm_provider: pg_vector' "$DEV_CONFIG" || vector_ok=0
if grep -A6 '^vector_store_registry:' "$DEV_CONFIG" |
  grep -E '^\s*api_(key|base):' |
  grep -qvE 'os\.environ/'; then
  vector_ok=0
fi
if grep -q '^vector_store_registry:' "$PROD_CONFIG" 2>/dev/null; then
  vector_ok=0
fi
if ((vector_ok)); then
  pass "Vector-store registry is declared for development only and uses environment-sourced credentials"
else
  fail "Vector-store registry is declared for development only and uses environment-sourced credentials" \
    "expected litellm/config.dev.yaml to declare a pg_vector vector_store_registry entry with api_key/api_base via os.environ/*, and litellm/config.prod.yaml to declare none"
fi

# Privacy and retention settings
retention_ok=1
for cfg in "$PROD_CONFIG" "$DEV_CONFIG"; do
  grep -Eq '^\s*disable_spend_logs:\s*false\s*$' "$cfg" || retention_ok=0
  grep -Eq '^\s*store_prompts_in_spend_logs:\s*false\s*$' "$cfg" || retention_ok=0
  grep -Eq '^\s*maximum_spend_logs_retention_period:\s*180d\s*$' "$cfg" || retention_ok=0
  grep -Eq '^\s*maximum_health_check_retention_period:\s*7d\s*$' "$cfg" || retention_ok=0
  grep -Eq '^\s*turn_off_message_logging:\s*true\s*$' "$cfg" || retention_ok=0
  grep -Eq '^\s*store_audit_logs:\s*true\s*$' "$cfg" || retention_ok=0
  grep -Eq '^\s*callbacks:\s*$' "$cfg" || retention_ok=0
  grep -Eq '^\s*-\s*modules\.langfuse_user_attribution\.proxy_handler_instance\s*$' "$cfg" || retention_ok=0
  grep -Eq '^\s*-\s*langfuse_otel\s*$' "$cfg" || retention_ok=0
done
for service in litellm-prod litellm-dev; do
  service_block="$(compose_service_block "$service")"
  grep -Fq 'LITELLM_OTEL_V2: "true"' <<<"$service_block" || retention_ok=0
  grep -Fq 'LITELLM_OTEL_BAGGAGE_PROMOTED_KEYS: ""' <<<"$service_block" || retention_ok=0
  grep -Fq 'LITELLM_OTEL_BAGGAGE_METADATA_KEYS: ""' <<<"$service_block" || retention_ok=0
  grep -Fq 'LITELLM_OTEL_BAGGAGE_TEAM_METADATA_KEYS: ""' <<<"$service_block" || retention_ok=0
  grep -Fq 'OTEL_PYTHON_FASTAPI_EXCLUDED_URLS: ".*"' <<<"$service_block" || retention_ok=0
done
grep -Fq 'metadata.pop("trace_user_id", None)' "$LANGFUSE_USER_MODULE" || retention_ok=0
grep -Fq 'litellm_metadata["trace_user_id"]' "$LANGFUSE_USER_MODULE" || retention_ok=0
grep -q 'user_api_key_dict.user_id' "$LANGFUSE_USER_MODULE" || retention_ok=0
grep -q 'LANGFUSE_USER_ID_SALT' "$LANGFUSE_USER_MODULE" || retention_ok=0
grep -Fq 'key.lower().startswith("langfuse_")' "$LANGFUSE_USER_MODULE" || retention_ok=0
if ((retention_ok)); then
  pass "Privacy, retention, and authenticated pseudonymous Langfuse attribution settings match both profiles"
else
  fail "Privacy, retention, and authenticated pseudonymous Langfuse attribution settings match both profiles" \
    "required retention settings, content controls, langfuse_otel, or the trusted user-attribution hook are missing"
fi

# Dev/prod isolation at the Compose and config level
isolation_ok=1
prod_block="$(compose_service_block litellm-prod)"
dev_block="$(compose_service_block litellm-dev)"
pgvector_block="$(compose_service_block litellm-pgvector)"
demo_prod_block="$(compose_service_block litellm-demo-data-prod)"

[[ -n "$prod_block" && -n "$dev_block" && -n "$pgvector_block" && -n "$demo_prod_block" ]] || isolation_ok=0
grep -Eq '^\s*profiles:\s*\[prod\]\s*$' <<<"$prod_block" || isolation_ok=0
grep -Eq '^\s*profiles:\s*\[dev\]\s*$' <<<"$dev_block" || isolation_ok=0
grep -Eq '^\s*profiles:\s*\[dev\]\s*$' <<<"$pgvector_block" || isolation_ok=0
grep -Eq '^\s*profiles:\s*\[demo\]\s*$' <<<"$demo_prod_block" || isolation_ok=0
grep -q 'litellm_prod' <<<"$prod_block" || isolation_ok=0
grep -q 'litellm_dev' <<<"$dev_block" || isolation_ok=0
grep -q 'LITELLM_PROD_HOST' <<<"$prod_block" || isolation_ok=0
grep -q 'LITELLM_DEV_HOST' <<<"$dev_block" || isolation_ok=0
grep -q 'litellm_dev' <<<"$prod_block" && isolation_ok=0
grep -q 'litellm_prod' <<<"$dev_block" && isolation_ok=0
if grep -Eq 'ollama_chat/|ollama/|test-model' "$PROD_CONFIG"; then
  isolation_ok=0
fi
if ((isolation_ok)); then
  pass "Development and production services, databases, hostnames, and model lists remain isolated in docker-compose.yml"
else
  fail "Development and production services, databases, hostnames, and model lists remain isolated in docker-compose.yml" \
    "expected litellm-prod/litellm-dev/litellm-pgvector to use distinct profiles, distinct database names, distinct hostnames, and config.prod.yaml to contain no dev-only model (ollama_chat/, ollama/, test-model)"
fi

info ""

### --- Candidate image compatibility checks --- ###

info "== Candidate image compatibility checks =="

# Immutable image reference
if [[ "$CANDIDATE_IMAGE" =~ $IMAGE_REF_REGEX ]]; then
  pass "Candidate image reference is pinned to an immutable digest"
  image_ref_ok=1
else
  fail "Candidate image reference is pinned to an immutable digest" \
    "expected registry/repository:tag@sha256:<64-hex-digest>; got: $CANDIDATE_IMAGE (a floating tag alone is not reproducible and must be rejected)"
  image_ref_ok=0
fi

# Image availability (local inspect, or an explicit, read-only pull)
image_available=0
if ((image_ref_ok)); then
  if docker image inspect "$CANDIDATE_IMAGE" >/dev/null 2>&1; then
    pass "Candidate image is available locally"
    image_available=1
  elif ((ALLOW_PULL)); then
    info "Candidate image is not present locally; pulling read-only (no service is started)..."
    if docker pull "$CANDIDATE_IMAGE" >/dev/null 2>&1; then
      pass "Candidate image was retrieved read-only via 'docker pull'"
      image_available=1
    else
      fail "Candidate image is available locally" \
        "'docker pull $CANDIDATE_IMAGE' failed; verify registry access, authentication, and that the digest exists"
    fi
  else
    fail "Candidate image is available locally" \
      "image not present locally; re-run with --pull to fetch it read-only, or run 'docker pull $CANDIDATE_IMAGE' yourself first"
  fi
else
  fail "Candidate image is available locally" \
    "skipped: the image reference failed immutability validation"
fi

if ((image_available)); then
  start_validation_mcp_server
  prod_result="$(run_validate_config "$PROD_CONFIG")"
  prod_status="${prod_result%%$'\x1e'*}"
  prod_output="${prod_result#*$'\x1e'}"

  dev_result="$(
    run_validate_config "$DEV_CONFIG" \
      -e PG_VECTOR_API_BASE="http://upgrade-check-placeholder:8000" \
      -e PG_VECTOR_API_KEY="upgrade-check-placeholder"
  )"
  dev_status="${dev_result%%$'\x1e'*}"
  dev_output="${dev_result#*$'\x1e'}"

  if [[ "$prod_status" == "0" ]] &&
    grep -Eq 'LiteLLM: config OK|LiteLLM: Proxy initialized with Config, Set models:' <<<"$prod_output"; then
    pass "Candidate image loads litellm/config.prod.yaml without error"
  else
    fail "Candidate image loads litellm/config.prod.yaml without error" \
      "'litellm --validate_config' exited $prod_status; last output line: $(tail -n1 <<<"$prod_output")"
  fi

  if [[ "$dev_status" == "0" ]] &&
    grep -Eq 'LiteLLM: config OK|LiteLLM: Proxy initialized with Config, Set models:' <<<"$dev_output"; then
    pass "Candidate image loads litellm/config.dev.yaml without error"
  else
    fail "Candidate image loads litellm/config.dev.yaml without error" \
      "'litellm --validate_config' exited $dev_status; last output line: $(tail -n1 <<<"$dev_output")"
  fi

  # Modular search / guardrails-policies evidence from the validator output
  modular_evidence_ok=1
  for output in "$prod_output" "$dev_output"; do
    grep -q 'Search Tools:' <<<"$output" || modular_evidence_ok=0
    [[ -z "$SEARCH_TOOL_NAME" ]] || contains_exact_token "$output" "$SEARCH_TOOL_NAME" || modular_evidence_ok=0
    grep -qi 'Policy Engine' <<<"$output" || modular_evidence_ok=0
    [[ -z "$POLICY_NAME" ]] || contains_exact_token "$output" "$POLICY_NAME" || modular_evidence_ok=0
    [[ -z "$GUARDRAIL_NAME" ]] || contains_exact_token "$output" "$GUARDRAIL_NAME" || modular_evidence_ok=0
  done
  if ((modular_evidence_ok)); then
    pass "Candidate image's own output confirms it recognizes the modular search tool and the guardrail/policy modules in both profiles"
  else
    fail "Candidate image's own output confirms it recognizes the modular search tool and the guardrail/policy modules in both profiles" \
      "expected 'Search Tools:' with '$SEARCH_TOOL_NAME' and 'Policy Engine' with policy '$POLICY_NAME' and guardrail '$GUARDRAIL_NAME' in the validator output for both configs"
  fi

  # Provider/setting identifiers still present in the candidate's own code
  # (checked together in a single container to minimize overhead).
  identifiers_ok=1
  identifiers_output="$(
    check_image_identifiers \
      pg_vector presidio searxng langfuse_otel \
      maximum_spend_logs_retention_period maximum_health_check_retention_period \
      store_prompts_in_spend_logs disable_spend_logs \
      turn_off_message_logging store_audit_logs LITELLM_OTEL_V2 \
      LITELLM_OTEL_BAGGAGE_PROMOTED_KEYS LITELLM_OTEL_BAGGAGE_METADATA_KEYS \
      LITELLM_OTEL_BAGGAGE_TEAM_METADATA_KEYS \
      OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT
  )"
  while IFS=':' read -r identifier count; do
    [[ -n "$identifier" ]] || continue
    count="$(printf '%s' "$count" | tr -dc '0-9')"
    if [[ -z "$count" || "$count" -eq 0 ]]; then
      identifiers_ok=0
      note "candidate image has no reference to '$identifier' in its installed package"
    fi
  done <<<"$identifiers_output"
  if ((identifiers_ok)); then
    pass "Candidate image's installed package recognizes the provider, Langfuse OTel, and privacy/retention identifiers this repository depends on"
  else
    fail "Candidate image's installed package recognizes the provider, Langfuse OTel, and privacy/retention identifiers this repository depends on" \
      "one or more identifiers used by litellm/config.*.yaml and litellm/modules/* were not found anywhere in the candidate image's litellm package; it may silently ignore that setting or provider"
  fi

  if check_langfuse_attribution; then
    pass "Candidate image preserves trusted pseudonymous Langfuse attribution without exporting raw principal metadata"
  else
    fail "Candidate image preserves trusted pseudonymous Langfuse attribution without exporting raw principal metadata" \
      "the attribution hook did not replace caller identity fields, strip langfuse_* headers and raw principal metadata, or remain compatible with the candidate image's Langfuse OTel exporter"
  fi

  # Isolation evidence: each profile's models stayed within its own container
  cross_leak_ok=1
  grep -q 'qwen3.5:4b' <<<"$prod_output" && cross_leak_ok=0
  grep -q 'test-model' <<<"$prod_output" && cross_leak_ok=0
  grep -q 'vector_store' <<<"$prod_output" && cross_leak_ok=0
  if ((cross_leak_ok)); then
    pass "Production and development candidate-image validations ran in fully isolated containers without leaking one profile's models into the other"
  else
    fail "Production and development candidate-image validations ran in fully isolated containers without leaking one profile's models into the other" \
      "the production config's validator output unexpectedly referenced development-only models or the vector store"
  fi
else
  fail "Candidate image loads litellm/config.prod.yaml without error" "skipped: candidate image is unavailable"
  fail "Candidate image loads litellm/config.dev.yaml without error" "skipped: candidate image is unavailable"
  fail "Candidate image's own output confirms it recognizes the modular search tool and the guardrail/policy modules in both profiles" "skipped: candidate image is unavailable"
  fail "Candidate image's installed package recognizes the provider, Langfuse OTel, and privacy/retention identifiers this repository depends on" "skipped: candidate image is unavailable"
  fail "Candidate image preserves trusted pseudonymous Langfuse attribution without exporting raw principal metadata" "skipped: candidate image is unavailable"
  fail "Production and development candidate-image validations ran in fully isolated containers without leaking one profile's models into the other" "skipped: candidate image is unavailable"
fi

info ""
info "== Summary =="
info "Passed: $PASS_COUNT"
info "Failed: $FAIL_COUNT"

if ((FAIL_COUNT > 0)); then
  info "Result: NOT compatible for upgrade. Resolve every FAIL above, then re-run this check before pinning the new digest in docker-compose.yml."
  exit 1
fi

info "Result: compatible. Still review upstream release notes, back up the databases (scripts/backup-databases.sh), update the pinned digest, and re-run scripts/verify-schema.sql and scripts/verify-pgvector.sh after deploying."
exit 0
