#!/usr/bin/env bash
# shellcheck disable=SC2018,SC2329
#
# Post-upgrade regression suite for the LiteLLM stack.
#
# Runs a safe, read-mostly set of checks against the already-running Compose
# stack to confirm an upgrade has not regressed Compose/service health,
# prod/dev auth boundaries and isolation, the development Ollama integration,
# pgvector storage, Presidio PII masking behavior, SearXNG and Xberg MCP auth
# boundaries, Langfuse tracing, and the Prometheus/Alloy/Loki/Grafana
# observability pipeline.
#
# Every check either prints PASS/FAIL/SKIP lines and returns 0 (pass), 1
# (fail), or 2 (skip). A check is only skipped when its documented
# prerequisite (a specific profile, container, or host service) is absent;
# any failure that occurs once a prerequisite is satisfied is reported as a
# real, nonzero-exit-causing failure. Nothing here performs destructive
# writes: pgvector verification reuses scripts/verify-pgvector.sh, which only
# attempts inserts that must be rejected, and the Ollama checks only issue
# read-only chat/embedding calls. Credentials and response bodies that may
# contain user or masking data are never printed; only derived booleans,
# counts, and HTTP status codes are reported.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"

# --- Configuration (overridable via environment, defaults match .env.example) ---
: "${LITELLM_PROD_HOST:=$(grep -m1 '^LITELLM_PROD_HOST=' .env 2>/dev/null | cut -d= -f2- || true)}"
: "${LITELLM_PROD_HOST:=llm.example.test}"
: "${LITELLM_DEV_HOST:=$(grep -m1 '^LITELLM_DEV_HOST=' .env 2>/dev/null | cut -d= -f2- || true)}"
: "${LITELLM_DEV_HOST:=llm-dev.example.test}"
: "${GRAFANA_HOST:=$(grep -m1 '^GRAFANA_HOST=' .env 2>/dev/null | cut -d= -f2- || true)}"
: "${GRAFANA_HOST:=grafana.example.test}"
: "${SEARXNG_HOST:=$(grep -m1 '^SEARXNG_HOST=' .env 2>/dev/null | cut -d= -f2- || true)}"
: "${SEARXNG_HOST:=search.example.test}"
: "${LANGFUSE_WEB_HOST:=$(grep -m1 '^LANGFUSE_WEB_HOST=' .env 2>/dev/null | cut -d= -f2- || true)}"
: "${LANGFUSE_WEB_HOST:=langfuse.example.test}"
: "${OLLAMA_BASE_URL:=http://localhost:11434}"

PROD_URL="https://$LITELLM_PROD_HOST"
DEV_URL="https://$LITELLM_DEV_HOST"
GRAFANA_URL="https://$GRAFANA_HOST"
SEARXNG_URL="https://$SEARXNG_HOST"
LANGFUSE_URL="https://$LANGFUSE_WEB_HOST"

ALL_CHECKS="compose-config compose-health network-exposure prod-auth dev-auth isolation dev-ollama-chat dev-ollama-embedding pgvector presidio searxng xberg-mcp langfuse observability logs"
SELECTED_CHECKS=""
SKIPPED_CHECKS=""
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
FAILED_NAMES=""
CHAT_NONCE=""
CHAT_NONCE_SERVICE=""

usage() {
  cat <<EOF
Usage: scripts/verify-litellm-upgrade.sh [--only CHECK[,CHECK...]] [--skip CHECK[,CHECK...]] [--list]

Runs a safe, non-destructive post-upgrade regression suite against the
already-running Compose stack. Start the desired profile(s) first:

  docker compose --profile prod up -d
  docker compose --profile dev up -d

Checks (run in this order unless filtered):
  compose-config      Validate prod and dev Compose configs without starting containers
  compose-health       Container running/health state and no stray host-published ports
  network-exposure     Only traefik publishes ports to the host
  prod-auth            Production LiteLLM rejects unauthenticated and accepts authenticated requests
  dev-auth              Development LiteLLM rejects unauthenticated and accepts authenticated requests
  isolation             Separate databases, separate model lists, dev-only models rejected by prod
  dev-ollama-chat      Development profile's host Ollama chat model answers (skipped if Ollama absent)
  dev-ollama-embedding Development profile's host Ollama embedding model responds (skipped if Ollama absent)
  pgvector              Delegates to scripts/verify-pgvector.sh (skipped if dev/litellm-pgvector absent)
  presidio              Presidio masks PERSON and intentionally leaves ORGANIZATION unmasked
  searxng               SearXNG public page reachable; LiteLLM search tool enforces auth
  xberg-mcp             Xberg tools enforce auth and extract the official Docker guide
  langfuse              Langfuse web/worker health and LiteLLM OTel wiring
  observability         Grafana, Prometheus, and Loki datasources and scrape targets are healthy
  logs                  Loki has labels for running containers and does not leak a chat probe

Options:
  --only CHECK[,CHECK...]  Run only the listed checks (comma-separated)
  --skip CHECK[,CHECK...]  Run every check except the listed ones
  --list                   Print the available check names and exit
  -h, --help               Print this help and exit

A check is skipped (not failed) only when its documented prerequisite, such
as a Compose profile or a host Ollama model, is absent. Once a prerequisite
is present, any assertion failure is reported as FAIL and causes a nonzero
exit. Never pass real personal or client data as probe text.
EOF
}

log_pass() { printf 'PASS: %s\n' "$1"; }
log_fail() { printf 'FAIL: %s\n' "$1" >&2; }
log_skip() { printf 'SKIP: %s\n' "$1"; }
log_info() { printf 'INFO: %s\n' "$1"; }
fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

# --- Argument parsing ---
ONLY_LIST=""
SKIP_LIST=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --only)
      [[ $# -ge 2 ]] || fail "--only requires an argument"
      [[ -n "$2" ]] || fail "--only requires a non-empty argument"
      ONLY_LIST="$2"
      shift 2
      ;;
    --only=*)
      ONLY_LIST="${1#--only=}"
      [[ -n "$ONLY_LIST" ]] || fail "--only requires a non-empty argument"
      shift
      ;;
    --skip)
      [[ $# -ge 2 ]] || fail "--skip requires an argument"
      [[ -n "$2" ]] || fail "--skip requires a non-empty argument"
      SKIP_LIST="$2"
      shift 2
      ;;
    --skip=*)
      SKIP_LIST="${1#--skip=}"
      [[ -n "$SKIP_LIST" ]] || fail "--skip requires a non-empty argument"
      shift
      ;;
    --list)
      for c in $ALL_CHECKS; do printf '%s\n' "$c"; done
      exit 0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -n "$ONLY_LIST" && -n "$SKIP_LIST" ]]; then
  fail "--only and --skip are mutually exclusive"
fi

is_in_csv() {
  local needle="$1" csv="$2" item
  IFS=',' read -r -a _items <<<"$csv"
  for item in "${_items[@]}"; do
    [[ "$item" == "$needle" ]] && return 0
  done
  return 1
}

is_known_check() {
  local needle="$1" item
  for item in $ALL_CHECKS; do
    [[ "$item" == "$needle" ]] && return 0
  done
  return 1
}

