#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
BACKUP_ROOT="${BACKUP_DIR:-$ROOT/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_NAME="litellm-stack-$TIMESTAMP"
STAGING_DIR=""
BACKED_UP_DATABASES=""
SKIPPED_DATABASES=""
BACKED_UP_COUNT=0
SKIPPED_COUNT=0
declare -a DUMP_FILES=()

usage() {
  cat <<'EOF'
Usage: scripts/backup-databases.sh

Creates logical backups of:
  - litellm_prod
  - litellm_dev
  - vector_store

Unavailable services and missing databases are recorded and skipped. The
command fails when no configured database can be backed up.

Environment:
  BACKUP_DIR             Destination directory (default: ./backups)
  BACKUP_RETENTION_DAYS  Remove complete backups older than this many days
                         (default: 14)
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
      "$STAGING_DIR/litellm_prod.dump" \
      "$STAGING_DIR/litellm_dev.dump" \
      "$STAGING_DIR/vector_store.dump" \
      "$STAGING_DIR/metadata.txt" \
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

database_exists() {
  local service="$1"
  local database="$2"
  local database_names
  local name

  if ! database_names="$(
    docker compose exec -T "$service" \
      sh -ec 'exec psql --username "$POSTGRES_USER" --dbname postgres --list --tuples-only --no-align'
  )"; then
    return 2
  fi

  while IFS='|' read -r name _; do
    if [[ "$name" == "$database" ]]; then
      return 0
    fi
  done <<<"$database_names"

  return 1
}

dump_database() {
  local service="$1"
  local database="$2"
  local destination="$3"

  printf 'Backing up %s from %s...\n' "$database" "$service"
  docker compose exec -T "$service" sh -ec \
    'exec pg_dump --username "$POSTGRES_USER" --dbname "$1" --format=custom --compress=9 --lock-wait-timeout=30s --no-owner --no-privileges' \
    sh "$database" >"$destination"

  docker compose exec -T "$service" pg_restore --list \
    <"$destination" >/dev/null
}

record_backed_up_database() {
  local database="$1"

  if [[ -n "$BACKED_UP_DATABASES" ]]; then
    BACKED_UP_DATABASES+=","
  fi
  BACKED_UP_DATABASES+="$database"
  ((BACKED_UP_COUNT += 1))
}

record_skipped_database() {
  local database="$1"
  local reason="$2"

  if [[ -n "$SKIPPED_DATABASES" ]]; then
    SKIPPED_DATABASES+=","
  fi
  SKIPPED_DATABASES+="$database:$reason"
  ((SKIPPED_COUNT += 1))
}

backup_database_if_available() {
  local service="$1"
  local database="$2"
  local filename="$3"
  local existence_status

  if ! service_is_running "$service"; then
    printf 'WARNING: skipping %s because service %s is unavailable\n' \
      "$database" "$service" >&2
    record_skipped_database "$database" service-unavailable
    return 0
  fi

  if database_exists "$service" "$database"; then
    :
  else
    existence_status=$?
    if [[ "$existence_status" -eq 1 ]]; then
      printf 'WARNING: skipping %s because it does not exist in service %s\n' \
        "$database" "$service" >&2
      record_skipped_database "$database" database-not-found
      return 0
    fi
    fail "could not query databases in service '$service'"
  fi

  if ! dump_database "$service" "$database" "$STAGING_DIR/$filename"; then
    fail "backup failed for existing database '$database' in service '$service'"
  fi

  record_backed_up_database "$database"
  DUMP_FILES+=("$filename")
}

