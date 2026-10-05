#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="$ROOT/grafana/dashboards"

mkdir -p \
  "$BASE/traefik" \
  "$BASE/postgres" \
  "$BASE/node" \
  "$BASE/litellm-official"

fetch_dashboard() {
  local url="$1"
  local destination="$2"

  curl --fail --silent --show-error --location \
    --output "$destination" \
    "$url"
}

fetch_dashboard \
  "https://grafana.com/api/dashboards/17346/revisions/latest/download" \
  "$BASE/traefik/traefik.json"

fetch_dashboard \
  "https://grafana.com/api/dashboards/9628/revisions/latest/download" \
  "$BASE/postgres/postgres.json"

fetch_dashboard \
  "https://grafana.com/api/dashboards/1860/revisions/latest/download" \
  "$BASE/node/node.json"

fetch_dashboard \
  "https://raw.githubusercontent.com/BerriAI/litellm/main/cookbook/litellm_proxy_server/grafana_dashboard/dashboard_all_metrics/grafana_dashboard.json" \
  "$BASE/litellm-official/grafana_dashboard.json"

echo "Dashboards fetched into $BASE"