for c in $ALL_CHECKS; do
  if [[ -n "$ONLY_LIST" ]]; then
    if is_in_csv "$c" "$ONLY_LIST"; then
      SELECTED_CHECKS="$SELECTED_CHECKS $c"
    fi
  elif [[ -n "$SKIP_LIST" ]]; then
    if ! is_in_csv "$c" "$SKIP_LIST"; then
      SELECTED_CHECKS="$SELECTED_CHECKS $c"
    fi
  else
    SELECTED_CHECKS="$SELECTED_CHECKS $c"
  fi
done

if [[ -n "$ONLY_LIST" ]]; then
  IFS=',' read -r -a _requested <<<"$ONLY_LIST"
  for r in "${_requested[@]}"; do
    is_known_check "$r" || fail "unknown check name: $r (use --list)"
  done
fi
if [[ -n "$SKIP_LIST" ]]; then
  IFS=',' read -r -a _requested <<<"$SKIP_LIST"
  for r in "${_requested[@]}"; do
    is_known_check "$r" || fail "unknown check name: $r (use --list)"
  done
fi

# --- Tool prerequisites (fatal; nothing below can run without these) ---
command -v docker >/dev/null 2>&1 || fail "docker is required"
docker compose version >/dev/null 2>&1 || fail "docker compose is required"
command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v jq >/dev/null 2>&1 || fail "jq is required"

cd "$ROOT"
docker compose config --quiet >/dev/null 2>&1 ||
  fail "docker compose cannot read this project's configuration"

# --- curl helper with mkcert fallback (mirrors README's TLS guidance) ---
# A plain string (rather than an array) avoids a bash 3.2 "unbound variable"
# bug when expanding an empty array under `set -u` (macOS ships bash 3.2).
CACERT_FILE=""
curl_req() {
  # Usage: curl_req <curl args...>
  local rc=0
  if [[ -n "$CACERT_FILE" ]]; then
    curl --silent --show-error --cacert "$CACERT_FILE" "$@" || rc=$?
  else
    curl --silent --show-error "$@" || rc=$?
  fi
  if [[ $rc -eq 60 && -z "$CACERT_FILE" ]] && command -v mkcert >/dev/null 2>&1; then
    local candidate
    candidate="$(mkcert -CAROOT 2>/dev/null)/rootCA.pem"
    if [[ -r "$candidate" ]]; then
      CACERT_FILE="$candidate"
      curl --silent --show-error --cacert "$CACERT_FILE" "$@"
      return $?
    fi
  fi
  return "$rc"
}

curl_status() {
  # Usage: curl_status <curl args...> ; echoes only the HTTP status code
  curl_req --output /dev/null --write-out '%{http_code}' "$@"
}

service_running() {
  docker compose ps --services --status running 2>/dev/null | grep -Fqx "$1"
}

running_services() {
  docker compose ps --services --status running 2>/dev/null
}

PROD_RUNNING=false
DEV_RUNNING=false
service_running litellm-prod && PROD_RUNNING=true
service_running litellm-dev && DEV_RUNNING=true

# Resolve a usable master key from whichever profile is running. Never echoed.
litellm_master_key() {
  if $DEV_RUNNING; then
    docker compose exec -T litellm-dev printenv LITELLM_MASTER_KEY 2>/dev/null | tr -d '\r\n'
  elif $PROD_RUNNING; then
    docker compose exec -T litellm-prod printenv LITELLM_MASTER_KEY 2>/dev/null | tr -d '\r\n'
  else
    return 1
  fi
}

# =========================================================================
# compose-config: validate both profiles without starting containers
# =========================================================================
check_compose_config() {
  local ok=0
  if docker compose --profile prod config --quiet 2>/dev/null; then
    log_pass "production Compose profile config is valid"
  else
    log_fail "production Compose profile config failed validation"
    ok=1
  fi
  if docker compose --profile dev config --quiet 2>/dev/null; then
    log_pass "development Compose profile config is valid"
  else
    log_fail "development Compose profile config failed validation"
    ok=1
  fi
  return "$ok"
}

# =========================================================================
# compose-health: running/health state of shared and selected services
# =========================================================================
check_compose_health() {
  local ok=0
  local ps_json
  ps_json="$(docker compose ps --all --format json 2>/dev/null)" ||
    { log_fail "docker compose ps failed"; return 1; }

  if [[ -z "$ps_json" ]]; then
    log_fail "no Compose services are known to this project; start a profile first"
    return 1
  fi

  # Shared services expected whenever any profile is running.
  local shared_services="traefik postgres presidio-analyzer presidio-anonymizer searxng xberg prometheus loki alloy langfuse-postgres langfuse-clickhouse langfuse-minio langfuse-redis langfuse-worker langfuse-web grafana"
  local svc
  for svc in $shared_services; do
    if ! printf '%s' "$ps_json" | jq -e --arg s "$svc" 'select(.Service == $s)' >/dev/null 2>&1; then
      log_fail "shared service '$svc' has no container (is the stack started?)"
      ok=1
      continue
    fi
    local state
    state="$(printf '%s' "$ps_json" | jq -r --arg s "$svc" 'select(.Service == $s) | .State')"
    if [[ "$state" != "running" ]]; then
      log_fail "shared service '$svc' is not running (state: $state)"
      ok=1
    fi
  done

  # Any container reporting an explicit "unhealthy" healthcheck is a real failure.
  local unhealthy
  unhealthy="$(printf '%s' "$ps_json" | jq -r 'select(.Health == "unhealthy") | .Service')"
  if [[ -n "$unhealthy" ]]; then
    log_fail "unhealthy service(s): $(printf '%s' "$unhealthy" | tr '\n' ' ')"
    ok=1
  fi

  # One-off migration jobs must have exited successfully if they ran at all.
  local job
  for job in postgres-init pgvector-migrate; do
    local exit_code
    exit_code="$(printf '%s' "$ps_json" | jq -r --arg s "$job" 'select(.Service == $s) | .ExitCode')"
    if [[ -n "$exit_code" && "$exit_code" != "0" ]]; then
      log_fail "one-off job '$job' exited with code $exit_code"
      ok=1
    fi
  done

  if $PROD_RUNNING; then
    local prod_state
    prod_state="$(printf '%s' "$ps_json" | jq -r 'select(.Service == "litellm-prod") | .State')"
    [[ "$prod_state" == "running" ]] || { log_fail "litellm-prod is not running"; ok=1; }
  fi
  if $DEV_RUNNING; then
    local dev_state
    dev_state="$(printf '%s' "$ps_json" | jq -r 'select(.Service == "litellm-dev") | .State')"
    [[ "$dev_state" == "running" ]] || { log_fail "litellm-dev is not running"; ok=1; }
  fi

  [[ $ok -eq 0 ]] && log_pass "Compose services report expected running/health state"
  return "$ok"
}

