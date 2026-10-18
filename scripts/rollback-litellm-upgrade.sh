#!/usr/bin/env bash

set -euo pipefail

umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
STATE_ROOT="${UPGRADE_STATE_DIR:-$ROOT/upgrade-state}"
CONFIRMED=false
MODE=""
STATE_DIR_ARG=""
BACKUP_DIR_ARG=""
SERVICES_ARG=""
ALLOW_CONFIG_DRIFT=false
STATE_DIR=""
LOCK_DIR=""
OVERRIDE_DIR=""
REPORT_FILE=""
APPLIED_OVERRIDE_FILE=""
RESULT="FAILURE"
FAILED_STAGE="argument-validation"

declare -a TARGET_SERVICES=()
declare -a CONFIG_FILES=()

CREATED_AT=""
SERVICES_CAPTURED=""
IMAGE_LITELLM_PROD=""
IMAGE_LITELLM_DEV=""
BACKUP_DIR_RECORDED=""

PREVIOUS_IMAGE_LITELLM_PROD=""
PREVIOUS_IMAGE_LITELLM_DEV=""
ACTION_LITELLM_PROD="not-attempted"
ACTION_LITELLM_DEV="not-attempted"
LIVELINESS_LITELLM_PROD="not-checked"
LIVELINESS_LITELLM_DEV="not-checked"

usage() {
  cat <<'EOF'
Usage:
  scripts/rollback-litellm-upgrade.sh --confirm-rollback --mode MODE [options]

Rolls back LiteLLM (litellm-prod / litellm-dev) to the image reference
captured by scripts/prepare-litellm-upgrade.sh. Nothing in docker-compose.yml
is modified; the rollback image is applied through a validated, temporary
Compose override file generated under the upgrade-state directory.

Required:
  --confirm-rollback   Mandatory explicit confirmation.
  --mode MODE          One of:
                          image-only   Swap the running container image back
                                       to the captured digest. Use this only
                                       when the upgrade did not make the
                                       database schema incompatible with the
                                       prior image.
                          restore-db   Stop all application services, restore
                                       the database backup via
                                       scripts/restore-databases.sh, then
                                       bring the application back up pinned to
                                       the captured rollback image. Use this
                                       when the upgrade applied a schema
                                       migration that the prior image cannot
                                       read.

Options:
  --state DIR           Upgrade-state directory produced by
                         scripts/prepare-litellm-upgrade.sh
                         (default: the most recently prepared state under
                         ./upgrade-state).
  --backup DIR           Backup directory to restore (restore-db mode only).
                         Defaults to the backup recorded at prepare time.
  --services LIST        Comma-separated subset to roll back: prod, dev, or
                         the full names litellm-prod, litellm-dev (default:
                         every service captured in the state directory).
  --allow-config-drift   Proceed even if litellm/config.*.yaml or
                         litellm/modules content changed since the state was
                         captured (default: refuse and report the drift).
  -h, --help             Show this help text.

Environment:
  UPGRADE_STATE_DIR  Directory holding prepared state (default: ./upgrade-state)
EOF
}

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

warn() {
  printf 'WARNING: %s\n' "$1" >&2
}

checksum_check() {
  local manifest="$1"

  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum --check --quiet --strict -- "$manifest"
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 --check --quiet --strict -- "$manifest"
  else
    fail "sha256sum or shasum is required"
  fi
}

service_is_running() {
  local service="$1"

  docker compose ps --services --status running | grep -Fqx "$service"
}

normalize_service() {
  case "$1" in
    prod|litellm-prod)
      printf 'litellm-prod'
      ;;
    dev|litellm-dev)
      printf 'litellm-dev'
      ;;
    *)
      fail "unsupported service: $1 (expected prod, dev, litellm-prod, or litellm-dev)"
      ;;
  esac
}

get_target_image() {
  case "$1" in
    litellm-prod) printf '%s' "$IMAGE_LITELLM_PROD" ;;
    litellm-dev) printf '%s' "$IMAGE_LITELLM_DEV" ;;
    *) fail "internal error: unknown service $1" ;;
  esac
}

get_previous_image() {
  case "$1" in
    litellm-prod) printf '%s' "$PREVIOUS_IMAGE_LITELLM_PROD" ;;
    litellm-dev) printf '%s' "$PREVIOUS_IMAGE_LITELLM_DEV" ;;
    *) fail "internal error: unknown service $1" ;;
  esac
}

