#!/bin/sh
set -eu

D3_ROUTER_IMAGE='ghcr.io/vllm-project/semantic-router/vllm-sr-cuda:latest@sha256:2fbea0e18f86d0d94c5bf4084019bbd0acbc9dc20919367baba8befd8e27fd63'
D3_TLS_IMAGE='docker.io/nginxinc/nginx-unprivileged:1.29.5-alpine@sha256:42a7d7f2ee23e9f5a1dcdf3647ba5c585bbd18f79e79cd817e70e8cd61c55779'
D3_MODEL='vllm-sr/d3-mini'
D3_MODEL_REVISION='a475367de0d8133a3c1a2a0dc12303d89cfa2ab4'

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

mode=${1:-deploy}
case "$mode" in
  deploy | --render-only) ;;
  *) fail "usage: scripts/deploy-d3-mini.sh [--render-only]" ;;
esac

require_file() {
  path=$1
  label=$2
  [ -f "$path" ] || fail "$label does not exist or is not a regular file: $path"
  [ ! -L "$path" ] || fail "$label must not be a symbolic link: $path"
}

command -v docker >/dev/null 2>&1 || fail "docker is required"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v curl >/dev/null 2>&1 || fail "curl is required"

[ -n "${D3_API_KEY:-}" ] || fail "D3_API_KEY is required"
[ -n "${D3_SERVER_NAME:-}" ] || fail "D3_SERVER_NAME is required"
[ -n "${D3_TLS_CERT_FILE:-}" ] || fail "D3_TLS_CERT_FILE is required"
[ -n "${D3_TLS_KEY_FILE:-}" ] || fail "D3_TLS_KEY_FILE is required"
[ -n "${D3_CA_CERT_FILE:-}" ] || fail "D3_CA_CERT_FILE is required"
require_file "$D3_TLS_CERT_FILE" "D3_TLS_CERT_FILE"
require_file "$D3_TLS_KEY_FILE" "D3_TLS_KEY_FILE"
require_file "$D3_CA_CERT_FILE" "D3_CA_CERT_FILE"

[ "${#D3_API_KEY}" -ge 32 ] || fail "D3_API_KEY must contain at least 32 characters"
case "$D3_API_KEY" in
  *'
'*) fail "D3_API_KEY must be a single line" ;;
esac
case "$D3_SERVER_NAME" in
  *[!A-Za-z0-9.-]* | .* | *.) fail "D3_SERVER_NAME must be a DNS hostname" ;;
esac

D3_DEPLOY_DIR=${D3_DEPLOY_DIR:-"$HOME/.local/share/d3-mini"}
D3_BIND_ADDRESS=${D3_BIND_ADDRESS:-0.0.0.0}
D3_PROBE_ADDRESS=$D3_BIND_ADDRESS
[ "$D3_PROBE_ADDRESS" != "0.0.0.0" ] || D3_PROBE_ADDRESS=127.0.0.1
D3_PUBLIC_PORT=${D3_PUBLIC_PORT:-8443}
D3_STARTUP_TIMEOUT=${D3_STARTUP_TIMEOUT:-1800}
D3_MEMORY_LIMIT=${D3_MEMORY_LIMIT:-16g}
D3_SHM_SIZE=${D3_SHM_SIZE:-4gb}

case "$D3_PUBLIC_PORT" in
  '' | *[!0-9]*) fail "D3_PUBLIC_PORT must be an integer" ;;
esac
[ "$D3_PUBLIC_PORT" -ge 1 ] && [ "$D3_PUBLIC_PORT" -le 65535 ] ||
  fail "D3_PUBLIC_PORT must be between 1 and 65535"
case "$D3_STARTUP_TIMEOUT" in
  '' | *[!0-9]*) fail "D3_STARTUP_TIMEOUT must be an integer" ;;
esac
umask 077
mkdir -p "$D3_DEPLOY_DIR/models" "$D3_DEPLOY_DIR/state" "$D3_DEPLOY_DIR/secrets"
cp "$D3_TLS_CERT_FILE" "$D3_DEPLOY_DIR/secrets/tls.crt"
cp "$D3_TLS_KEY_FILE" "$D3_DEPLOY_DIR/secrets/tls.key"
cp "$D3_CA_CERT_FILE" "$D3_DEPLOY_DIR/secrets/ca.crt"
printf 'Authorization: Bearer %s\n' "$D3_API_KEY" >"$D3_DEPLOY_DIR/authorization.header"
chmod 600 "$D3_DEPLOY_DIR/secrets/"*
chmod 600 "$D3_DEPLOY_DIR/authorization.header"