# =========================================================================
# network-exposure: only traefik may publish ports to the host
# =========================================================================
check_network_exposure() {
  local ps_json leaks
  ps_json="$(docker compose ps --format json 2>/dev/null)" ||
    { log_fail "docker compose ps failed"; return 1; }

  leaks="$(
    printf '%s' "$ps_json" |
      jq -r 'select(.Service != "traefik") | select(.Publishers != null) | select([.Publishers[] | select(.URL != "")] | length > 0) | .Service'
  )"

  if [[ -n "$leaks" ]]; then
    log_fail "service(s) unexpectedly publish ports to the host: $(printf '%s' "$leaks" | tr '\n' ' ')"
    return 1
  fi
  log_pass "only traefik publishes ports to the host"
  return 0
}

# =========================================================================
# prod-auth / dev-auth: auth boundary for each running proxy
# =========================================================================
check_profile_auth() {
  local label="$1" base_url="$2"
  local master_key status

  master_key="$(litellm_master_key)" || { log_fail "$label: could not read LITELLM_MASTER_KEY"; return 1; }
  [[ -n "$master_key" ]] || { log_fail "$label: LITELLM_MASTER_KEY is empty"; return 1; }

  local ok=0
  status="$(curl_status "$base_url/v1/models")"
  if [[ "$status" == "401" ]]; then
    log_pass "$label: unauthenticated request returned HTTP 401"
  else
    log_fail "$label: unauthenticated request returned HTTP $status (expected 401)"
    ok=1
  fi

  status="$(curl_status "$base_url/v1/models" -H "Authorization: Bearer $master_key")"
  if [[ "$status" == "200" ]]; then
    log_pass "$label: authenticated request returned HTTP 200"
  else
    log_fail "$label: authenticated request returned HTTP $status (expected 200)"
    ok=1
  fi

  unset master_key
  return "$ok"
}

check_prod_auth() {
  $PROD_RUNNING || { log_skip "prod-auth: litellm-prod is not running"; return 2; }
  check_profile_auth "production" "$PROD_URL"
}

check_dev_auth() {
  $DEV_RUNNING || { log_skip "dev-auth: litellm-dev is not running"; return 2; }
  check_profile_auth "development" "$DEV_URL"
}

# =========================================================================
# isolation: separate databases, separate model catalogs, cross rejection
# =========================================================================
check_isolation() {
  if ! $PROD_RUNNING || ! $DEV_RUNNING; then
    log_skip "isolation: requires both litellm-prod and litellm-dev running"
    return 2
  fi

  local ok=0
  local master_key
  master_key="$(litellm_master_key)" || { log_fail "isolation: could not read LITELLM_MASTER_KEY"; return 1; }

  # Separate databases.
  local db_list
  if db_list="$(
    docker compose exec -T postgres sh -c \
      'psql -U "$POSTGRES_USER" -d postgres -tAc "SELECT datname FROM pg_database WHERE datname IN ('"'"'litellm_prod'"'"','"'"'litellm_dev'"'"')"'
  )"; then
    if printf '%s' "$db_list" | grep -Fqx "litellm_prod" && printf '%s' "$db_list" | grep -Fqx "litellm_dev"; then
      log_pass "isolation: litellm_prod and litellm_dev exist as separate databases"
    else
      log_fail "isolation: expected both litellm_prod and litellm_dev databases to exist"
      ok=1
    fi
  else
    log_fail "isolation: could not query postgres for litellm_prod/litellm_dev"
    ok=1
  fi

  # Separate model catalogs.
  local prod_models dev_models
  prod_models="$(curl_req "$PROD_URL/v1/models" -H "Authorization: Bearer $master_key" | jq -r '.data[].id' | sort -u)"
  dev_models="$(curl_req "$DEV_URL/v1/models" -H "Authorization: Bearer $master_key" | jq -r '.data[].id' | sort -u)"

  if [[ -z "$prod_models" || -z "$dev_models" ]]; then
    log_fail "isolation: could not read model lists from one or both profiles"
    ok=1
  else
    local dev_only prod_only
    dev_only="$(comm -13 <(printf '%s\n' "$prod_models") <(printf '%s\n' "$dev_models"))"
    prod_only="$(comm -23 <(printf '%s\n' "$prod_models") <(printf '%s\n' "$dev_models"))"

    if [[ -n "$dev_only" ]]; then
      log_pass "isolation: development declares model(s) not present in production"
    else
      log_info "isolation: no development-only models are currently configured"
    fi

    # Every dev-only model must be rejected by production.
    local model status rejected=true
    while IFS= read -r model; do
      [[ -n "$model" ]] || continue
      status="$(
        curl_status "$PROD_URL/v1/chat/completions" \
          -H "Authorization: Bearer $master_key" -H 'Content-Type: application/json' \
          --data-raw "$(jq -nc --arg m "$model" '{model:$m, messages:[{role:"user",content:"ping"}]}')"
      )"
      case "$status" in
        4*) ;;
        *)
          log_fail "isolation: production unexpectedly returned HTTP $status for dev-only model"
          rejected=false
          ok=1
          ;;
      esac
    done <<<"$dev_only"
    $rejected && [[ -n "$dev_only" ]] && log_pass "isolation: production rejects every development-only model"

    if [[ -n "$prod_only" ]]; then
      local accepted=true
      while IFS= read -r model; do
        [[ -n "$model" ]] || continue
        status="$(
          curl_status "$DEV_URL/v1/chat/completions" \
            -H "Authorization: Bearer $master_key" -H 'Content-Type: application/json' \
            --data-raw "$(jq -nc --arg m "$model" '{model:$m, messages:[{role:"user",content:"ping"}]}')"
        )"
        case "$status" in
          4*) ;;
          *)
            log_fail "isolation: development unexpectedly returned HTTP $status for production-only model"
            accepted=false
            ok=1
            ;;
        esac
      done <<<"$prod_only"
      $accepted && log_pass "isolation: development rejects every production-only model"
    fi
  fi

  # Production must not expose the development-only pgvector store registry.
  local prod_store_count
  prod_store_count="$(
    curl_req "$PROD_URL/vector_store/list" -H "Authorization: Bearer $master_key" | jq -r '.data | length' 2>/dev/null
  )"
  if [[ "$prod_store_count" == "0" ]]; then
    log_pass "isolation: production lists no pgvector stores"
  else
    log_fail "isolation: production unexpectedly lists $prod_store_count vector store(s)"
    ok=1
  fi

  unset master_key
  return "$ok"
}

# =========================================================================
# dev-ollama-chat / dev-ollama-embedding: optional host Ollama integration
# =========================================================================
ollama_tags() {
  curl --silent --show-error --max-time 3 "$OLLAMA_BASE_URL/api/tags" 2>/dev/null
}

