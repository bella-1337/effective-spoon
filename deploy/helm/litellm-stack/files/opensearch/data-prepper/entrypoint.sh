#!/bin/bash
# Render the Data Prepper configuration from environment variables into a
# private temporary directory, then start Data Prepper.
set -euo pipefail

password="${OPENSEARCH_INGEST_PASSWORD:-}"
if [[ ! "$password" =~ ^[A-Za-z0-9._~-]{16,}$ ]]; then
  echo "OPENSEARCH_INGEST_PASSWORD must be at least 16 URL-safe characters ([A-Za-z0-9._~-])" >&2
  exit 64
fi

umask 077
render_dir="$(mktemp -d /tmp/data-prepper.XXXXXX)"
sed "s/__OPENSEARCH_INGEST_PASSWORD__/${password}/g" /etc/data-prepper/pipelines.yaml \
  > "$render_dir/pipelines.yaml"

# The core API is only used inside the container; protect it with a random
# per-start credential so other services on the network cannot stop pipelines.
api_password="$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)"
cat > "$render_dir/data-prepper-config.yaml" <<YAML
ssl: false
serverPort: 4900
authentication:
  http_basic:
    username: data-prepper-local
    password: "${api_password}"
peer_forwarder:
  discovery_mode: local_node
YAML
unset password api_password OPENSEARCH_INGEST_PASSWORD

cd /usr/share/data-prepper
exec bin/data-prepper "$render_dir/pipelines.yaml" "$render_dir/data-prepper-config.yaml"
