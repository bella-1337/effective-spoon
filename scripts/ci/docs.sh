#!/bin/sh
# Build the versioned documentation site into public/.
#
# Usage: scripts/ci/docs.sh check
#        scripts/ci/docs.sh publish [SITE_URL]
#
#   check    Build every version, taking "dev" from the checked-out commit, so
#            a pull or merge request proves that its documentation builds.
#   publish  Build every version with "dev" from the default branch, for a
#            GitHub Pages or GitLab Pages deployment at SITE_URL.
#
# GitHub Actions runs this inside the pinned DOCS_IMAGE with `docker run`;
# GitLab CI uses the same image as the job image. Both need the full Git
# history with the default branch and every release tag fetched.

set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
readonly ROOT
cd "$ROOT"

usage() {
  echo "usage: $0 check | publish [SITE_URL]" >&2
  exit 2
}

[ "$#" -ge 1 ] || usage

if [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
  echo "The documentation site needs the full Git history; fetch with depth 0." >&2
  exit 2
fi

case "$1" in
  check)
    [ "$#" -eq 1 ] || usage
    python3 scripts/build-docs.py site --dev-ref HEAD --output public
    ;;
  publish)
    [ "$#" -le 2 ] || usage
    site_url=${2:-}
    if [ -n "$site_url" ]; then
      python3 scripts/build-docs.py site --output public --site-url "$site_url"
    else
      python3 scripts/build-docs.py site --output public
    fi
    ;;
  *)
    usage
    ;;
esac