check_dev_ollama_chat() {
  $DEV_RUNNING || { log_skip "dev-ollama-chat: litellm-dev is not running"; return 2; }

  local tags
  tags="$(ollama_tags)" || { log_skip "dev-ollama-chat: Ollama is not reachable at $OLLAMA_BASE_URL"; return 2; }
  printf '%s' "$tags" | jq -e '.models[]?.name' >/dev/null 2>&1 ||
    { log_skip "dev-ollama-chat: Ollama at $OLLAMA_BASE_URL returned no models"; return 2; }
  printf '%s' "$tags" | jq -r '.models[].name' | grep -Fqx "qwen3.5:4b" ||
    { log_skip "dev-ollama-chat: Ollama model qwen3.5:4b is not installed"; return 2; }

  local master_key
  master_key="$(litellm_master_key)" || { log_fail "dev-ollama-chat: could not read LITELLM_MASTER_KEY"; return 1; }

  # Letters-only nonce: a digit-heavy token (e.g. a timestamp/PID) can be
  # misdetected by Presidio's built-in CREDIT_CARD/PHONE_NUMBER/LOCATION
  # recognizers and get masked before it reaches the model, which would make
  # this probe nondeterministic. Random lowercase letters avoid that.
  CHAT_NONCE="probe-$(LC_ALL=C tr -dc 'a-z' < /dev/urandom 2>/dev/null | head -c 16)"
  local payload reply attempt
  payload="$(jq -nc --arg nonce "$CHAT_NONCE" '{model:"qwen3.5:4b", messages:[{role:"user",content:("Reply with exactly, and nothing else: " + $nonce)}], temperature:0}')"

  # Ollama unloads idle models after a few minutes; retry once so a cold-start
  # reload (which can otherwise surface as a transient 502/timeout) is not
  # mistaken for a real regression.
  for attempt in 1 2; do
    reply="$(
      curl_req --max-time 90 "$DEV_URL/v1/chat/completions" \
        -H "Authorization: Bearer $master_key" -H 'Content-Type: application/json' \
        --data-raw "$payload" 2>/dev/null | jq -r '.choices[0].message.content // empty' 2>/dev/null
    )"
    [[ "$(printf '%s' "$reply" | tr -d '[:space:]')" == "$CHAT_NONCE" ]] && break
    [[ "$attempt" == "1" ]] && sleep 15
  done
  unset master_key

  if [[ "$(printf '%s' "$reply" | tr -d '[:space:]')" == "$CHAT_NONCE" ]]; then
    log_pass "dev-ollama-chat: qwen3.5:4b answered the deterministic chat probe"
    CHAT_NONCE_SERVICE="litellm-dev"
    return 0
  fi

  log_fail "dev-ollama-chat: qwen3.5:4b did not echo the expected probe text"
  return 1
}

check_dev_ollama_embedding() {
  $DEV_RUNNING || { log_skip "dev-ollama-embedding: litellm-dev is not running"; return 2; }

  local tags
  tags="$(ollama_tags)" || { log_skip "dev-ollama-embedding: Ollama is not reachable at $OLLAMA_BASE_URL"; return 2; }
  printf '%s' "$tags" | jq -r '.models[]?.name // empty' | grep -Eq '^bge-m3(:|$)' ||
    { log_skip "dev-ollama-embedding: Ollama model bge-m3 is not installed"; return 2; }

  local master_key
  master_key="$(litellm_master_key)" || { log_fail "dev-ollama-embedding: could not read LITELLM_MASTER_KEY"; return 1; }

  # Ollama unloads idle models after a few minutes; retry once so a cold-start
  # reload is not mistaken for a real regression.
  local dims attempt
  for attempt in 1 2; do
    dims="$(
      curl_req --max-time 90 "$DEV_URL/v1/embeddings" \
        -H "Authorization: Bearer $master_key" -H 'Content-Type: application/json' \
        --data-raw '{"model":"bge-m3","input":"litellm upgrade verification probe"}' 2>/dev/null |
        jq -r '.data[0].embedding | length' 2>/dev/null
    )"
    [[ "$dims" == "1024" ]] && break
    [[ "$attempt" == "1" ]] && sleep 15
  done
  unset master_key

  if [[ "$dims" == "1024" ]]; then
    log_pass "dev-ollama-embedding: bge-m3 returned a 1024-dimension embedding"
    return 0
  fi

  log_fail "dev-ollama-embedding: bge-m3 returned an unexpected embedding (dimensions: ${dims:-none})"
  return 1
}

# =========================================================================
# pgvector: delegate to the existing verification script
# =========================================================================
check_pgvector() {
  if ! $DEV_RUNNING; then
    log_skip "pgvector: litellm-dev is not running"
    return 2
  fi
  if ! service_running litellm-pgvector; then
    log_skip "pgvector: litellm-pgvector is not running"
    return 2
  fi
  [[ -x "$ROOT/scripts/verify-pgvector.sh" || -r "$ROOT/scripts/verify-pgvector.sh" ]] ||
    { log_skip "pgvector: scripts/verify-pgvector.sh is not present"; return 2; }

  if sh "$ROOT/scripts/verify-pgvector.sh"; then
    return 0
  fi
  log_fail "pgvector: scripts/verify-pgvector.sh reported a failure"
  return 1
}

# =========================================================================
# presidio: PERSON masked, ORGANIZATION intentionally left unmasked
# =========================================================================
check_presidio() {
  service_running presidio-analyzer || { log_skip "presidio: presidio-analyzer is not running"; return 2; }
  service_running presidio-anonymizer || { log_skip "presidio: presidio-anonymizer is not running"; return 2; }

  local ok=0
  local probe_text="My name is Jordan Example and I work at Example Client Limited."
  local analysis
  analysis="$(
    docker compose exec -T presidio-analyzer curl --silent --show-error \
      -X POST http://localhost:3000/analyze -H 'Content-Type: application/json' \
      --data-raw "$(jq -nc --arg t "$probe_text" '{text:$t, language:"en"}')"
  )" || { log_fail "presidio: could not reach the analyzer API"; return 1; }

  if printf '%s' "$analysis" | jq -e 'any(.[]; .entity_type == "PERSON")' >/dev/null 2>&1; then
    log_pass "presidio: analyzer detected a PERSON entity"
  else
    log_fail "presidio: analyzer did not detect the PERSON entity in the probe text"
    ok=1
  fi

  if printf '%s' "$analysis" | jq -e 'any(.[]; .entity_type == "ORGANIZATION")' >/dev/null 2>&1; then
    log_fail "presidio: analyzer unexpectedly detected an ORGANIZATION entity"
    ok=1
  else
    log_pass "presidio: analyzer did not detect an ORGANIZATION entity (intentional current configuration)"
  fi

  local anonymize_request anonymized
  anonymize_request="$(
    jq -nc --arg t "$probe_text" --argjson results "$analysis" \
      '{text:$t, analyzer_results:$results}'
  )"
  anonymized="$(
    docker compose exec -T presidio-analyzer curl --silent --show-error \
      -X POST http://presidio-anonymizer:3000/anonymize -H 'Content-Type: application/json' \
      --data-raw "$anonymize_request" | jq -r '.text // empty'
  )" || { log_fail "presidio: could not reach the anonymizer API"; return 1; }

  if [[ -n "$anonymized" ]] &&
    ! printf '%s' "$anonymized" | grep -Fq "Jordan Example" &&
    printf '%s' "$anonymized" | grep -Fq "Example Client Limited"; then
    log_pass "presidio: anonymizer masked the PERSON name and left the organization name intact"
  else
    log_fail "presidio: anonymizer output did not match the expected masking behavior"
    ok=1
  fi

  # Confirm the guardrail is wired into whichever LiteLLM profile(s) are running.
  local master_key
  if master_key="$(litellm_master_key)"; then
    local profile base_url
    for profile in prod:$PROD_URL dev:$DEV_URL; do
      local name="${profile%%:*}" base="${profile#*:}"
      if { [[ "$name" == "prod" ]] && $PROD_RUNNING; } || { [[ "$name" == "dev" ]] && $DEV_RUNNING; }; then
        if curl_req "$base/policies/resolve" -H "Authorization: Bearer $master_key" -H 'Content-Type: application/json' \
          --data-raw '{"model":"gpt-4o-mini"}' |
          jq -e '.effective_guardrails // [] | index("presidio-pii") != null' >/dev/null 2>&1; then
          log_pass "presidio: $name policy resolution includes the presidio-pii guardrail"
        else
          log_fail "presidio: $name policy resolution does not include the presidio-pii guardrail"
          ok=1
        fi
      fi
    done
    unset master_key
  fi

  return "$ok"
}

