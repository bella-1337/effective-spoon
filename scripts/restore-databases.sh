#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CONFIRMATION_FLAG="--confirm-restore"
BACKUP_DIR=""
VERIFIED_FILES="|"
METADATA_DATABASES=""
METADATA_FORMAT=""
RESTORED_DATABASES=""
FAILED_DATABASES=""
RESTORED_COUNT=0
FAILED_COUNT=0

usage() {
  cat <<'EOF'
Usage:
  scripts/restore-databases.sh --confirm-restore BACKUP_DIRECTORY

Restores databases present in a backup created by backup-databases.sh.

This operation drops and recreates each target database found in the backup.
Start only postgres and vector-postgres in the target Compose project. LiteLLM
and the pgvector connector must remain stopped until restoration is complete.
EOF
}

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

append_csv() {
  local variable_name="$1"
  local value="$2"
  local current_value="${!variable_name}"

  if [[ -n "$current_value" ]]; then
    printf -v "$variable_name" '%s,%s' "$current_value" "$value"
  else
    printf -v "$variable_name" '%s' "$value"
  fi
}

checksum_file() {
  local path="$1"
  local output

  if command -v sha256sum >/dev/null 2>&1; then
    output="$(sha256sum "$path")"
  elif command -v shasum >/dev/null 2>&1; then
    output="$(shasum -a 256 "$path")"
  else
    fail "sha256sum or shasum is required"
  fi

  printf '%s' "${output%% *}"
}

verify_backup_contents() {
  local entry
  local filename

  while IFS= read -r -d '' entry; do
    filename="$(basename "$entry")"
    case "$filename" in
      litellm_prod.dump|litellm_dev.dump|vector_store.dump|metadata.txt|SHA256SUMS)
        [[ -f "$entry" && ! -L "$entry" ]] ||
          fail "backup entry must be a regular file: $filename"
        ;;
      *)
        fail "unexpected backup entry: $filename"
        ;;
    esac
  done < <(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -print0)

  [[ -f "$BACKUP_DIR/metadata.txt" && ! -L "$BACKUP_DIR/metadata.txt" ]] ||
    fail "metadata.txt is missing or unsafe"
  [[ -f "$BACKUP_DIR/SHA256SUMS" && ! -L "$BACKUP_DIR/SHA256SUMS" ]] ||
    fail "SHA256SUMS is missing or unsafe"
}

verify_checksum_manifest() {
  local expected
  local filename
  local extra
  local actual
  local entries=0

  while read -r expected filename extra; do
    [[ -n "$expected" && -n "$filename" && -z "${extra:-}" ]] ||
      fail "invalid SHA256SUMS entry"
    [[ "$expected" =~ ^[0-9a-f]{64}$ ]] ||
      fail "invalid SHA-256 value for $filename"

    case "$filename" in
      litellm_prod.dump|litellm_dev.dump|vector_store.dump|metadata.txt)
        ;;
      *)
        fail "unsafe filename in SHA256SUMS: $filename"
        ;;
    esac

    [[ "$VERIFIED_FILES" != *"|$filename|"* ]] ||
      fail "duplicate SHA256SUMS entry: $filename"
    [[ -f "$BACKUP_DIR/$filename" && ! -L "$BACKUP_DIR/$filename" ]] ||
      fail "checksummed file is missing or unsafe: $filename"

    actual="$(checksum_file "$BACKUP_DIR/$filename")"
    [[ "$actual" == "$expected" ]] ||
      fail "checksum mismatch for $filename"

    VERIFIED_FILES+="$filename|"
    ((entries += 1))
  done <"$BACKUP_DIR/SHA256SUMS"

  ((entries > 0)) || fail "SHA256SUMS is empty"
  [[ "$VERIFIED_FILES" == *"|metadata.txt|"* ]] ||
    fail "metadata.txt is not covered by SHA256SUMS"
}