set_previous_image() {
  case "$1" in
    litellm-prod) PREVIOUS_IMAGE_LITELLM_PROD="$2" ;;
    litellm-dev) PREVIOUS_IMAGE_LITELLM_DEV="$2" ;;
    *) fail "internal error: unknown service $1" ;;
  esac
}

set_action() {
  case "$1" in
    litellm-prod) ACTION_LITELLM_PROD="$2" ;;
    litellm-dev) ACTION_LITELLM_DEV="$2" ;;
    *) fail "internal error: unknown service $1" ;;
  esac
}

get_action() {
  case "$1" in
    litellm-prod) printf '%s' "$ACTION_LITELLM_PROD" ;;
    litellm-dev) printf '%s' "$ACTION_LITELLM_DEV" ;;
    *) fail "internal error: unknown service $1" ;;
  esac
}

set_liveliness() {
  case "$1" in
    litellm-prod) LIVELINESS_LITELLM_PROD="$2" ;;
    litellm-dev) LIVELINESS_LITELLM_DEV="$2" ;;
    *) fail "internal error: unknown service $1" ;;
  esac
}

get_liveliness() {
  case "$1" in
    litellm-prod) printf '%s' "$LIVELINESS_LITELLM_PROD" ;;
    litellm-dev) printf '%s' "$LIVELINESS_LITELLM_DEV" ;;
    *) fail "internal error: unknown service $1" ;;
  esac
}

write_report() {
  local completed_at
  completed_at="$(date -u +%Y%m%dT%H%M%SZ)"

  {
    printf 'rollback_mode=%s\n' "$MODE"
    printf 'state_dir=%s\n' "$STATE_DIR"
    printf 'confirmed=%s\n' "$CONFIRMED"
    printf 'services_requested=%s\n' "$(
      local IFS=','
      printf '%s' "${TARGET_SERVICES[*]:-}"
    )"
    for service in "${TARGET_SERVICES[@]:-}"; do
      [[ -n "$service" ]] || continue
      printf 'service.%s.previous_image=%s\n' "$service" "$(get_previous_image "$service")"
      printf 'service.%s.target_image=%s\n' "$service" "$(get_target_image "$service")"
      printf 'service.%s.action=%s\n' "$service" "$(get_action "$service")"
      printf 'service.%s.liveliness=%s\n' "$service" "$(get_liveliness "$service")"
    done
    if [[ "$MODE" == "restore-db" ]]; then
      printf 'backup_dir=%s\n' "${RESOLVED_BACKUP_DIR:-}"
      printf 'database_restore_exit_code=%s\n' "${RESTORE_EXIT_CODE:-not-run}"
    fi
    printf 'applied_override=%s\n' "$APPLIED_OVERRIDE_FILE"
    printf 'result=%s\n' "$RESULT"
    if [[ "$RESULT" == "FAILURE" ]]; then
      printf 'failed_stage=%s\n' "$FAILED_STAGE"
    fi
    printf 'completed_at_utc=%s\n' "$completed_at"
  } >"$REPORT_FILE"

  printf '\nRollback report: %s\n' "$REPORT_FILE"
  cat "$REPORT_FILE"
}

cleanup() {
  local status=$?
  trap - EXIT

  if [[ -n "$REPORT_FILE" ]]; then
    write_report || true
  fi

  if [[ -n "$OVERRIDE_DIR" && -d "$OVERRIDE_DIR" ]]; then
    rm -f -- "$OVERRIDE_DIR/rollback-override.yml"
    rmdir -- "$OVERRIDE_DIR" 2>/dev/null || true
  fi

  if [[ -n "$LOCK_DIR" && -d "$LOCK_DIR" ]]; then
    rmdir -- "$LOCK_DIR" 2>/dev/null || true
  fi

  if [[ "$RESULT" == "SUCCESS" ]]; then
    exit 0
  fi
  if [[ "$status" -eq 0 ]]; then
    exit 1
  fi
  exit "$status"
}