# =========================================================================
# searxng: public page reachable, LiteLLM search tool enforces auth
# =========================================================================
check_searxng() {
  service_running searxng || { log_skip "searxng: searxng is not running"; return 2; }

  local ok=0
  local status
  status="$(curl_status "$SEARXNG_URL/")"
  if [[ "$status" == "200" ]]; then
    log_pass "searxng: public search page returned HTTP 200"
  else
    log_fail "searxng: public search page returned HTTP $status (expected 200)"
    ok=1
  fi

  local master_key profile base_url
  master_key="$(litellm_master_key)" || { log_fail "searxng: could not read LITELLM_MASTER_KEY"; return 1; }

  for profile in prod:$PROD_URL dev:$DEV_URL; do
    local name="${profile%%:*}" base="${profile#*:}"
    if { [[ "$name" == "prod" ]] && $PROD_RUNNING; } || { [[ "$name" == "dev" ]] && $DEV_RUNNING; }; then
      status="$(
        curl_status "$base/v1/search/searxng-search" -H 'Content-Type: application/json' \
          --data-raw '{"query":"litellm upgrade verification","max_results":1}'
      )"
      if [[ "$status" == "401" ]]; then
        log_pass "searxng: $name unauthenticated search request returned HTTP 401"
      else
        log_fail "searxng: $name unauthenticated search request returned HTTP $status (expected 401)"
        ok=1
      fi

      if curl_req "$base/v1/search/searxng-search" -H "Authorization: Bearer $master_key" -H 'Content-Type: application/json' \
        --data-raw '{"query":"litellm upgrade verification","max_results":1}' |
        jq -e 'has("results")' >/dev/null 2>&1; then
        log_pass "searxng: $name authenticated search returned a results payload"
      else
        log_fail "searxng: $name authenticated search did not return a results payload"
        ok=1
      fi
    fi
  done

  unset master_key
  return "$ok"
}