read_metadata() {
  local key
  local value
  local created_at=""
  local skipped_databases=""
  local seen_keys="|"

  while IFS='=' read -r key value; do
    [[ -n "$key" ]] || fail "invalid empty metadata key"
    [[ "$seen_keys" != *"|$key|"* ]] || fail "duplicate metadata key: $key"
    seen_keys+="$key|"

    case "$key" in
      created_at_utc)
        created_at="$value"
        ;;
      format)
        METADATA_FORMAT="$value"
        ;;
      databases_backed_up)
        METADATA_DATABASES="$value"
        ;;
      databases_skipped)
        skipped_databases="$value"
        ;;
      *)
        fail "unsupported metadata key: $key"
        ;;
    esac
  done <"$BACKUP_DIR/metadata.txt"

  [[ "$created_at" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] ||
    fail "invalid or missing created_at_utc metadata"
  [[ "$METADATA_FORMAT" == "postgresql-custom" ]] ||
    fail "unsupported or missing backup format"
  [[ -n "$METADATA_DATABASES" ]] ||
    fail "backup metadata lists no completed databases"

  # The skipped list is informational, but reading it ensures the generated
  # metadata schema remains explicit and does not silently accept omissions.
  [[ "$seen_keys" == *"|databases_skipped|"* ]] ||
    fail "databases_skipped metadata is missing"
  : "$skipped_databases"
}

metadata_includes_database() {
  local database="$1"

  [[ ",$METADATA_DATABASES," == *",$database,"* ]]
}

verify_database_manifest() {
  local database
  local filename
  local listed_databases="|"
  local -a databases

  IFS=',' read -r -a databases <<<"$METADATA_DATABASES"
  for database in "${databases[@]}"; do
    case "$database" in
      litellm_prod|litellm_dev|vector_store)
        ;;
      *)
        fail "unsupported database in metadata: $database"
        ;;
    esac
    [[ "$listed_databases" != *"|$database|"* ]] ||
      fail "duplicate database in metadata: $database"
    listed_databases+="$database|"
  done

  for database in litellm_prod litellm_dev vector_store; do
    filename="$database.dump"
    if metadata_includes_database "$database"; then
      [[ "$VERIFIED_FILES" == *"|$filename|"* ]] ||
        fail "metadata lists $database but $filename is not verified"
    elif [[ "$VERIFIED_FILES" == *"|$filename|"* ]]; then
      fail "$filename exists in the manifest but not in metadata"
    fi
  done
}

service_is_running() {
  local service="$1"

  docker compose ps --services --status running | grep -Fqx "$service"
}

require_application_services_stopped() {
  local service

  for service in litellm-prod litellm-dev litellm-pgvector; do
    if service_is_running "$service"; then
      fail "application service '$service' must be stopped before restoration"
    fi
  done
}

remove_staging_database() {
  local service="$1"
  local staging_database="$2"

  if ! docker compose exec -T "$service" sh -ec \
    'exec dropdb --username "$POSTGRES_USER" --if-exists --force "$1"' \
    sh "$staging_database" >/dev/null; then
    printf 'WARNING: could not remove failed staging database %s\n' \
      "$staging_database" >&2
  fi
}

