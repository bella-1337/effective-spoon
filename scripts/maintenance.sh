#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SCRIPTS_DIR="$ROOT/scripts"

usage() {
  cat <<'EOF'
Usage: scripts/maintenance.sh COMMAND [OPTIONS]

Central maintenance entry point for the LiteLLM stack.

Commands:
  doctor
      Validate required scripts, Bash syntax, Docker Compose, and both profiles.

  status
      Show all Compose containers and their current state.

  backup
      Run the fault-tolerant logical database backup.
      Configure BACKUP_DIR and BACKUP_RETENTION_DAYS through the environment.

  restore --confirm-restore BACKUP_DIRECTORY
      Restore a complete or partial backup into stopped application services.
      The explicit confirmation is enforced by restore-databases.sh.

  upgrade-check --candidate-image IMAGE_REF [--pull]
      Validate an immutable candidate image without touching live services.

  upgrade-prepare [--skip-backup] [--services LIST]
      Capture current image/config rollback state and, by default, a database
      backup immediately before changing the pinned LiteLLM image.

  pre-upgrade --candidate-image IMAGE_REF [--pull] [--skip-backup]
              [--services LIST]
      Run upgrade-check and then upgrade-prepare as one fail-fast workflow.

  upgrade-verify [--only LIST | --skip LIST | --list]
      Run the post-upgrade regression suite.

  post-upgrade [--only LIST | --skip LIST]
      Alias for upgrade-verify, intended for the deployment runbook.

  rollback --confirm-rollback --mode image-only|restore-db [OPTIONS]
      Run the validated rollback workflow. All confirmation, state, backup,
      drift, and service-selection options are delegated unchanged.

  help
      Show this help text.

Examples:
  scripts/maintenance.sh doctor
  scripts/maintenance.sh backup
  scripts/maintenance.sh pre-upgrade \
    --candidate-image ghcr.io/berriai/litellm-database:VERSION@sha256:DIGEST \
    --pull
  scripts/maintenance.sh post-upgrade
  scripts/maintenance.sh rollback --confirm-rollback --mode image-only

This wrapper never reads or prints secret values. Destructive operations still
require the explicit confirmation flags enforced by their underlying scripts.
EOF
}

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

usage_error() {
  printf 'ERROR: %s\n\n' "$1" >&2
  usage >&2
  exit 2
}

require_script() {
  local script="$1"

  [[ -f "$SCRIPTS_DIR/$script" && ! -L "$SCRIPTS_DIR/$script" ]] ||
    fail "required script is missing or unsafe: scripts/$script"
  [[ -x "$SCRIPTS_DIR/$script" ]] ||
    fail "required script is not executable: scripts/$script"
}

doctor() {
  local script
  local scripts="
maintenance.sh
backup-databases.sh
restore-databases.sh
verify-pgvector.sh
check-litellm-upgrade-compatibility.sh
prepare-litellm-upgrade.sh
verify-litellm-upgrade.sh
rollback-litellm-upgrade.sh
"

  command -v docker >/dev/null 2>&1 || fail "docker is required"
  docker compose version >/dev/null 2>&1 || fail "docker compose is required"

  while IFS= read -r script; do
    [[ -n "$script" ]] || continue
    require_script "$script"
    bash -n "$SCRIPTS_DIR/$script"
    printf 'PASS: scripts/%s is executable and has valid Bash syntax\n' "$script"
  done <<<"$scripts"

  (
    cd "$ROOT"
    docker compose --profile prod config --quiet
    docker compose --profile dev config --quiet
  )
  printf 'PASS: production and development Compose profiles are valid\n'
}

run_pre_upgrade() {
  local candidate_image=""
  local allow_pull=false
  local skip_backup=false
  local services=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --candidate-image)
        [[ $# -ge 2 ]] || usage_error "--candidate-image requires a value"
        [[ -n "$2" ]] || usage_error "--candidate-image requires a non-empty value"
        candidate_image="$2"
        shift 2
        ;;
      --candidate-image=*)
        candidate_image="${1#--candidate-image=}"
        [[ -n "$candidate_image" ]] ||
          usage_error "--candidate-image requires a non-empty value"
        shift
        ;;
      --pull)
        allow_pull=true
        shift
        ;;
      --skip-backup)
        skip_backup=true
        shift
        ;;
      --services)
        [[ $# -ge 2 ]] || usage_error "--services requires a value"
        [[ -n "$2" ]] || usage_error "--services requires a non-empty value"
        services="$2"
        shift 2
        ;;
      *)
        usage_error "unsupported pre-upgrade option: $1"
        ;;
    esac
  done

  [[ -n "$candidate_image" ]] ||
    usage_error "pre-upgrade requires --candidate-image"

  require_script check-litellm-upgrade-compatibility.sh
  require_script prepare-litellm-upgrade.sh

  printf '== Candidate compatibility ==\n'
  if [[ "$allow_pull" == true ]]; then
    "$SCRIPTS_DIR/check-litellm-upgrade-compatibility.sh" \
      --candidate-image "$candidate_image" \
      --pull
  else
    "$SCRIPTS_DIR/check-litellm-upgrade-compatibility.sh" \
      --candidate-image "$candidate_image"
  fi

  printf '\n== Rollback-point preparation ==\n'
  if [[ "$skip_backup" == true && -n "$services" ]]; then
    "$SCRIPTS_DIR/prepare-litellm-upgrade.sh" \
      --skip-backup \
      --services "$services"
  elif [[ "$skip_backup" == true ]]; then
    "$SCRIPTS_DIR/prepare-litellm-upgrade.sh" --skip-backup
  elif [[ -n "$services" ]]; then
    "$SCRIPTS_DIR/prepare-litellm-upgrade.sh" --services "$services"
  else
    "$SCRIPTS_DIR/prepare-litellm-upgrade.sh"
  fi
}

command_name="${1:-help}"
if [[ $# -gt 0 ]]; then
  shift
fi

case "$command_name" in
  help|-h|--help)
    [[ $# -eq 0 ]] || usage_error "help accepts no additional arguments"
    usage
    ;;
  doctor)
    [[ $# -eq 0 ]] || usage_error "doctor accepts no additional arguments"
    doctor
    ;;
  status)
    [[ $# -eq 0 ]] || usage_error "status accepts no additional arguments"
    command -v docker >/dev/null 2>&1 || fail "docker is required"
    (
      cd "$ROOT"
      docker compose ps --all
    )
    ;;
  backup)
    [[ $# -eq 0 ]] || usage_error "backup accepts no command-line arguments"
    require_script backup-databases.sh
    "$SCRIPTS_DIR/backup-databases.sh"
    ;;
  restore)
    require_script restore-databases.sh
    "$SCRIPTS_DIR/restore-databases.sh" "$@"
    ;;
  upgrade-check)
    require_script check-litellm-upgrade-compatibility.sh
    "$SCRIPTS_DIR/check-litellm-upgrade-compatibility.sh" "$@"
    ;;
  upgrade-prepare)
    require_script prepare-litellm-upgrade.sh
    "$SCRIPTS_DIR/prepare-litellm-upgrade.sh" "$@"
    ;;
  pre-upgrade)
    run_pre_upgrade "$@"
    ;;
  upgrade-verify|post-upgrade)
    require_script verify-litellm-upgrade.sh
    "$SCRIPTS_DIR/verify-litellm-upgrade.sh" "$@"
    ;;
  rollback)
    require_script rollback-litellm-upgrade.sh
    "$SCRIPTS_DIR/rollback-litellm-upgrade.sh" "$@"
    ;;
  *)
    usage_error "unknown command: $command_name"
    ;;
esac
