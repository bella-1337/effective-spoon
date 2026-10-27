#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly ROOT
cd "$ROOT"

created_env=0
created_secret=0

cleanup() {
  if ((created_secret)); then
    rm -f -- litellm/secrets/litellm-master-key
    rmdir -- litellm/secrets 2>/dev/null || true
  fi
  if ((created_env)); then
    rm -f -- .env
  fi
}
trap cleanup EXIT HUP INT TERM

for command in docker python3; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Required command is unavailable: $command" >&2
    exit 127
  fi
done

python3 - <<'PY'
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required for local validation")
PY

if [[ ! -f .env ]]; then
  cp -- .env.example .env
  chmod 600 .env
  created_env=1
fi

if [[ ! -f litellm/secrets/litellm-master-key ]]; then
  mkdir -p -- litellm/secrets
  printf '%s\n' 'sk-local-validation-placeholder' \
    > litellm/secrets/litellm-master-key
  chmod 600 litellm/secrets/litellm-master-key
  created_secret=1
fi

echo "==> Validating Python syntax and hermetic unit tests"
python3 -m compileall -q scripts litellm/modules
python3 -m unittest discover -s scripts/tests -v

echo "==> Validating JSON, TOML, and immutable workflow dependencies"
python3 - <<'PY'
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

root = Path.cwd()

for path in sorted(root.rglob("*.json")):
    if ".git" in path.parts:
        continue
    with path.open(encoding="utf-8") as source:
        json.load(source)

for path in sorted(root.rglob("*.toml")):
    if ".git" in path.parts:
        continue
    with path.open("rb") as source:
        tomllib.load(source)

uses_pattern = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)")
sha_reference = re.compile(r"^[^@]+@[0-9a-f]{40}$")
failures: list[str] = []
for path in sorted((root / ".github" / "workflows").glob("*.y*ml")):
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = uses_pattern.match(line)
        if not match:
            continue
        reference = match.group(1)
        if reference.startswith("./"):
            continue
        if not sha_reference.fullmatch(reference):
            failures.append(
                f"{path.relative_to(root)}:{line_number}: action is not pinned "
                f"to a full commit SHA: {reference}"
            )

if failures:
    raise SystemExit("\n".join(failures))
PY

echo "==> Validating every Compose profile without starting services"
docker compose \
  --profile prod \
  --profile dev \
  --profile demo \
  --profile security \
  config --quiet

echo "==> Validating game-day safety contracts"
python3 scripts/game-day-preflight.py --env-file .env

echo "==> Checking tracked changes for whitespace errors"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git diff --check HEAD --
else
  echo "Git metadata is unavailable; skipping the advisory whitespace check."
fi

echo
echo "Hermetic validation passed."
echo "Deferred external-service checks: full stack startup, GPU models, provider APIs,"
echo "vulnerability database downloads, and live deployment probes."