collect_config_files() {
  local base_file
  local entry

  for base_file in litellm/config.prod.yaml litellm/config.dev.yaml; do
    [[ -f "$ROOT/$base_file" && ! -L "$ROOT/$base_file" ]] ||
      fail "expected configuration file is missing or unsafe: $base_file"
    printf '%s\n' "$base_file"
  done

  if [[ -d "$ROOT/litellm/modules" ]]; then
    while IFS= read -r entry; do
      printf '%s\n' "${entry#"$ROOT"/}"
    done < <(
      find "$ROOT/litellm/modules" -type f -not -name '.DS_Store' -print | sort
    )
  fi
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --confirm-rollback)
      CONFIRMED=true
      shift
      ;;
    --mode)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      MODE="$2"
      shift 2
      ;;
    --state)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      STATE_DIR_ARG="$2"
      shift 2
      ;;
    --backup)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      BACKUP_DIR_ARG="$2"
      shift 2
      ;;
    --services)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      [[ -n "$2" ]] || fail "--services requires a non-empty value"
      SERVICES_ARG="$2"
      shift 2
      ;;
    --allow-config-drift)
      ALLOW_CONFIG_DRIFT=true
      shift
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

[[ "$CONFIRMED" == true ]] || { usage >&2; exit 2; }
case "$MODE" in
  image-only|restore-db) ;;
  *) usage >&2; exit 2 ;;
esac
if [[ "$MODE" == "restore-db" && -n "$SERVICES_ARG" ]]; then
  fail "--services is not supported with restore-db because database restoration is stack-wide"
fi

command -v docker >/dev/null 2>&1 || fail "docker is required"
docker compose version >/dev/null 2>&1 || fail "docker compose is required"
if ! command -v sha256sum >/dev/null 2>&1 &&
  ! command -v shasum >/dev/null 2>&1; then
  fail "sha256sum or shasum is required"
fi

cd "$ROOT"
docker compose config --quiet

# ---------------------------------------------------------------------------
# Resolve and validate the prepared state directory
# ---------------------------------------------------------------------------
[[ -d "$STATE_ROOT" ]] || fail "no upgrade state directory found at $STATE_ROOT; run scripts/prepare-litellm-upgrade.sh first"
STATE_ROOT="$(cd "$STATE_ROOT" && pwd -P)"

if [[ -n "$STATE_DIR_ARG" ]]; then
  STATE_DIR="$STATE_DIR_ARG"
else
  [[ -f "$STATE_ROOT/LATEST" && ! -L "$STATE_ROOT/LATEST" ]] ||
    fail "no prepared upgrade state found; run scripts/prepare-litellm-upgrade.sh first or pass --state"
  latest_name="$(cat "$STATE_ROOT/LATEST")"
  [[ "$latest_name" =~ ^litellm-upgrade-[0-9]{8}T[0-9]{6}Z$ ]] ||
    fail "invalid LATEST pointer in $STATE_ROOT"
  STATE_DIR="$STATE_ROOT/$latest_name"
fi

[[ -d "$STATE_DIR" && ! -L "$STATE_DIR" ]] ||
  fail "state directory does not exist or is a symbolic link: $STATE_DIR"
STATE_DIR="$(cd "$STATE_DIR" && pwd -P)"
[[ "$(basename "$STATE_DIR")" =~ ^litellm-upgrade-[0-9]{8}T[0-9]{6}Z$ ]] ||
  fail "unexpected state directory name: $STATE_DIR"

FAILED_STAGE="state-validation"

while IFS= read -r -d '' entry; do
  case "$(basename "$entry")" in
    state.env|config-checksums.txt|SHA256SUMS)
      [[ -f "$entry" && ! -L "$entry" ]] ||
        fail "state entry must be a regular file: $(basename "$entry")"
      ;;
    reports)
      # Mutable output written by prior rollback runs (reports, applied
      # override copies). Not part of the trusted, checksummed input set.
      [[ -d "$entry" && ! -L "$entry" ]] ||
        fail "state entry must be a directory: $(basename "$entry")"
      ;;
    *)
      fail "unexpected entry in state directory: $(basename "$entry")"
      ;;
  esac
done < <(find "$STATE_DIR" -mindepth 1 -maxdepth 1 -print0)

for required in state.env config-checksums.txt SHA256SUMS; do
  [[ -f "$STATE_DIR/$required" && ! -L "$STATE_DIR/$required" ]] ||
    fail "state directory is missing $required"
done

