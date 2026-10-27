#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly ROOT
cd "$ROOT"

if [[ ! -f .env ]]; then
  cp -- .env.example .env
  chmod 600 .env
  echo "Created .env from .env.example; replace placeholders before starting services."
fi

mkdir -p -- \
  backups \
  litellm/secrets \
  security-metrics \
  security-reports \
  security-state \
  traefik/certs \
  upgrade-state

echo "Development container ready. Run ./scripts/ci/validate.sh before committing."