prune_old_backups() {
  local candidate
  local entry
  local safe
  local dump_count
  local -a entries

  while IFS= read -r -d '' candidate; do
    if [[ "$(basename "$candidate")" =~ ^litellm-stack-[0-9]{8}T[0-9]{6}Z$ ]]; then
      safe=true
      dump_count=0
      entries=()

      while IFS= read -r -d '' entry; do
        entries+=("$entry")
        case "$(basename "$entry")" in
          litellm_prod.dump|litellm_dev.dump|vector_store.dump)
            [[ -f "$entry" && ! -L "$entry" ]] || safe=false
            ((dump_count += 1))
            ;;
          metadata.txt|SHA256SUMS)
            [[ -f "$entry" && ! -L "$entry" ]] || safe=false
            ;;
          *)
            safe=false
            ;;
        esac
      done < <(find "$candidate" -mindepth 1 -maxdepth 1 -print0)

      for entry in "$candidate/metadata.txt" "$candidate/SHA256SUMS"; do
        [[ -f "$entry" && ! -L "$entry" ]] || safe=false
      done
      ((dump_count > 0)) || safe=false

      if [[ "$safe" == true ]]; then
        if ((${#entries[@]} > 0)); then
          rm -f -- "${entries[@]}"
        fi
        rmdir -- "$candidate"
        printf 'Removed expired backup %s\n' "$candidate"
      else
        printf 'WARNING: skipped retention cleanup for unexpected contents in %s\n' \
          "$candidate" >&2
      fi
    fi
  done < <(
    find "$BACKUP_ROOT" \
      -mindepth 1 \
      -maxdepth 1 \
      -type d \
      -name 'litellm-stack-*' \
      -mtime "+$RETENTION_DAYS" \
      -print0
  )
}

case "${1:-}" in
  "")
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

[[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] ||
  fail "BACKUP_RETENTION_DAYS must be a non-negative integer"

command -v docker >/dev/null 2>&1 || fail "docker is required"
docker compose version >/dev/null 2>&1 || fail "docker compose is required"
if ! command -v sha256sum >/dev/null 2>&1 &&
  ! command -v shasum >/dev/null 2>&1; then
  fail "sha256sum or shasum is required"
fi

umask 077
mkdir -p -- "$BACKUP_ROOT"
BACKUP_ROOT="$(cd "$BACKUP_ROOT" && pwd -P)"
[[ "$BACKUP_ROOT" != "/" ]] || fail "BACKUP_DIR must not be the filesystem root"

chmod 700 "$BACKUP_ROOT"
cd "$ROOT"

docker compose config --quiet

FINAL_DIR="$BACKUP_ROOT/$BACKUP_NAME"
[[ ! -e "$FINAL_DIR" ]] || fail "backup destination already exists: $FINAL_DIR"

STAGING_DIR="$(mktemp -d "$BACKUP_ROOT/.litellm-stack-backup.XXXXXX")"
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

backup_database_if_available postgres litellm_prod litellm_prod.dump
backup_database_if_available postgres litellm_dev litellm_dev.dump
backup_database_if_available vector-postgres vector_store vector_store.dump

((BACKED_UP_COUNT > 0)) ||
  fail "no configured databases were available to back up"

cat >"$STAGING_DIR/metadata.txt" <<EOF
created_at_utc=$TIMESTAMP
format=postgresql-custom
databases_backed_up=$BACKED_UP_DATABASES
databases_skipped=$SKIPPED_DATABASES
EOF

(
  cd "$STAGING_DIR"
  checksum_files "${DUMP_FILES[@]}" metadata.txt >SHA256SUMS
)

mv -- "$STAGING_DIR" "$FINAL_DIR"
STAGING_DIR=""

prune_old_backups

printf 'Backup completed: %s\n' "$FINAL_DIR"
if ((SKIPPED_COUNT > 0)); then
  printf 'Completed with skipped resources: %s\n' "$SKIPPED_DATABASES"
fi
printf 'Verify with: cd %q && ' "$FINAL_DIR"
if command -v sha256sum >/dev/null 2>&1; then
  printf 'sha256sum --check SHA256SUMS\n'
else
  printf 'shasum -a 256 --check SHA256SUMS\n'
fi