# =========================================================================
# xberg-mcp: tool allowlist, auth boundary, and live website extraction
# =========================================================================
check_xberg_mcp() {
  service_running xberg || { log_skip "xberg-mcp: xberg is not running"; return 2; }

  local master_key
  master_key="$(litellm_master_key)" || { log_fail "xberg-mcp: could not read LITELLM_MASTER_KEY"; return 1; }
  [[ -n "$master_key" ]] || { log_fail "xberg-mcp: LITELLM_MASTER_KEY is empty"; return 1; }

  local ok=0 profile name base status tools response
  for profile in prod:$PROD_URL dev:$DEV_URL; do
    name="${profile%%:*}"
    base="${profile#*:}"
    if { [[ "$name" == "prod" ]] && $PROD_RUNNING; } || { [[ "$name" == "dev" ]] && $DEV_RUNNING; }; then
      status="$(curl_status "$base/mcp-rest/tools/list?server_id=xberg")"
      if [[ "$status" == "401" ]]; then
        log_pass "xberg-mcp: $name unauthenticated tool discovery returned HTTP 401"
      else
        log_fail "xberg-mcp: $name unauthenticated tool discovery returned HTTP $status (expected 401)"
        ok=1
      fi

      tools="$(
        curl_req "$base/mcp-rest/tools/list?server_id=xberg" \
          -H "x-litellm-api-key: $master_key"
      )" || { log_fail "xberg-mcp: $name authenticated tool discovery failed"; ok=1; continue; }
      if printf '%s' "$tools" | jq -e '
        [.tools[]?.name] | sort ==
        ["detect_mime_type", "extract", "get_version", "list_formats"]
      ' >/dev/null 2>&1; then
        log_pass "xberg-mcp: $name exposes only the approved read-only tools"
      else
        log_fail "xberg-mcp: $name tool list does not match the approved allowlist"
        ok=1
      fi

      local dev_index_before=""
      if [[ "$name" == "dev" ]]; then
        dev_index_before="$(
          docker compose exec -T vector-postgres \
            psql --no-psqlrc --quiet --tuples-only --no-align \
              --username vector --dbname vector_store \
              --command "SELECT COALESCE(
                           FLOOR(EXTRACT(EPOCH FROM MAX(created_at)) * 1000000)::bigint,
                           0
                         )
                         FROM embeddings
                         WHERE vector_store_id = 'sme-knowledge'
                           AND metadata->>'indexed_by' = 'xberg-auto-index'
                           AND metadata->>'source' = 'https://docs.xberg.io/guides/docker/';"
        )" || dev_index_before=""
      fi

      response="$(
        curl_req --max-time 120 "$base/mcp-rest/tools/call" \
          -H "x-litellm-api-key: $master_key" \
          -H 'Content-Type: application/json' \
          --data-raw '{
            "server_id": "xberg",
            "name": "extract",
            "arguments": {
              "input": {
                "kind": "uri",
                "uri": "https://docs.xberg.io/guides/docker/"
              }

            }
          }'
      )" || { log_fail "xberg-mcp: $name Docker guide extraction request failed"; ok=1; continue; }
      if printf '%s' "$response" | jq -e '
        tostring |
        contains("Official Docker images") and
        contains("ghcr.io/xberg-io/xberg:latest") and
        contains("MCP server")
      ' >/dev/null 2>&1; then
        log_pass "xberg-mcp: $name extracted the expected official Docker guide content"
      else
        log_fail "xberg-mcp: $name response did not contain the expected Docker guide evidence"
        ok=1
      fi

      if [[ "$name" == "dev" ]]; then
        local indexed_count repeated_count search_response index_generation attempt
        index_generation="$dev_index_before"
        attempt=0
        while [[ "$index_generation" == "$dev_index_before" && "$attempt" -lt 60 ]]; do
          sleep 1
          index_generation="$(
            docker compose exec -T vector-postgres \
              psql --no-psqlrc --quiet --tuples-only --no-align \
                --username vector --dbname vector_store \
                --command "SELECT COALESCE(
                             FLOOR(EXTRACT(EPOCH FROM MAX(created_at)) * 1000000)::bigint,
                             0
                           )
                           FROM embeddings
                           WHERE vector_store_id = 'sme-knowledge'
                             AND metadata->>'indexed_by' = 'xberg-auto-index'
                             AND metadata->>'source' = 'https://docs.xberg.io/guides/docker/';"
          )" || index_generation=""
          attempt=$((attempt + 1))
        done
        indexed_count="$(
          docker compose exec -T vector-postgres \
            psql --no-psqlrc --quiet --tuples-only --no-align \
              --username vector --dbname vector_store \
              --command "SELECT COUNT(*) FROM embeddings
                         WHERE vector_store_id = 'sme-knowledge'
                           AND metadata->>'indexed_by' = 'xberg-auto-index'
                           AND metadata->>'source' = 'https://docs.xberg.io/guides/docker/';"
        )" || indexed_count=""
        if [[ "$indexed_count" =~ ^[1-9][0-9]*$ ]]; then
          log_pass "xberg-mcp: dev extract automatically indexed $indexed_count chunk(s)"
        else
          log_fail "xberg-mcp: dev extract did not create automatic index chunks"
          ok=1
        fi

        local repeat_generation_before="$index_generation"
        response="$(
          curl_req --max-time 120 "$base/mcp-rest/tools/call" \
            -H "x-litellm-api-key: $master_key" \
            -H 'Content-Type: application/json' \
            --data-raw '{
              "server_id": "xberg",
              "name": "extract",
              "arguments": {
                "input": {
                  "kind": "uri",
                  "uri": "https://docs.xberg.io/guides/docker/"
                }
              }
            }'
        )" || { log_fail "xberg-mcp: dev repeated extraction request failed"; ok=1; response=""; }
        index_generation="$repeat_generation_before"
        attempt=0
        while [[ "$index_generation" == "$repeat_generation_before" && "$attempt" -lt 60 ]]; do
          sleep 1
          index_generation="$(
            docker compose exec -T vector-postgres \
              psql --no-psqlrc --quiet --tuples-only --no-align \
                --username vector --dbname vector_store \
                --command "SELECT COALESCE(
                             FLOOR(EXTRACT(EPOCH FROM MAX(created_at)) * 1000000)::bigint,
                             0
                           )
                           FROM embeddings
                           WHERE vector_store_id = 'sme-knowledge'
                             AND metadata->>'indexed_by' = 'xberg-auto-index'
                             AND metadata->>'source' = 'https://docs.xberg.io/guides/docker/';"
          )" || index_generation=""
          attempt=$((attempt + 1))
        done
        repeated_count="$(
          docker compose exec -T vector-postgres \
            psql --no-psqlrc --quiet --tuples-only --no-align \
              --username vector --dbname vector_store \
              --command "SELECT COUNT(*) FROM embeddings
                         WHERE vector_store_id = 'sme-knowledge'
                           AND metadata->>'indexed_by' = 'xberg-auto-index'
                           AND metadata->>'source' = 'https://docs.xberg.io/guides/docker/';"
        )" || repeated_count=""
        if [[ -n "$indexed_count" &&
              "$repeated_count" == "$indexed_count" &&
              "$index_generation" != "$repeat_generation_before" ]]; then
          log_pass "xberg-mcp: repeated dev extraction replaced chunks without duplicates"
        else
          log_fail "xberg-mcp: repeated dev extraction changed the source chunk count"
          ok=1
        fi

        search_response="$(
          curl_req --max-time 120 "$base/v1/vector_stores/sme-knowledge/search" \
            -H "x-litellm-api-key: $master_key" \
            -H 'Content-Type: application/json' \
            --data-raw '{
              "query": "What Docker image runs the Xberg MCP server?",
              "max_num_results": 3
            }'
        )" || search_response=""
        if printf '%s' "$search_response" |
          jq -e 'tostring | contains("ghcr.io/xberg-io/xberg")' >/dev/null 2>&1; then
          log_pass "xberg-mcp: automatically indexed content is searchable through LiteLLM"
        else
          log_fail "xberg-mcp: automatically indexed Docker guide content was not searchable"
          ok=1
        fi

        local semantic_headers semantic_body semantic_status filter_summary filtered_tools
        semantic_headers="$(mktemp)"
        semantic_body="$(mktemp)"
        semantic_status="$(
          curl_req --max-time 180 \
            --dump-header "$semantic_headers" \
            --output "$semantic_body" \
            --write-out '%{http_code}' \
            "$base/v1/chat/completions" \
            -H "x-litellm-api-key: $master_key" \
            -H 'Content-Type: application/json' \
            --data-raw '{
              "model": "qwen3.5:4b",
              "messages": [{
                "role": "user",
                "content": "Use the appropriate tool to report the installed Xberg version."
              }],
              "tools": [{
                "type": "mcp",
                "server_url": "litellm_proxy"
              }],
              "tool_choice": "required",
              "max_tokens": 128
            }'
        )" || semantic_status=""
        filter_summary="$(
          grep -i '^x-litellm-semantic-filter:' "$semantic_headers" |
            head -1 | cut -d: -f2- | tr -d ' \r'
        )" || filter_summary=""
        filtered_tools="$(
          grep -i '^x-litellm-semantic-filter-tools:' "$semantic_headers" |
            head -1 | cut -d: -f2- | tr -d ' \r'
        )" || filtered_tools=""
        if [[ "$semantic_status" == "200" &&
              "$filter_summary" == "4->3" &&
              "$filtered_tools" == *"xberg-get_version"* ]] &&
          jq -e '.choices | length > 0' "$semantic_body" >/dev/null 2>&1; then
          log_pass "xberg-mcp: dev bge-m3 semantic filter reduced 4 tools to 3 and retained get_version"
        else
          log_fail "xberg-mcp: dev bge-m3 semantic filter did not return the expected 4->3 selection"
          ok=1
        fi
        rm -f "$semantic_headers" "$semantic_body"
      fi
    fi
  done

  unset master_key response tools
  return "$ok"
}