(
  cd "$STATE_DIR"
  checksum_check SHA256SUMS
) || fail "state directory checksum verification failed; the prepared rollback point may be corrupted or tampered with"

# ---------------------------------------------------------------------------
# Parse state.env (strict allowlist, matching restore-databases.sh's style)
# ---------------------------------------------------------------------------
seen_keys="|"
while IFS='=' read -r key value; do
  [[ -n "$key" ]] || fail "invalid empty key in state.env"
  [[ "$seen_keys" != *"|$key|"* ]] || fail "duplicate key in state.env: $key"
  seen_keys+="$key|"

  case "$key" in
    created_at_utc) CREATED_AT="$value" ;;
    services_captured) SERVICES_CAPTURED="$value" ;;
    services_skipped) : ;;
    image.litellm-prod) IMAGE_LITELLM_PROD="$value" ;;
    image.litellm-dev) IMAGE_LITELLM_DEV="$value" ;;
    backup_dir) BACKUP_DIR_RECORDED="$value" ;;
    backup_skipped) : ;;
    config_checksum_manifest) : ;;
    *) fail "unsupported key in state.env: $key" ;;
  esac
done <"$STATE_DIR/state.env"

[[ "$CREATED_AT" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || fail "invalid or missing created_at_utc in state.env"
[[ -n "$SERVICES_CAPTURED" ]] || fail "state.env recorded no captured services"

# ---------------------------------------------------------------------------
# Determine target services
# ---------------------------------------------------------------------------
IFS=',' read -r -a captured_list <<<"$SERVICES_CAPTURED"
for svc in "${captured_list[@]}"; do
  case "$svc" in
    litellm-prod|litellm-dev) ;;
    *) fail "unsupported service recorded in state.env: $svc" ;;
  esac
done

if [[ -n "$SERVICES_ARG" ]]; then
  IFS=',' read -r -a requested <<<"$SERVICES_ARG"
  for raw in "${requested[@]}"; do
    normalized="$(normalize_service "$raw")"
    match=false
    for svc in "${captured_list[@]}"; do
      [[ "$svc" == "$normalized" ]] && match=true
    done
    [[ "$match" == true ]] ||
      fail "no captured rollback image for service '$normalized'; rerun scripts/prepare-litellm-upgrade.sh"
    TARGET_SERVICES+=("$normalized")
  done
else
  TARGET_SERVICES=("${captured_list[@]}")
fi

[[ "${#TARGET_SERVICES[@]}" -gt 0 ]] || fail "no target services resolved for rollback"

for svc in "${TARGET_SERVICES[@]}"; do
  image_value="$(get_target_image "$svc")"
  [[ "$image_value" =~ ^[A-Za-z0-9._/:-]+@sha256:[0-9a-f]{64}$ ]] ||
    fail "recorded rollback image for $svc is not an immutable digest reference: $image_value"
done

# ---------------------------------------------------------------------------
# Configuration drift detection
# ---------------------------------------------------------------------------
FAILED_STAGE="configuration-drift-check"

while IFS= read -r entry; do
  CONFIG_FILES+=("$entry")
done < <(collect_config_files)

recorded_files="$(awk '{ $1=""; sub(/^ /, ""); print }' "$STATE_DIR/config-checksums.txt" | sort)"
current_files="$(printf '%s\n' "${CONFIG_FILES[@]}" | sort)"

drift_detected=false
if [[ "$recorded_files" != "$current_files" ]]; then
  drift_detected=true
  warn "the set of LiteLLM configuration files changed since the rollback point was prepared"
fi

if ! (cd "$ROOT" && checksum_check "$STATE_DIR/config-checksums.txt") >/dev/null 2>&1; then
  drift_detected=true
  warn "LiteLLM configuration content changed since the rollback point was prepared"
fi

if [[ "$drift_detected" == true ]]; then
  if [[ "$ALLOW_CONFIG_DRIFT" != true ]]; then
    fail "configuration drift detected since prepare; re-run scripts/prepare-litellm-upgrade.sh or pass --allow-config-drift to proceed knowingly"
  fi
  warn "proceeding despite configuration drift because --allow-config-drift was given"
fi

