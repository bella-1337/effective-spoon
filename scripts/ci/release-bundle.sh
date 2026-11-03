#!/bin/sh
# Build the immutable source release bundle and its checksum manifest.
#
# Usage: scripts/ci/release-bundle.sh vMAJOR.MINOR.PATCH [output-directory]
# Writes litellm-stack-<tag>.tar.gz and SHA256SUMS to the output directory
# (default: dist). Both GitHub Actions and GitLab CI publish these two files.

set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
readonly ROOT
cd "$ROOT"

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: $0 vMAJOR.MINOR.PATCH [output-directory]" >&2
  exit 2
fi

tag=$1
output=${2:-dist}

if ! printf '%s\n' "$tag" | grep -Eqx 'v[0-9]+\.[0-9]+\.[0-9]+'; then
  echo "Release tag must match vMAJOR.MINOR.PATCH" >&2
  exit 2
fi

if ! git rev-parse --verify --quiet "refs/tags/${tag}^{commit}" >/dev/null; then
  echo "Release tag does not exist in this checkout: $tag" >&2
  exit 2
fi

archive="litellm-stack-${tag}.tar.gz"
mkdir -p -- "$output"
rm -f -- "$output/$archive" "$output/SHA256SUMS"

git archive \
  --format=tar.gz \
  --prefix="litellm-stack-${tag}/" \
  --output="$output/$archive" \
  "refs/tags/$tag"

(cd -- "$output" && sha256sum -- "$archive" > SHA256SUMS)

echo "Release bundle written to $output/:"
cat -- "$output/SHA256SUMS"