# =========================================================================
# langfuse: application health and LiteLLM OTel integration
# =========================================================================
check_langfuse() {
  service_running langfuse-web || { log_skip "langfuse: web service is not running"; return 2; }
  service_running langfuse-worker || { log_fail "langfuse: worker service is not running"; return 1; }

  local ok=0 status
  status="$(curl_status "$LANGFUSE_URL/api/public/health?failIfDatabaseUnavailable=true")"
  if [[ "$status" == "200" ]]; then
    log_pass "langfuse: public health endpoint and PostgreSQL check returned HTTP 200"
  else
    log_fail "langfuse: public health endpoint returned HTTP $status (expected 200)"
    ok=1
  fi

  if docker compose exec -T langfuse-worker \
    wget --quiet --spider http://127.0.0.1:3030/api/health >/dev/null 2>&1; then
    log_pass "langfuse: worker health endpoint is reachable"
  else
    log_fail "langfuse: worker health endpoint is not reachable"
    ok=1
  fi

  local svc otel_flag capture_mode callback_host
  for svc in litellm-prod litellm-dev; do
    service_running "$svc" || continue
    otel_flag="$(docker compose exec -T "$svc" printenv LITELLM_OTEL_V2 2>/dev/null | tr -d '\r\n')"
    capture_mode="$(docker compose exec -T "$svc" printenv OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT 2>/dev/null | tr -d '\r\n')"
    callback_host="$(docker compose exec -T "$svc" printenv LANGFUSE_HOST 2>/dev/null | tr -d '\r\n')"
    if [[ "$otel_flag" == "true" &&
          "$capture_mode" == "no_content" &&
          "$callback_host" == "http://langfuse-web:3000" ]] &&
      docker compose exec -T "$svc" sh -c '
        [ -z "$LITELLM_OTEL_BAGGAGE_PROMOTED_KEYS" ] &&
        [ -z "$LITELLM_OTEL_BAGGAGE_METADATA_KEYS" ] &&
        [ -z "$LITELLM_OTEL_BAGGAGE_TEAM_METADATA_KEYS" ] &&
        [ "$OTEL_PYTHON_FASTAPI_EXCLUDED_URLS" = ".*" ]
      ' >/dev/null 2>&1; then
      log_pass "langfuse: $svc uses privacy-hardened OTel v2 with message content and server spans disabled"
    else
      log_fail "langfuse: $svc OTel environment is not privacy-hardened"
      ok=1
    fi
  done

  return "$ok"
}

# =========================================================================
# observability: Grafana, Prometheus, and Loki
# =========================================================================
grafana_admin_password() {
  service_running grafana || return 1
  docker compose exec -T grafana printenv GF_SECURITY_ADMIN_PASSWORD 2>/dev/null | tr -d '\r\n'
}

check_observability() {
  service_running grafana || { log_skip "observability: grafana is not running"; return 2; }

  local ok=0
  local status
  status="$(curl_status "$GRAFANA_URL/api/health")"
  if [[ "$status" == "200" ]]; then
    log_pass "observability: Grafana health endpoint returned HTTP 200"
  else
    log_fail "observability: Grafana health endpoint returned HTTP $status (expected 200)"
    ok=1
  fi

  local admin_password
  admin_password="$(grafana_admin_password)" || { log_fail "observability: could not read Grafana admin password"; return 1; }
  [[ -n "$admin_password" ]] || { log_fail "observability: Grafana admin password is empty"; return 1; }

  local ds
  for ds in prom loki; do
    if curl_req -u "admin:$admin_password" "$GRAFANA_URL/api/datasources/uid/$ds/health" |
      jq -e '(.status // "") == "OK" or (.status // "") == "success"' >/dev/null 2>&1; then
      log_pass "observability: Grafana datasource '$ds' reports healthy"
    else
      log_fail "observability: Grafana datasource '$ds' did not report healthy"
      ok=1
    fi
  done

  local dashboard_uid
  for dashboard_uid in litellm-stack litellm-cost litellm-admin litellm-team-cost; do
    if curl_req -u "admin:$admin_password" "$GRAFANA_URL/api/dashboards/uid/$dashboard_uid" |
      jq -e --arg uid "$dashboard_uid" '.dashboard.uid == $uid' >/dev/null 2>&1; then
      log_pass "observability: Grafana dashboard '$dashboard_uid' is provisioned"
    else
      log_fail "observability: Grafana dashboard '$dashboard_uid' is not provisioned"
      ok=1
    fi
  done

  local service database demo_counts demo_query
  demo_query="WITH demo_spend AS (
      SELECT team_id, \"user\" AS user_id, api_key, SUM(spend) AS spend
      FROM \"LiteLLM_SpendLogs\"
      WHERE request_id LIKE 'demo-%'
      GROUP BY team_id, \"user\", api_key
    )
    SELECT
      (SELECT COUNT(*) FROM \"LiteLLM_TeamTable\" WHERE team_id LIKE 'demo-%'),
      (SELECT COUNT(*) FROM \"LiteLLM_UserTable\" WHERE user_id LIKE 'demo-%'),
      (SELECT COUNT(*) FROM \"LiteLLM_SpendLogs\" WHERE request_id LIKE 'demo-%'),
      (SELECT COUNT(*)
       FROM \"LiteLLM_TeamTable\" team
       JOIN (SELECT team_id, SUM(spend) AS spend FROM demo_spend GROUP BY team_id) logs
         USING (team_id)
       WHERE team.team_id LIKE 'demo-%'
         AND ROUND(team.spend::numeric, 6) = ROUND(logs.spend::numeric, 6)),
      (SELECT COUNT(*)
       FROM \"LiteLLM_UserTable\" demo_user
       JOIN (SELECT user_id, SUM(spend) AS spend FROM demo_spend GROUP BY user_id) logs
         USING (user_id)
       WHERE demo_user.user_id LIKE 'demo-%'
         AND ROUND(demo_user.spend::numeric, 6) = ROUND(logs.spend::numeric, 6)),
      (SELECT COUNT(*)
       FROM \"LiteLLM_VerificationToken\" demo_key
       JOIN (SELECT api_key, SUM(spend) AS spend FROM demo_spend GROUP BY api_key) logs
         ON logs.api_key = demo_key.token
       WHERE demo_key.token LIKE 'demo-token-%'
         AND ROUND(demo_key.spend::numeric, 6) = ROUND(logs.spend::numeric, 6)
         AND ROUND(demo_key.total_spend::numeric, 6) = ROUND(logs.spend::numeric, 6));"
  for service in litellm-prod litellm-dev; do
    service_running "$service" || continue
    if [[ "$service" == "litellm-prod" ]]; then
      database="litellm_prod"
    else
      database="litellm_dev"
    fi
    demo_counts="$(
      docker compose exec -T postgres sh -c \
        'psql --username "$POSTGRES_USER" --dbname "$1" --tuples-only --no-align --command "$2"' \
        sh "$database" "$demo_query" 2>/dev/null |
        tr -d '[:space:]'
    )"
    if [[ "$demo_counts" == "2|4|12|2|4|4" ]]; then
      log_pass "observability: $database demo counts and team, user, and key spend counters match"
    elif [[ "$database" == "litellm_prod" && "$demo_counts" == "0|0|0|0|0|0" ]]; then
      log_pass "observability: $database has no synthetic demo data (production demo profile is opt-in)"
    else
      if [[ "$database" == "litellm_prod" ]]; then
        log_fail "observability: $database demo data result was '$demo_counts' (expected either complete opt-in data 2|4|12|2|4|4 or no demo data 0|0|0|0|0|0)"
      else
        log_fail "observability: $database demo data result was '$demo_counts' (expected 2|4|12|2|4|4)"
      fi
      ok=1
    fi
  done

  # Always-on scrape targets (independent of which LiteLLM profile is active).
  local targets
  targets="$(curl_req -u "admin:$admin_password" "$GRAFANA_URL/api/datasources/proxy/uid/prom/api/v1/targets")"
  local always_on_jobs="traefik postgres pgvector node cadvisor blackbox-http"
  local job target_count down_jobs
  for job in $always_on_jobs; do
    target_count="$(printf '%s' "$targets" | jq -r --arg j "$job" '[.data.activeTargets[]? | select(.labels.job == $j)] | length')"
    down_jobs="$(printf '%s' "$targets" | jq -r --arg j "$job" '[.data.activeTargets[]? | select(.labels.job == $j and .health != "up")] | length')"
    if [[ "$target_count" == "0" ]]; then
      log_fail "observability: Prometheus job '$job' has no active target"
      ok=1
    elif [[ "$down_jobs" != "0" ]]; then
      log_fail "observability: Prometheus job '$job' has $down_jobs unhealthy target(s)"
      ok=1
    fi
  done
  if $PROD_RUNNING; then
    target_count="$(printf '%s' "$targets" | jq -r '[.data.activeTargets[]? | select(.labels.job == "litellm-prod")] | length')"
    down_jobs="$(printf '%s' "$targets" | jq -r '[.data.activeTargets[]? | select(.labels.job == "litellm-prod" and .health != "up")] | length')"
    if [[ "$target_count" == "0" ]]; then
      log_fail "observability: Prometheus job 'litellm-prod' has no active target"
      ok=1
    elif [[ "$down_jobs" != "0" ]]; then
      log_fail "observability: Prometheus job 'litellm-prod' is unhealthy"
      ok=1
    fi
  fi
  if $DEV_RUNNING; then
    target_count="$(printf '%s' "$targets" | jq -r '[.data.activeTargets[]? | select(.labels.job == "litellm-dev")] | length')"
    down_jobs="$(printf '%s' "$targets" | jq -r '[.data.activeTargets[]? | select(.labels.job == "litellm-dev" and .health != "up")] | length')"
    if [[ "$target_count" == "0" ]]; then
      log_fail "observability: Prometheus job 'litellm-dev' has no active target"
      ok=1
    elif [[ "$down_jobs" != "0" ]]; then
      log_fail "observability: Prometheus job 'litellm-dev' is unhealthy"
      ok=1
    fi
  fi
  [[ $ok -eq 0 ]] && log_pass "observability: expected Prometheus scrape targets are healthy"

  unset admin_password
  return "$ok"
}

# =========================================================================
# logs: Loki has labels for running containers and does not leak probe text
# =========================================================================
check_logs() {
  service_running loki || { log_skip "logs: loki is not running"; return 2; }
  service_running grafana || { log_skip "logs: grafana is not running (used as the Loki query proxy)"; return 2; }

  local ok=0
  local admin_password
  admin_password="$(grafana_admin_password)" || { log_fail "logs: could not read Grafana admin password"; return 1; }

  # Query a wide (7 day) window: low-traffic sidecars such as node-exporter or
  # postgres-exporter may log only at container start, so the endpoint's
  # default (narrow) time range can miss them even though Alloy is shipping
  # their logs correctly.
  local labels now window_start
  now="$(date -u +%s)"
  window_start=$((now - 604800))
  labels="$(
    curl_req -G "$GRAFANA_URL/api/datasources/proxy/uid/loki/loki/api/v1/label/compose_service/values" \
      -u "admin:$admin_password" \
      --data-urlencode "start=${window_start}000000000" \
      --data-urlencode "end=${now}000000000"
  )"
  if ! printf '%s' "$labels" | jq -e '(.status // "") == "success"' >/dev/null 2>&1; then
    log_fail "logs: Loki label query did not succeed"
    ok=1
  else
    local missing svc
    missing=""
    for svc in $(running_services); do
      printf '%s' "$labels" | jq -e --arg s "$svc" '.data | index($s) != null' >/dev/null 2>&1 ||
        missing="$missing $svc"
    done
    if [[ -n "$missing" ]]; then
      log_fail "logs: Loki has no log label for running service(s):$missing"
      ok=1
    else
      log_pass "logs: Loki has log labels for every currently running Compose service"
    fi
  fi

  if [[ -n "$CHAT_NONCE" && -n "$CHAT_NONCE_SERVICE" ]]; then
    local now start query leak_count
    now="$(date -u +%s)"
    start=$((now - 600))
    query="{compose_service=\"$CHAT_NONCE_SERVICE\"} |= \`$CHAT_NONCE\`"
    leak_count="$(
      curl_req -u "admin:$admin_password" -G "$GRAFANA_URL/api/datasources/proxy/uid/loki/loki/api/v1/query_range" \
        --data-urlencode "query=$query" \
        --data-urlencode "start=${start}000000000" \
        --data-urlencode "end=${now}000000000" \
        --data-urlencode 'limit=5' |
        jq -r '.data.result | length' 2>/dev/null
    )"
    if [[ "$leak_count" == "0" ]]; then
      log_pass "logs: the chat probe text was not found in $CHAT_NONCE_SERVICE logs (message logging stays off)"
    else
      log_fail "logs: the chat probe text appeared in $CHAT_NONCE_SERVICE logs; prompt content may be leaking into logs"
      ok=1
    fi
  else
    log_info "logs: skipping the log-leak probe because dev-ollama-chat did not run or was skipped"
  fi

  unset admin_password
  return "$ok"
}