# ---------------------------------------------------------------------------
# Acquire an exclusive lock to prevent a racing rollback invocation
# ---------------------------------------------------------------------------
FAILED_STAGE="lock-acquisition"
LOCK_DIR="$STATE_ROOT/.rollback.lock"
mkdir -- "$LOCK_DIR" 2>/dev/null ||
  fail "another rollback appears to be in progress (lock present: $LOCK_DIR)"

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

mkdir -p -- "$STATE_DIR/reports"
REPORT_FILE="$STATE_DIR/reports/rollback-report-$(date -u +%Y%m%dT%H%M%SZ).txt"

OVERRIDE_DIR="$(mktemp -d "$STATE_ROOT/.litellm-rollback-override.XXXXXX")"

build_override_file() {
  local override_file="$1"
  shift
  local service

  {
    printf 'services:\n'
    for service in "$@"; do
      printf '  %s:\n' "$service"
      printf '    image: %s\n' "$(get_target_image "$service")"
    done
  } >"$override_file"
}

validate_override_file() {
  local override_file="$1"

  docker compose -f docker-compose.yml -f "$override_file" \
    --profile prod --profile dev config --quiet ||
    fail "generated rollback override file failed Compose validation"
}

persist_override_file() {
  local override_file="$1"

  APPLIED_OVERRIDE_FILE="$STATE_DIR/reports/active-rollback-override.yml"
  cp -- "$override_file" "$APPLIED_OVERRIDE_FILE"
  chmod 600 "$APPLIED_OVERRIDE_FILE"
}

stop_service_if_running() {
  local service="$1"

  if service_is_running "$service"; then
    printf 'Stopping %s...\n' "$service"
    docker compose stop "$service" >/dev/null
  fi
  if service_is_running "$service"; then
    fail "service $service did not stop; aborting to avoid an application/database race"
  fi
}

start_service_with_override() {
  local service="$1"
  local override_file="$2"
  local waited=0

  printf 'Starting %s with rollback image...\n' "$service"
  docker compose -f docker-compose.yml -f "$override_file" \
    --profile prod --profile dev up -d --no-deps "$service" >/dev/null

  while ! service_is_running "$service"; do
    sleep 2
    waited=$((waited + 2))
    [[ "$waited" -lt 60 ]] || fail "service $service did not start within 60 seconds"
  done
}

verify_running_image() {
  local service="$1"
  local expected="$2"
  local actual

  actual="$(docker compose ps --format '{{.Image}}' "$service")"
  [[ "$actual" == "$expected" ]] ||
    fail "post-rollback image mismatch for $service: expected $expected, got $actual"
}

check_liveliness() {
  local service="$1"
  local attempt=0

  while [[ "$attempt" -lt 15 ]]; do
    if docker compose exec -T "$service" python3 -c '
import sys, urllib.request
try:
    with urllib.request.urlopen("http://localhost:4000/health/liveliness", timeout=5) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
' >/dev/null 2>&1; then
      set_liveliness "$service" "pass"
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 2
  done

  set_liveliness "$service" "fail"
  return 1
}

for svc in "${TARGET_SERVICES[@]}"; do
  if service_is_running "$svc"; then
    PREV="$(docker compose ps --format '{{.Image}}' "$svc")"
  else
    PREV=""
  fi
  set_previous_image "$svc" "$PREV"
done

# ---------------------------------------------------------------------------
# Mode: image-only
# ---------------------------------------------------------------------------
if [[ "$MODE" == "image-only" ]]; then
  FAILED_STAGE="image-rollback"
  declare -a NEEDS_ROLLBACK=()

  for svc in "${TARGET_SERVICES[@]}"; do
    target="$(get_target_image "$svc")"
    current="$(get_previous_image "$svc")"
    if [[ -z "$current" ]]; then
      warn "$svc is not currently running; it will be started with the rollback image"
      NEEDS_ROLLBACK+=("$svc")
    elif [[ "$current" == "$target" ]]; then
      printf '%s is already running the rollback image; nothing to do\n' "$svc"
      set_action "$svc" "already-current"
    else
      NEEDS_ROLLBACK+=("$svc")
    fi
  done

  if [[ "${#NEEDS_ROLLBACK[@]}" -eq 0 ]]; then
    override_file="$OVERRIDE_DIR/rollback-override.yml"
    build_override_file "$override_file" "${TARGET_SERVICES[@]}"
    validate_override_file "$override_file"
    persist_override_file "$override_file"
    RESULT="SUCCESS"
  else
    override_file="$OVERRIDE_DIR/rollback-override.yml"
    build_override_file "$override_file" "${TARGET_SERVICES[@]}"
    validate_override_file "$override_file"

    for svc in "${NEEDS_ROLLBACK[@]}"; do
      stop_service_if_running "$svc"
      start_service_with_override "$svc" "$override_file"
      verify_running_image "$svc" "$(get_target_image "$svc")"
      if check_liveliness "$svc"; then
        set_action "$svc" "rolled-back"
      else
        set_action "$svc" "rolled-back-liveliness-failed"
        fail "service $svc was rolled back to the prior image but failed its liveliness check"
      fi
    done

    persist_override_file "$override_file"
    RESULT="SUCCESS"
  fi
