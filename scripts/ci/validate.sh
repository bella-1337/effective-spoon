#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly ROOT
cd "$ROOT"

created_env=0
created_optional_env=0
created_secret=0

cleanup() {
  if ((created_secret)); then
    rm -f -- litellm/secrets/litellm-master-key
    rmdir -- litellm/secrets 2>/dev/null || true
  fi
  if ((created_env)); then
    rm -f -- .env
  fi
  if ((created_optional_env)); then
    rm -f -- .env.optional
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

if [[ ! -f .env.optional ]]; then
  cp -- .env.optional.example .env.optional
  chmod 600 .env.optional
  created_optional_env=1
fi

if [[ ! -f litellm/secrets/litellm-master-key ]]; then
  mkdir -p -- litellm/secrets
  printf '%s\n' 'sk-local-validation-placeholder' \
    > litellm/secrets/litellm-master-key
  chmod 600 litellm/secrets/litellm-master-key
  created_secret=1
fi

echo "==> Validating Python syntax and hermetic unit tests"
python3 -m compileall -q scripts litellm/modules gpu-host
python3 -m unittest discover -s scripts/tests -v

echo "==> Checking that every diagram was rendered from its current Mermaid source"
python3 scripts/render-diagrams.py --check

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

# GitLab CI: the root file includes every file under .gitlab/ci/, and every job
# and service image is a `*_IMAGE` variable pinned to a digest, so the pipeline
# cannot silently skip a file or move to a different image.
gitlab_ci = root / ".gitlab-ci.yml"
if gitlab_ci.is_file():
    include_pattern = re.compile(r"^\s*-\s*local:\s*[\"']?/?([^\s\"'#]+)")
    included = {
        match.group(1)
        for line in gitlab_ci.read_text(encoding="utf-8").splitlines()
        if (match := include_pattern.match(line))
    }
    gitlab_files = sorted(
        path.relative_to(root).as_posix()
        for pattern in ("*.yml", "*.yaml")
        for path in (root / ".gitlab" / "ci").rglob(pattern)
    )
    for missing in sorted(included - set(gitlab_files)):
        failures.append(f".gitlab-ci.yml: included file does not exist: {missing}")
    for orphan in sorted(set(gitlab_files) - included):
        failures.append(f".gitlab-ci.yml: {orphan} is not included, so its jobs never run")

    digest_reference = re.compile(r"^[^\s@\"']+@sha256:[0-9a-f]{64}$")
    variable_pattern = re.compile(r"^\s+([A-Z][A-Z0-9_]*_IMAGE):\s*\"?([^\s\"#]+)\"?\s*$")
    image_key = re.compile(r"^(\s*)(?:-\s*)?(image|services):\s*(.*?)\s*$")
    name_key = re.compile(r"^\s*(?:-\s*)?name:\s*\"?([^\s\"#]+)\"?\s*$")
    sources = {
        name: (root / name).read_text(encoding="utf-8").splitlines()
        for name in [".gitlab-ci.yml", *gitlab_files]
    }
    pinned: set[str] = set()
    for name, lines in sources.items():
        for line_number, line in enumerate(lines, 1):
            variable = variable_pattern.match(line)
            if variable:
                if not digest_reference.fullmatch(variable.group(2)):
                    failures.append(
                        f"{name}:{line_number}: {variable.group(1)} is not "
                        f"pinned to a sha256 digest: {variable.group(2)}"
                    )
                pinned.add(variable.group(1))

    def check_reference(location: str, reference: str) -> None:
        reference = reference.strip("\"'")
        variable = reference.removeprefix("$").strip("{}")
        if reference.startswith("$") and variable in pinned:
            return
        if digest_reference.fullmatch(reference):
            return
        failures.append(
            f"{location}: image is neither a pinned *_IMAGE "
            f"variable nor a sha256 digest: {reference}"
        )

    images_found = 0
    for name, lines in sources.items():
        block_indent: int | None = None
        for line_number, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            if block_indent is not None and indent <= block_indent and not stripped.startswith("- "):
                block_indent = None
            match = image_key.match(line)
            if match:
                value = match.group(3)
                if value and not value.startswith("#"):
                    if match.group(2) == "services":
                        failures.append(
                            f"{name}:{line_number}: list services as name: entries"
                        )
                    else:
                        check_reference(f"{name}:{line_number}", value)
                        images_found += 1
                else:
                    block_indent = len(match.group(1))
                continue
            if block_indent is not None and indent > block_indent:
                image_name = name_key.match(line)
                if image_name:
                    check_reference(f"{name}:{line_number}", image_name.group(1))
                    images_found += 1
    if not images_found:
        failures.append(".gitlab-ci.yml: no job images were found to verify")

if failures:
    raise SystemExit("\n".join(failures))
PY

echo "==> Validating every Compose profile and game-day safety contracts"
python3 scripts/game-day-preflight.py \
  --env-file .env \
  --optional-env-file .env.optional

echo "==> Checking tracked changes for whitespace errors"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git diff --check HEAD --
else
  echo "Git metadata is unavailable; skipping the advisory whitespace check."
fi

echo
echo "Hermetic validation passed."
echo "Deferred external-service checks: full stack startup, GPU models, provider APIs,"
echo "and live deployment probes. CI runs image and dependency vulnerability scans"
echo "separately (scripts/ci/images.py and scripts/ci/static-checks.sh)."