# =========================================================================
# Runner
# =========================================================================
run_check() {
  local name="$1" fn="$2" rc=0
  printf -- '--- %s ---\n' "$name"
  "$fn" || rc=$?
  case $rc in
    0) PASS_COUNT=$((PASS_COUNT + 1)) ;;
    2) SKIP_COUNT=$((SKIP_COUNT + 1)); SKIPPED_CHECKS="$SKIPPED_CHECKS $name" ;;
    *) FAIL_COUNT=$((FAIL_COUNT + 1)); FAILED_NAMES="$FAILED_NAMES $name" ;;
  esac
  printf '\n'
  return 0
}

for check in $SELECTED_CHECKS; do
  case "$check" in
    compose-config) run_check "$check" check_compose_config ;;
    compose-health) run_check "$check" check_compose_health ;;
    network-exposure) run_check "$check" check_network_exposure ;;
    prod-auth) run_check "$check" check_prod_auth ;;
    dev-auth) run_check "$check" check_dev_auth ;;
    isolation) run_check "$check" check_isolation ;;
    dev-ollama-chat) run_check "$check" check_dev_ollama_chat ;;
    dev-ollama-embedding) run_check "$check" check_dev_ollama_embedding ;;
    pgvector) run_check "$check" check_pgvector ;;
    presidio) run_check "$check" check_presidio ;;
    searxng) run_check "$check" check_searxng ;;
    xberg-mcp) run_check "$check" check_xberg_mcp ;;
    langfuse) run_check "$check" check_langfuse ;;
    observability) run_check "$check" check_observability ;;
    logs) run_check "$check" check_logs ;;
  esac
done

printf '==========================================\n'
printf 'Summary: %d passed, %d failed, %d skipped\n' "$PASS_COUNT" "$FAIL_COUNT" "$SKIP_COUNT"
[[ -z "$SKIPPED_CHECKS" ]] || printf 'Skipped (documented prerequisite absent):%s\n' "$SKIPPED_CHECKS"
[[ -z "$FAILED_NAMES" ]] || printf 'Failed:%s\n' "$FAILED_NAMES"

if [[ "$FAIL_COUNT" -gt 0 ]]; then
  exit 1
fi
exit 0