fi

# ---------------------------------------------------------------------------
# Mode: restore-db
# ---------------------------------------------------------------------------
if [[ "$MODE" == "restore-db" ]]; then
  FAILED_STAGE="backup-resolution"

  if [[ -n "$BACKUP_DIR_ARG" ]]; then
    RESOLVED_BACKUP_DIR="$BACKUP_DIR_ARG"
  elif [[ -n "$BACKUP_DIR_RECORDED" ]]; then
    RESOLVED_BACKUP_DIR="$BACKUP_DIR_RECORDED"
  else
    fail "no backup recorded in state.env and none provided via --backup; a schema-incompatible rollback requires a database backup"
  fi

  [[ -d "$RESOLVED_BACKUP_DIR" && ! -L "$RESOLVED_BACKUP_DIR" ]] ||
    fail "backup directory does not exist or is a symbolic link: $RESOLVED_BACKUP_DIR"

  FAILED_STAGE="application-stop"
  was_running_pgvector=false
  service_is_running litellm-pgvector && was_running_pgvector=true

  for svc in litellm-prod litellm-dev litellm-pgvector; do
    stop_service_if_running "$svc"
  done

  FAILED_STAGE="database-startup"
  docker compose up -d --wait postgres vector-postgres >/dev/null ||
    fail "postgres and vector-postgres did not become healthy"

  FAILED_STAGE="database-restore"
  printf 'Restoring database backup: %s\n' "$RESOLVED_BACKUP_DIR"
  RESTORE_EXIT_CODE=0
  "$ROOT/scripts/restore-databases.sh" --confirm-restore "$RESOLVED_BACKUP_DIR" ||
    RESTORE_EXIT_CODE=$?

  if [[ "$RESTORE_EXIT_CODE" -ne 0 ]]; then
    fail "database restore failed (exit code $RESTORE_EXIT_CODE); application services were left stopped to avoid running against an inconsistent database"
  fi

  FAILED_STAGE="image-rollback"
  override_file="$OVERRIDE_DIR/rollback-override.yml"
  build_override_file "$override_file" "${TARGET_SERVICES[@]}"
  validate_override_file "$override_file"

  for svc in "${TARGET_SERVICES[@]}"; do
    start_service_with_override "$svc" "$override_file"
    verify_running_image "$svc" "$(get_target_image "$svc")"
    if check_liveliness "$svc"; then
      set_action "$svc" "rolled-back"
    else
      set_action "$svc" "rolled-back-liveliness-failed"
      fail "service $svc was restored and rolled back but failed its liveliness check"
    fi
  done

  persist_override_file "$override_file"

  if [[ "$was_running_pgvector" == true ]]; then
    FAILED_STAGE="pgvector-restart"
    printf 'Restarting litellm-pgvector...\n'
    docker compose up -d --no-deps litellm-pgvector >/dev/null
    waited=0
    while ! service_is_running litellm-pgvector; do
      sleep 2
      waited=$((waited + 2))
      [[ "$waited" -lt 60 ]] || fail "litellm-pgvector did not start within 60 seconds"
    done
  fi

  RESULT="SUCCESS"
fi

printf 'Rollback completed successfully (mode: %s)\n' "$MODE"
if [[ -n "$APPLIED_OVERRIDE_FILE" ]]; then
  printf 'Saved rollback override: %s\n' "$APPLIED_OVERRIDE_FILE"
  printf 'Before the next default docker compose up, either pin these rollback images in docker-compose.yml or include this override with -f.\n'
fi
