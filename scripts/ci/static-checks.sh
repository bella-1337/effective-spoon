#!/bin/sh
# Run one static check with the tool installed in the current image.
#
# GitHub Actions runs each check inside the pinned tool image with `docker run`;
# GitLab CI uses the same pinned image as the job image. Both call this script so
# the file sets, configuration files, and failure policy stay identical.

set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
readonly ROOT
cd "$ROOT"

# Lint images ship without Git, so prune the local-only directories that can
# hold untracked copies of repository files (CI tool state, Python caches).
find_repository_files() {
  find . \
    \( -path ./.git -o -path ./.gitlab-ci-local -o -path ./node_modules \
       -o -path ./.docs-cache \
       -o -name __pycache__ \) -prune \
    -o -type f \( "$@" \) -print \
    | sort
}

usage() {
  echo "usage: $0 shell|dockerfiles|python|workflows|secrets|dependencies" >&2
  exit 2
}

[ "$#" -eq 1 ] || usage

case "$1" in
  shell)
    echo "==> ShellCheck: every *.sh file in the repository"
    find_repository_files -name '*.sh' | xargs shellcheck --severity=style
    ;;
  dockerfiles)
    echo "==> Hadolint: every Dockerfile in the repository"
    find_repository_files -name Dockerfile -o -name '*.Dockerfile' \
      | xargs hadolint --config .hadolint.yaml
    ;;
  python)
    echo "==> Ruff: every Python file in the repository (ruff.toml)"
    # Ruff finds the files itself and honours .gitignore without Git; the
    # checkout is mounted read-only, so skip the cache.
    ruff check --no-cache --output-format concise .
    ;;
  workflows)
    echo "==> zizmor: GitHub Actions workflows and Dependabot configuration"
    # Without GH_TOKEN, zizmor skips its online audits (impostor commits,
    # known-vulnerable actions); GitHub Actions passes the job token.
    zizmor --no-progress --config .github/zizmor.yml .
    ;;
  secrets)
    echo "==> Gitleaks: full Git history"
    # A shallow clone would silently skip older commits.
    if [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
      echo "Secret scanning needs the full history; fetch with depth 0." >&2
      exit 2
    fi
    # The checkout is owned by the CI user, not the container user.
    GIT_CONFIG_COUNT=1 \
      GIT_CONFIG_KEY_0=safe.directory \
      GIT_CONFIG_VALUE_0="$ROOT" \
      gitleaks git --config .gitleaks.toml --redact --no-banner --exit-code 1 .
    ;;
  dependencies)
    echo "==> Trivy: fixable High and Critical findings in pinned Python dependencies"
    trivy fs \
      --scanners vuln \
      --severity HIGH,CRITICAL \
      --ignore-unfixed \
      --skip-dirs .gitlab-ci-local \
      --skip-dirs node_modules \
      --skip-dirs .docs-cache \
      --no-progress \
      --exit-code 1 \
      .
    ;;
  *)
    usage
    ;;
esac
