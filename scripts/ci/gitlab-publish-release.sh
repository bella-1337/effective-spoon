#!/bin/sh
# Publish a GitLab release bundle to the project's generic package registry.
#
# Runs in the GitLab `release` job before its `release:` keyword creates the
# release that links these two files. Refuses to touch an existing release.

set -eu

: "${CI_API_V4_URL:?must run in GitLab CI}"
: "${CI_PROJECT_ID:?must run in GitLab CI}"
: "${CI_JOB_TOKEN:?must run in GitLab CI}"
: "${CI_COMMIT_TAG:?must run in a tag pipeline}"

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
readonly ROOT
cd "$ROOT"

tag=$CI_COMMIT_TAG
archive="litellm-stack-${tag}.tar.gz"
package_url="${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/packages/generic/litellm-stack/${tag}"

status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --header "JOB-TOKEN: ${CI_JOB_TOKEN}" \
  "${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/releases/${tag}") || status=000
case "$status" in
  404) ;;
  200)
    echo "Release $tag already exists; refusing to replace it." >&2
    exit 1
    ;;
  000)
    echo "Could not reach the GitLab API at ${CI_API_V4_URL}." >&2
    exit 1
    ;;
  *)
    echo "Could not check for an existing release (HTTP $status)." >&2
    exit 1
    ;;
esac

sh scripts/ci/release-bundle.sh "$tag" dist

for file in "$archive" SHA256SUMS; do
  curl --silent --show-error --fail \
    --header "JOB-TOKEN: ${CI_JOB_TOKEN}" \
    --upload-file "dist/$file" \
    "${package_url}/${file}" >/dev/null
  echo "Uploaded ${file} to the generic package registry"
done