restore_database() {
  local service="$1"
  local database="$2"
  local archive="$3"
  local staging_database="${database}_restore_$$"

  if ! service_is_running "$service"; then
    printf 'ERROR: cannot restore %s because service %s is unavailable\n' \
      "$database" "$service" >&2
    return 1
  fi

  if ! docker compose exec -T "$service" pg_restore --list \
    <"$archive" >/dev/null; then
    printf 'ERROR: archive validation failed for %s\n' "$database" >&2
    return 1
  fi

  printf 'Restoring %s into staging database %s...\n' \
    "$database" "$staging_database"
  if ! docker compose exec -T "$service" sh -ec \
    'dropdb --username "$POSTGRES_USER" --if-exists --force "$1" &&
     createdb --username "$POSTGRES_USER" "$1"' \
    sh "$staging_database"; then
    printf 'ERROR: could not create staging database for %s\n' "$database" >&2
    return 1
  fi

  if ! docker compose exec -T "$service" sh -ec \
    'exec pg_restore --username "$POSTGRES_USER" --dbname "$1" --exit-on-error --single-transaction --no-owner --no-privileges' \
    sh "$staging_database" <"$archive"; then
    printf 'ERROR: staged restore failed for %s\n' "$database" >&2
    remove_staging_database "$service" "$staging_database"
    return 1
  fi

  if ! docker compose exec -T "$service" sh -ec \
    'table_count="$(psql --username "$POSTGRES_USER" --dbname "$1" --no-psqlrc --quiet --tuples-only --no-align --set ON_ERROR_STOP=1 --command "SELECT COUNT(*) FROM pg_tables WHERE schemaname = current_schema()")"
     case "$table_count" in ""|*[!0-9]*) exit 1 ;; esac
     test "$table_count" -gt 0' \
    sh "$staging_database"; then
    printf 'ERROR: staged restore validation failed for %s\n' \
      "$database" >&2
    remove_staging_database "$service" "$staging_database"
    return 1
  fi

  printf 'Replacing target database %s...\n' "$database"
  if ! docker compose exec -T "$service" sh -ec \
    'case "$1" in litellm_prod|litellm_dev|vector_store) ;; *) exit 2 ;; esac
     case "$2" in ""|*[!a-z0-9_]*) exit 2 ;; esac
     dropdb --username "$POSTGRES_USER" --if-exists --force "$1"
     psql --username "$POSTGRES_USER" --dbname postgres --no-psqlrc --set ON_ERROR_STOP=1 \
       --command "ALTER DATABASE \"$2\" RENAME TO \"$1\""' \
    sh "$database" "$staging_database"; then
    printf 'ERROR: could not promote staged restore for %s; recovered data remains in %s\n' \
      "$database" "$staging_database" >&2
    return 1
  fi

  if ! docker compose exec -T "$service" sh -ec \
    'exec psql --username "$POSTGRES_USER" --dbname "$1" --no-psqlrc --set ON_ERROR_STOP=1 --command "SELECT 1"' \
    sh "$database" >/dev/null; then
    printf 'ERROR: post-promotion connection check failed for %s\n' \
      "$database" >&2
    return 1
  fi

  return 0
}

restore_if_present() {
  local service="$1"
  local database="$2"

  if ! metadata_includes_database "$database"; then
    printf 'Skipping %s because it is not present in this backup\n' "$database"
    return 0
  fi

  if restore_database "$service" "$database" "$BACKUP_DIR/$database.dump"; then
    append_csv RESTORED_DATABASES "$database"
    ((RESTORED_COUNT += 1))
  else
    append_csv FAILED_DATABASES "$database"
    ((FAILED_COUNT += 1))
  fi
}

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
esac

[[ "$#" -eq 2 && "$1" == "$CONFIRMATION_FLAG" ]] || {
  usage >&2
  exit 2
}

BACKUP_DIR="$2"
[[ -d "$BACKUP_DIR" && ! -L "$BACKUP_DIR" ]] ||
  fail "backup directory does not exist or is a symbolic link"
BACKUP_DIR="$(cd "$BACKUP_DIR" && pwd -P)"
[[ "$BACKUP_DIR" != "/" ]] || fail "backup directory must not be the filesystem root"

command -v docker >/dev/null 2>&1 || fail "docker is required"
docker compose version >/dev/null 2>&1 || fail "docker compose is required"
if ! command -v sha256sum >/dev/null 2>&1 &&
  ! command -v shasum >/dev/null 2>&1; then
  fail "sha256sum or shasum is required"
fi

cd "$ROOT"
docker compose config --quiet

verify_backup_contents
verify_checksum_manifest
read_metadata
verify_database_manifest
require_application_services_stopped

restore_if_present postgres litellm_prod
restore_if_present postgres litellm_dev
restore_if_present vector-postgres vector_store

if ((FAILED_COUNT > 0)); then
  printf 'ERROR: restore completed with failures: %s\n' "$FAILED_DATABASES" >&2
  if ((RESTORED_COUNT > 0)); then
    printf 'Successfully restored before failure: %s\n' "$RESTORED_DATABASES"
  fi
  exit 1
fi

printf 'Restore completed successfully: %s\n' "$RESTORED_DATABASES"
