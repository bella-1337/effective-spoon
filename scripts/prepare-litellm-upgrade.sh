#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
STATE_ROOT="${UPGRADE_STATE_DIR:-$ROOT/upgrade-state}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
STATE_NAME="litellm-upgrade-$TIMESTAMP"
STAGING_DIR=""
SKIP_BACKUP=false
SERVICES_ARG="litellm-prod,litellm-dev"
declare -a TARGET_SERVICES=()
declare -a CONFIG_FILES=()
SERVICES_CAPTURED=""
SERVICES_SKIPPED=""
IMAGE_LITELLM_PROD=""
IMAGE_LITELLM_DEV=""
BACKUP_DIR_CAPTURED=""
BACKUP_SKIPPED="false"

usage() {
  cat <<'EOF'
Usage: scripts/prepare-litellm-upgrade.sh [--skip-backup] [--services LIST]

Captures a deterministic rollback point BEFORE a LiteLLM image upgrade is
applied, for later use by scripts/rollback-litellm-upgrade.sh.

For each running target service (default: litellm-prod, litellm-dev) this
records:
  - the exact, digest-pinned image reference currently deployed;
  - checksums of the repository-owned LiteLLM configuration currently in use
    (litellm/config.prod.yaml, litellm/config.dev.yaml, litellm/modules/**);
  - optionally, a fresh database backup via scripts/backup-databases.sh, so a
    schema-incompatible rollback can restore pre-upgrade data.

Nothing in docker-compose.yml, litellm/config.*.yaml, or litellm/modules is
modified. Secrets (.env values, DATABASE_URL, API keys) are never captured or
printed.

Options:
  --skip-backup     Do not create a pre-upgrade database backup. Only an
                     image-only rollback will be possible afterward; a
                     schema-incompatible rollback will require an explicit
                     --backup argument to scripts/rollback-litellm-upgrade.sh.
  --services LIST   Comma-separated services to capture: prod, dev, or the
                     full names litellm-prod, litellm-dev (default: both).
  -h, --help        Show this help text.

Environment:
  UPGRADE_STATE_DIR  Directory for captured state (default: ./upgrade-state)
  BACKUP_DIR         Passed through to scripts/backup-databases.sh
  BACKUP_RETENTION_DAYS  Passed through to scripts/backup-databases.sh
EOF
}

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

cleanup() {
  local status=$?
  trap - EXIT

  if [[ -n "$STAGING_DIR" && -d "$STAGING_DIR" ]]; then
    rm -f -- \
      "$STAGING_DIR/state.env" \
      "$STAGING_DIR/config-checksums.txt" \
      "$STAGING_DIR/SHA256SUMS"
    rmdir -- "$STAGING_DIR" 2>/dev/null || true
  fi

  exit "$status"
}

checksum_files() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$@"
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$@"
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

get_image_value() {
  case "$1" in
    litellm-prod) printf '%s' "$IMAGE_LITELLM_PROD" ;;
    litellm-dev) printf '%s' "$IMAGE_LITELLM_DEV" ;;
    *) fail "internal error: unknown service $1" ;;
  esac
}

set_image_value() {
  case "$1" in
    litellm-prod) IMAGE_LITELLM_PROD="$2" ;;
    litellm-dev) IMAGE_LITELLM_DEV="$2" ;;
    *) fail "internal error: unknown service $1" ;;
  esac
}

record_captured_service() {
  local service="$1"

  if [[ -n "$SERVICES_CAPTURED" ]]; then
    SERVICES_CAPTURED+=","
  fi
  SERVICES_CAPTURED+="$service"
}

record_skipped_service() {
  local service="$1"
  local reason="$2"

  if [[ -n "$SERVICES_SKIPPED" ]]; then
    SERVICES_SKIPPED+=","
  fi
  SERVICES_SKIPPED+="$service:$reason"
  printf 'WARNING: skipping %s (%s)\n' "$service" "$reason" >&2
}

collect_config_files() {
  local base_file
  local entry

  for base_file in \
    litellm/config.prod.yaml \
    litellm/config.dev.yaml; do
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

# Parse arguments.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-backup)
      SKIP_BACKUP=true
      shift
      ;;
    --services)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      [[ -n "$2" ]] || fail "--services requires a non-empty value"
      SERVICES_ARG="$2"
      shift 2
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

IFS=',' read -r -a _raw_services <<<"$SERVICES_ARG"
for _raw in "${_raw_services[@]}"; do
  TARGET_SERVICES+=("$(normalize_service "$_raw")")
done
unset _raw_services _raw

command -v docker >/dev/null 2>&1 || fail "docker is required"
docker compose version >/dev/null 2>&1 || fail "docker compose is required"
if ! command -v sha256sum >/dev/null 2>&1 &&
  ! command -v shasum >/dev/null 2>&1; then
  fail "sha256sum or shasum is required"
fi

cd "$ROOT"
docker compose config --quiet

umask 077
mkdir -p -- "$STATE_ROOT"
STATE_ROOT="$(cd "$STATE_ROOT" && pwd -P)"
[[ "$STATE_ROOT" != "/" ]] || fail "UPGRADE_STATE_DIR must not be the filesystem root"
chmod 700 "$STATE_ROOT"

FINAL_DIR="$STATE_ROOT/$STATE_NAME"
[[ ! -e "$FINAL_DIR" ]] || fail "state destination already exists: $FINAL_DIR"

STAGING_DIR="$(mktemp -d "$STATE_ROOT/.litellm-upgrade-prepare.XXXXXX")"
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

# Capture the currently deployed, digest-pinned image for each target service.
for service in "${TARGET_SERVICES[@]}"; do
  if ! service_is_running "$service"; then
    record_skipped_service "$service" service-not-running
    continue
  fi

  image_ref="$(docker compose ps --format '{{.Image}}' "$service")"
  [[ -n "$image_ref" ]] || fail "could not determine the deployed image for $service"
  [[ "$image_ref" =~ ^[A-Za-z0-9._/:-]+@sha256:[0-9a-f]{64}$ ]] ||
    fail "service '$service' is not running a digest-pinned image ($image_ref); refusing to capture an unsafe rollback point"

  container_id="$(docker compose ps -q "$service")"
  [[ -n "$container_id" ]] || fail "could not resolve a container id for $service"
  inspected_ref="$(docker inspect --format '{{.Config.Image}}' "$container_id")"
  [[ "$inspected_ref" == "$image_ref" ]] ||
    fail "image reference mismatch for $service between 'docker compose ps' and 'docker inspect'"

  set_image_value "$service" "$image_ref"
  record_captured_service "$service"
  printf 'Captured rollback image for %s: %s\n' "$service" "$image_ref"
done

[[ -n "$SERVICES_CAPTURED" ]] ||
  fail "no running target service was available to capture a rollback point for"

# Capture configuration checksums for drift detection at rollback time.
while IFS= read -r entry; do
  CONFIG_FILES+=("$entry")
done < <(collect_config_files)

(
  cd "$ROOT"
  checksum_files "${CONFIG_FILES[@]}"
) >"$STAGING_DIR/config-checksums.txt"

# Optionally create a pre-upgrade database backup for schema-incompatible
# rollback scenarios.
if [[ "$SKIP_BACKUP" == true ]]; then
  BACKUP_SKIPPED="true"
  printf 'WARNING: --skip-backup was used; schema-incompatible rollback will require an explicit --backup argument\n' >&2
else
  printf 'Creating pre-upgrade database backup...\n'
  if ! BACKUP_OUTPUT="$("$ROOT/scripts/backup-databases.sh")"; then
    fail "pre-upgrade backup failed; rerun with --skip-backup only if this is acceptable"
  fi
  printf '%s\n' "$BACKUP_OUTPUT"
  BACKUP_DIR_CAPTURED="$(
    printf '%s\n' "$BACKUP_OUTPUT" | sed -n 's/^Backup completed: //p' | tail -n1
  )"
  [[ -n "$BACKUP_DIR_CAPTURED" && -d "$BACKUP_DIR_CAPTURED" && ! -L "$BACKUP_DIR_CAPTURED" ]] ||
    fail "could not determine the backup directory from backup-databases.sh output"
  [[ -f "$BACKUP_DIR_CAPTURED/SHA256SUMS" && ! -L "$BACKUP_DIR_CAPTURED/SHA256SUMS" ]] ||
    fail "captured backup is missing its checksum manifest"
fi

{
  printf 'created_at_utc=%s\n' "$TIMESTAMP"
  printf 'services_captured=%s\n' "$SERVICES_CAPTURED"
  printf 'services_skipped=%s\n' "$SERVICES_SKIPPED"
  printf 'image.litellm-prod=%s\n' "$IMAGE_LITELLM_PROD"
  printf 'image.litellm-dev=%s\n' "$IMAGE_LITELLM_DEV"
  printf 'backup_dir=%s\n' "$BACKUP_DIR_CAPTURED"
  printf 'backup_skipped=%s\n' "$BACKUP_SKIPPED"
  printf 'config_checksum_manifest=config-checksums.txt\n'
} >"$STAGING_DIR/state.env"

(
  cd "$STAGING_DIR"
  checksum_files state.env config-checksums.txt >SHA256SUMS
)

mv -- "$STAGING_DIR" "$FINAL_DIR"
STAGING_DIR=""

printf '%s\n' "$STATE_NAME" >"$STATE_ROOT/.LATEST.tmp"
mv -- "$STATE_ROOT/.LATEST.tmp" "$STATE_ROOT/LATEST"

printf 'Prepared upgrade rollback point: %s\n' "$FINAL_DIR"
if [[ -n "$SERVICES_SKIPPED" ]]; then
  printf 'Skipped services: %s\n' "$SERVICES_SKIPPED"
fi
if [[ "$BACKUP_SKIPPED" == "true" ]]; then
  printf 'No database backup was captured (schema-incompatible rollback needs --backup).\n'
else
  printf 'Database backup for schema-incompatible rollback: %s\n' "$BACKUP_DIR_CAPTURED"
fi
printf '\nAfter upgrading, if rollback is needed:\n'
printf '  Image-only rollback:    scripts/rollback-litellm-upgrade.sh --confirm-rollback --mode image-only\n'
printf '  Schema-incompatible:     scripts/rollback-litellm-upgrade.sh --confirm-rollback --mode restore-db\n'