api_key_json=$(D3_API_KEY="$D3_API_KEY" python3 -c \
  'import json, os; print(json.dumps(os.environ["D3_API_KEY"]))')

cat >"$D3_DEPLOY_DIR/config.yaml" <<EOF
version: v0.3
listeners:
  - name: decisions
    address: 0.0.0.0
    port: 8899
    timeout: 30s
    api_keys:
      - $api_key_json
    systemone:
      models:
        - $D3_MODEL
global:
  router:
    enabled: false
  model_catalog:
    deployments:
      primary:
        provider: model_runtime
        artifact: $D3_MODEL
        revision: $D3_MODEL_REVISION
        device: cuda:0
        input:
          max_tokens: 8192
          overflow: reject
    system:
      decision_model:
        deployment: primary
EOF

cat >"$D3_DEPLOY_DIR/nginx.conf" <<'EOF'
worker_processes auto;
pid /tmp/nginx.pid;
error_log /dev/stderr warn;

events {
  worker_connections 256;
}

http {
  access_log /dev/stdout combined;
  server_tokens off;
  client_max_body_size 2m;
  client_body_temp_path /tmp/client_temp;
  proxy_temp_path /tmp/proxy_temp;
  fastcgi_temp_path /tmp/fastcgi_temp;
  uwsgi_temp_path /tmp/uwsgi_temp;
  scgi_temp_path /tmp/scgi_temp;

  server {
    listen 8443 ssl;
    server_name _;
    ssl_certificate /run/secrets/tls.crt;
    ssl_certificate_key /run/secrets/tls.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_tickets off;
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options "nosniff" always;

    location = /v1/systemone {
      proxy_pass http://d3-mini:8899;
      proxy_http_version 1.1;
      proxy_set_header Host $host;
      proxy_set_header X-Forwarded-Proto https;
      proxy_connect_timeout 5s;
      proxy_read_timeout 30s;
    }

    location = /v1/decisions {
      proxy_pass http://d3-mini:8899;
      proxy_http_version 1.1;
      proxy_set_header Host $host;
      proxy_set_header X-Forwarded-Proto https;
      proxy_connect_timeout 5s;
      proxy_read_timeout 30s;
    }

    location = /v1/systemone/models {
      proxy_pass http://d3-mini:8899;
      proxy_http_version 1.1;
      proxy_set_header Host $host;
      proxy_set_header X-Forwarded-Proto https;
      proxy_connect_timeout 5s;
      proxy_read_timeout 30s;
    }

    location / {
      return 404;
    }
  }
}
EOF

cat >"$D3_DEPLOY_DIR/.env" <<EOF
D3_BIND_ADDRESS=$D3_BIND_ADDRESS
D3_PUBLIC_PORT=$D3_PUBLIC_PORT
D3_MEMORY_LIMIT=$D3_MEMORY_LIMIT
D3_SHM_SIZE=$D3_SHM_SIZE
D3_UID=$(id -u)
D3_GID=$(id -g)
EOF

cat >"$D3_DEPLOY_DIR/compose.yaml" <<EOF
name: d3-mini
services:
  d3-mini:
    image: $D3_ROUTER_IMAGE
    user: "\${D3_UID}:\${D3_GID}"
    command: ["/config/config.yaml"]
    environment:
      VLLM_SR_GATEWAY: standalone
      HF_HOME: /app/models
    volumes:
      - ./config.yaml:/config/config.yaml:ro
      - ./models:/app/models
      - ./state:/app/.vllm-sr
    shm_size: \${D3_SHM_SIZE}
    read_only: true
    tmpfs:
      - /tmp:mode=1777
    cap_drop: [ALL]
    security_opt:
      - no-new-privileges:true
    deploy:
      resources:
        limits:
          memory: \${D3_MEMORY_LIMIT}
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    networks: [model]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "--fail", "--silent", "http://127.0.0.1:8899/ready"]
      interval: 15s
      timeout: 5s
      retries: 120
      start_period: 30s

  tls:
    image: $D3_TLS_IMAGE
    user: "\${D3_UID}:\${D3_GID}"
    ports:
      - "\${D3_BIND_ADDRESS}:\${D3_PUBLIC_PORT}:8443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./secrets:/run/secrets:ro
    read_only: true
    tmpfs:
      - /tmp:mode=1777
      - /var/cache/nginx:mode=0750
    cap_drop: [ALL]
    security_opt:
      - no-new-privileges:true
    depends_on:
      d3-mini:
        condition: service_healthy
    networks: [model]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "nc", "-z", "127.0.0.1", "8443"]
      interval: 15s
      timeout: 5s
      retries: 12

