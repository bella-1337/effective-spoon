#!/bin/sh
# Static checks of the Helm chart; none of them needs a cluster.
#
# Usage: scripts/ci/chart-checks.sh
#
# Runs helm lint and helm template for the default values and for
# deploy/ci/values-full.yaml, validates both renders with kubeconform against
# Kubernetes 1.27.0 (the chart's kubeVersion floor) and 1.30.0, and checks
# values.yaml for duplicate keys with yamllint. Every check runs even when an
# earlier one fails; the exit status is non-zero if any failed.
#
# Needs helm and kubeconform on PATH, and uvx for yamllint. kubeconform fetches
# its schemas from raw.githubusercontent.com unless it has a local cache.

set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
readonly ROOT
cd "$ROOT"

readonly CHART=deploy/helm/litellm-stack
readonly FULL_VALUES=deploy/ci/values-full.yaml
readonly YAMLLINT=yamllint==1.38.0
readonly K8S_VERSIONS="1.27.0 1.30.0"

for tool in helm kubeconform uvx; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "$tool is required and was not found on PATH." >&2
    exit 2
  fi
done

WORK=$(mktemp -d)
readonly WORK
trap 'rm -rf "$WORK"' EXIT INT TERM

failures=0

# run LABEL COMMAND...: runs the command and records a failure without exiting.
run() {
  label=$1
  shift
  echo "==> $label"
  if ! "$@"; then
    echo "FAILED: $label" >&2
    failures=$((failures + 1))
    return 1
  fi
}

run "helm lint (default values)" helm lint "$CHART" || true
run "helm lint (values-full.yaml)" helm lint "$CHART" -f "$FULL_VALUES" || true

render_default=false
render_full=false
if run "helm template (default values)" \
  sh -c 'helm template ci "$1" --namespace stack > "$2"' sh "$CHART" "$WORK/default.yaml"; then
  render_default=true
fi
if run "helm template (values-full.yaml)" \
  sh -c 'helm template ci "$1" --namespace stack -f "$2" > "$3"' sh "$CHART" "$FULL_VALUES" "$WORK/full.yaml"; then
  render_full=true
fi

for version in $K8S_VERSIONS; do
  if [ "$render_default" = true ]; then
    run "kubeconform $version (default values)" \
      kubeconform -strict -summary -kubernetes-version "$version" "$WORK/default.yaml" || true
  fi
  if [ "$render_full" = true ]; then
    run "kubeconform $version (values-full.yaml)" \
      kubeconform -strict -summary -kubernetes-version "$version" "$WORK/full.yaml" || true
  fi
done

run "yamllint key-duplicates" \
  uvx "$YAMLLINT" -d '{rules: {key-duplicates: enable}}' \
  "$CHART/values.yaml" "$FULL_VALUES" || true

if [ "$failures" -ne 0 ]; then
  echo "$failures check(s) failed." >&2
  exit 1
fi
echo "All chart checks passed."