networks:
  model:
    driver: bridge
EOF

chmod 600 "$D3_DEPLOY_DIR/config.yaml" "$D3_DEPLOY_DIR/nginx.conf" \
  "$D3_DEPLOY_DIR/.env" "$D3_DEPLOY_DIR/compose.yaml"

docker compose --project-directory "$D3_DEPLOY_DIR" \
  --env-file "$D3_DEPLOY_DIR/.env" \
  -f "$D3_DEPLOY_DIR/compose.yaml" config --quiet
if [ "$mode" = "--render-only" ]; then
  printf 'Validated D3-mini deployment files in %s\n' "$D3_DEPLOY_DIR"
  exit 0
fi
docker compose --project-directory "$D3_DEPLOY_DIR" \
  --env-file "$D3_DEPLOY_DIR/.env" \
  -f "$D3_DEPLOY_DIR/compose.yaml" up -d --remove-orphans

deadline=$(( $(date +%s) + D3_STARTUP_TIMEOUT ))
while :; do
  if curl --fail --silent --show-error \
    --cacert "$D3_DEPLOY_DIR/secrets/ca.crt" \
    --resolve "$D3_SERVER_NAME:$D3_PUBLIC_PORT:$D3_PROBE_ADDRESS" \
    "https://$D3_SERVER_NAME:$D3_PUBLIC_PORT/v1/systemone/models" \
    -H @"$D3_DEPLOY_DIR/authorization.header" |
    D3_MODEL="$D3_MODEL" python3 -c \
      'import json, os, sys; data=json.load(sys.stdin); expected=os.environ["D3_MODEL"]; names={item.get("id") or item.get("name") for item in data.get("data", data.get("models", []))}; raise SystemExit(0 if expected in names else 1)' 2>/dev/null
  then
    break
  fi
  [ "$(date +%s)" -lt "$deadline" ] ||
    fail "D3-mini did not become ready within ${D3_STARTUP_TIMEOUT}s"
  sleep 10
done

probe=$(curl --fail --silent --show-error \
  --cacert "$D3_DEPLOY_DIR/secrets/ca.crt" \
  --resolve "$D3_SERVER_NAME:$D3_PUBLIC_PORT:$D3_PROBE_ADDRESS" \
  "https://$D3_SERVER_NAME:$D3_PUBLIC_PORT/v1/systemone" \
  -H @"$D3_DEPLOY_DIR/authorization.header" \
  -H "Content-Type: application/json" \
  --data "{\"model\":\"$D3_MODEL\",\"state\":\"Implement a bounded coding change.\",\"questions\":{\"tier\":{\"type\":\"choice\",\"instructions\":\"Choose the required coding effort.\",\"criteria\":{\"SIMPLE\":\"Small local change\",\"MEDIUM\":\"Bounded multi-step change\",\"COMPLEX\":\"Cross-module change\",\"REASONING\":\"High-risk reasoning\"}}}}")
printf '%s' "$probe" | python3 -c \
  'import json, sys; data=json.load(sys.stdin); answer=data.get("answers", {}).get("tier", {}); raise SystemExit(0 if answer.get("choice") in {"SIMPLE","MEDIUM","COMPLEX","REASONING"} else 1)' ||
  fail "D3-mini probe returned an invalid tier response"

printf 'D3-mini is ready at https://%s:%s\n' "$D3_SERVER_NAME" "$D3_PUBLIC_PORT"
printf 'Set D3_DECISION_API_BASE to that URL and D3_DECISION_API_KEY to the same secret on the LiteLLM host.\n'
