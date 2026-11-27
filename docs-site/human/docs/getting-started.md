<!-- human-version-of: docs/getting-started.md sha256: 4f60387321ae1000b9341e9417691f7cf5915e901924f459d0ee52f22290c5b4 -->

# Getting started

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

This guide takes you through a first deployment, from the environment file to
a verified, running stack. Install the
[prerequisites](../README.md#prerequisites) first, then follow steps 1–8 in
order.

## 1. Configure the environment

Create the ignored local environment file from the versioned template:

```sh
cp .env.example .env
chmod 600 .env
```

Optional services live in `compose.optional.yml` and remain gated by Compose
profiles. Create their separate environment file only when you enable Grafana,
Langfuse, LiteLLM Lens, or the image-security monitor:

```sh
cp .env.optional.example .env.optional
chmod 600 .env.optional
```

Then:

1. Replace every required placeholder.
2. Review the operational defaults.
3. Validate the core result before startup:

```sh
docker compose config --quiet
```

Both files must be regular files: maintenance commands refuse symlinked
environment files. If a secrets manager renders them, copy its output into
place with `install -m 600 SOURCE .env`.

The [environment variable reference](environment-variables.md) has the
complete catalog: accepted formats, defaults, security classification,
rotation guidance, change impact, and operational practices.

!!! warning
    Do not commit `.env`, `.env.optional`, provider keys, database passwords,
    rendered Compose configuration, or TLS private keys.

The example `.test` names are reserved for testing.

### Data-sovereignty region and models

Choose the region first.

- `DATA_SOVEREIGNTY_REGION` (`eu` or `us`) is required, and every cloud model
  must stay inside it.
- The template configures no cloud model. Leave the provider lists empty to
  start with only the self-hosted models.
- To add models later, set `BEDROCK_MODELS`, `AZURE_OPENAI_DEPLOYMENTS`, or
  `VERTEX_MODELS` and their credentials. Then recreate `litellm-prod` and
  `litellm-dev`.
- The gateway refuses to start when a provider location falls outside the
  region.

See [Data sovereignty and regional models](data-sovereignty.md).

### Public hostnames and `STACK_DOMAIN`

No public hostname is hard-coded in the Compose files, scripts, or commands in
this documentation. Set the deployment's base domain once in `.env`:

```dotenv
STACK_DOMAIN=ai.corp.example
```

How the hostnames are built:

- Every public `*_HOST` variable in `.env` and `.env.optional` defaults to
  `<service>.${STACK_DOMAIN}` (for example
  `LITELLM_PROD_HOST=llm.${STACK_DOMAIN}`).
- Docker Compose and the maintenance scripts expand that reference
  identically.
- To use a hostname outside the convention, replace only that variable's value
  with a full hostname.
- Compose refuses to render when `STACK_DOMAIN` or a required hostname is
  empty, and `game-day-preflight` rejects malformed rendered hostnames.

The commands below use shell variables instead of literal hostnames. Load the
resolved values into the current shell first (Python 3.11+, no other
dependencies):

```sh
eval "$(python3 scripts/maintenance.py stack-env)"
echo "$LITELLM_PROD_HOST"
```

Use the same names in the certificate request and host resolution.

Corporate branding, virtual-key rotation, and key email notifications are
optional; see [Corporate branding and virtual-key management](branding-and-key-management.md).

## 2. Deliver TLS certificates

Set `TRAEFIK_TLS_CERT_FILE` and `TRAEFIK_TLS_KEY_FILE` to the host paths that
the on-prem certificate delivery process populates. The defaults are:

- `traefik/certs/litellm-stack.pem`;
- `traefik/certs/litellm-stack-key.pem`.

Requirements:

- The certificate must include the full required chain and cover every
  configured public hostname.
- The private key must match the certificate and be readable only by the
  deployment account and Docker daemon.

After certificate renewal, recreate Traefik so it loads the updated
bind-mounted files:

```sh
docker compose up -d --force-recreate traefik
```

Confirm the delivered certificate before startup:

```sh
openssl x509 -in "${TRAEFIK_TLS_CERT_FILE:-traefik/certs/litellm-stack.pem}" \
  -noout -subject -issuer -dates -ext subjectAltName
```

### Local development fallback

Install `mkcert` using the package instructions for your operating system, then
run:

```sh
mkcert -install
mkdir -p traefik/certs
eval "$(python3 scripts/maintenance.py stack-env)"
mkcert \
  -cert-file traefik/certs/litellm-stack.pem \
  -key-file traefik/certs/litellm-stack-key.pem \
  "$LITELLM_PROD_HOST" "$LITELLM_DEV_HOST" "$OPENSEARCH_DASHBOARDS_HOST" \
  "$LANGFUSE_WEB_HOST" "$LANGFUSE_S3_HOST" "$GRAFANA_HOST" "$LENS_HOST" \
  "$DOCS_HOST"
```

The names come from `.env` and `.env.optional`, so the certificate always
matches the configured routes. Some names are needed only for an optional
profile:

- `GRAFANA_HOST` only for the `grafana` profile;
- the two Langfuse names only for the `langfuse` profile;
- `LENS_HOST` only for the `lens` profile;
- `DOCS_HOST` only for the `docs` profile.

`mkcert` creates a locally trusted CA and a private key. Its certificates are
for local/private use only: browsers on other devices will not trust them
unless that device explicitly trusts the local CA.

!!! warning
    Never publish the generated private key or the CA root key. Do not use the
    local fallback for the on-prem deployment.

### Trust the remote GPU host

D3-mini and BGE-M3 run on the remote GPU host, behind TLS issued by that
host's own mkcert CA (see the [GPU host guide](../gpu-host/README.md)).

1. Copy its public `gpu-host/state/mkcert/rootCA.pem` to this host. Never copy
   `rootCA-key.pem`.
2. Build the bundle that `GPU_HOST_CA_BUNDLE` mounts into both LiteLLM
   containers:

```sh
./scripts/build-gpu-host-ca-bundle.py --root-ca /path/to/rootCA.pem
```

The LiteLLM containers resolve the GPU host's names through DNS. Entries in
this host's `/etc/hosts` are not visible inside containers. So create DNS
records for the names in `D3_DECISION_API_BASE` and `BGE_M3_API_BASE` that
point at the GPU host's private IP.

## 3. Resolve the hostnames

For a stack running on this machine, generate the `/etc/hosts` line from the
configured names and append it:

```sh
eval "$(python3 scripts/maintenance.py stack-env)"
printf '127.0.0.1 %s %s %s %s %s %s %s %s\n' \
  "$LITELLM_PROD_HOST" "$LITELLM_DEV_HOST" "$OPENSEARCH_DASHBOARDS_HOST" \
  "$LANGFUSE_WEB_HOST" "$LANGFUSE_S3_HOST" "$GRAFANA_HOST" "$LENS_HOST" \
  "$DOCS_HOST" | sudo tee -a /etc/hosts
```

Other setups:

- On Windows, edit `C:\Windows\System32\drivers\etc\hosts` as Administrator
  and add the same names. Print them with the `printf` command above.
- For a remote Docker host, use that host's reachable private IP instead of
  `127.0.0.1`.

Keep the names identical to `.env` and the certificate SANs.

Check resolution before starting:

```sh
getent hosts "$LITELLM_PROD_HOST" 2>/dev/null || \
  dscacheutil -q host -a name "$LITELLM_PROD_HOST"
```

## 4. Fetch Grafana dashboards (optional `grafana` profile only)

Skip this step for the default stack. `opensearch-dashboards-setup` generates
and imports the OpenSearch Dashboards objects automatically, so no download is
required.

When you enable the optional `grafana` profile, the repository's fetch script
downloads maintained dashboards into Grafana's provisioning tree. Run it before
the first Grafana start, and whenever you intentionally want to refresh those
upstream assets:

```sh
./scripts/fetch-dashboards.py
```

The script uses Python's HTTPS client with certificate verification, immutable
dashboard revisions or source commits, size limits, JSON validation, and
pinned SHA-256 checksums. Updating a dashboard therefore requires reviewing
the upstream change and updating both its immutable URL and expected
checksum.

## 5. Configure Prometheus metrics authentication

LiteLLM protects its metrics endpoint with the master key. Before you start
the stack, create the ignored file that Prometheus reads:

```sh
mkdir -p litellm/secrets
grep '^LITELLM_MASTER_KEY=' .env | cut -d= -f2- \
  > litellm/secrets/litellm-master-key
chmod 600 litellm/secrets/litellm-master-key
```

The one-shot `prometheus-secrets-init` service copies this host-private file
into a named volume, with ownership and mode readable by the non-root
Prometheus process.

Whenever `LITELLM_MASTER_KEY` changes, recreate this file and restart
`prometheus-secrets-init` and Prometheus:

```sh
docker compose rm -f prometheus-secrets-init
docker compose up -d prometheus-secrets-init
docker compose up -d --force-recreate prometheus
```

## 6. Validate configuration

Validate both Compose profiles without starting containers:

```sh
docker compose --profile prod config --quiet
docker compose --profile dev config --quiet
docker compose --profile prod --profile demo config --quiet
docker compose --env-file .env --env-file .env.optional --profile prod --profile grafana config --quiet
docker compose --env-file .env --env-file .env.optional --profile prod --profile langfuse config --quiet
docker compose --env-file .env --env-file .env.optional --profile prod --profile lens config --quiet
docker compose --env-file .env --env-file .env.optional --profile security config --quiet
```

!!! warning
    To inspect the fully interpolated model, use `docker compose config`
    carefully: its output can contain resolved secrets. Do not paste or commit
    that output.

## 7. Start a profile

Services without a profile are shared. Select the LiteLLM proxy with exactly
one of:

```sh
# Production LiteLLM config
./scripts/maintenance.py up --profile prod -d

# Development LiteLLM config, including detailed debug logging
./scripts/maintenance.py up --profile dev -d
```

`maintenance.py up` does two things:

1. It runs `doctor` first. `doctor` renders every profile and checks the
   safety contracts, including that the Xberg allowlists match
   `security/xberg-destinations.toml`. When a check fails, it starts nothing.
2. It then runs `docker compose --profile ... up` with `.env` and, when
   present, `.env.optional`, passing every other option through.

The plain `docker compose` commands below start the same services without
those checks.

Both proxies can run together when needed:

```sh
docker compose --profile prod --profile dev up -d
```

OpenSearch, OpenSearch Dashboards, Data Prepper, Alloy, and
`opensearch-bridge` are shared services and start with every profile. The
first start:

- generates the private OpenSearch CA and certificates;
- provisions users, roles, index templates, lifecycle policies, alert
  monitors, and the Prometheus data source;
- imports the dashboards.

### Optional: Grafana

Grafana is optional. Add the `grafana` profile to any command to start
Grafana, Loki, and the Loki collector as well:

```sh
docker compose --env-file .env --env-file .env.optional \
  --profile prod --profile grafana up -d
```

The Grafana container refuses to start until `GRAFANA_ADMIN_PASSWORD`,
`GRAFANA_SECURITY_SECRET_KEY`, and `GRAFANA_DATABASE_PASSWORD` are set in
`.env.optional`. The profile's one-shot `grafana-db-init` service creates and
updates the least-privilege `grafana_ro` database role.

### Optional: Langfuse

Langfuse is also optional. OpenSearch already receives standards-based LiteLLM
traces, so you don't need Langfuse when it would only duplicate
observability. Enable it when you specifically want Langfuse's LLM trace UI,
datasets, prompt management, or evaluations:

```sh
docker compose --env-file .env --env-file .env.optional \
  --profile prod --profile langfuse up -d
```

Set the Langfuse infrastructure values in `.env.optional` first.

- The three core-side integration values (`LANGFUSE_PUBLIC_KEY`,
  `LANGFUSE_SECRET_KEY`, and `LANGFUSE_USER_ID_SALT`) remain in `.env`.
  LiteLLM and the OpenSearch bridge consume them during ordinary core
  lifecycle commands.
- The `langfuse-preflight` job rejects missing credentials before any
  Langfuse datastore starts.
- When the profile is absent, LiteLLM removes the Langfuse callback and
  attribution hook from its generated runtime config. OpenSearch tracing
  remains enabled.

### Optional: LiteLLM Lens

LiteLLM Lens is optional as well. Enable it when you want AI agents to review
your own agents' traces, group recurring failures, and turn runs into
datasets, inside the LiteLLM dashboard. Lens has no published image yet, so
the first start builds it from a pinned upstream commit. That takes several
minutes:

```sh
./scripts/lens.py secrets
docker compose --env-file .env --env-file .env.optional \
  --profile prod --profile lens up -d --build --wait
./scripts/lens.py status
```

`scripts/lens.py secrets` fills these values, without rotating existing ones:

- the two gateway connection secrets in `.env`;
- the Lens administrator token and ClickHouse password in `.env.optional`.

Start Lens together with the gateways, or recreate the gateways after Lens is
healthy. A gateway that started without Lens keeps it disabled.

See [LiteLLM Lens](lens.md) for agent tracing keys, analysis models, data
protection, backups, and upgrades.

### Optional: documentation site

To serve this documentation from the stack at `DOCS_HOST`
(`gateway-deployment-docs.${STACK_DOMAIN}` by default), start the `docs`
profile. It runs `zensical serve` on the checked-out revision, built into the
image. Export that revision first; the pages then name the commit they
describe:

```sh
export STACK_REPOSITORY_REVISION="$(git rev-parse HEAD)"
docker compose --env-file .env --env-file .env.optional \
  --profile docs up -d --build --wait docs
```

The site has no sign-in. See
[Documentation site](documentation-site.md#serve-the-site-from-the-stack).

### Optional: demonstration data

Synthetic production dashboard data is opt-in. To seed it explicitly, activate
the `demo` profile together with production:

```sh
docker compose --profile prod --profile demo up -d
```

### What the profiles share

The production and development proxies use separate databases and config
files. PostgreSQL, Traefik, OpenSearch, Prometheus, SearXNG, Presidio, and
exporters are shared. Langfuse and Lens are shared only when their optional
profiles are enabled.

## 8. Verify the stack

Check container state and recent logs:

```sh
docker compose --profile prod ps
docker compose --profile prod logs --tail=100
```

Replace `prod` with `dev` when that is the selected profile. Then verify the
HTTPS routes using the configured hostnames:

```sh
eval "$(python3 scripts/maintenance.py stack-env)"
curl --fail --show-error --silent "https://$LITELLM_PROD_HOST/health/liveliness"
# OpenSearch Dashboards requires authentication; 401 proves the route is up.
curl --silent --output /dev/null --write-out '%{http_code}\n' \
  "https://$OPENSEARCH_DASHBOARDS_HOST/api/status"
# Optional langfuse profile only:
curl --fail --show-error --silent \
  "https://$LANGFUSE_WEB_HOST/api/public/health?failIfDatabaseUnavailable=true"
# Optional lens profile only:
curl --fail --show-error --silent "https://$LENS_HOST/health/ready"
# Optional docs profile only:
curl --fail --show-error --silent --output /dev/null "https://$DOCS_HOST/"
# SearXNG is internal-only; Traefik must not route it (expect 404).
curl --silent --output /dev/null --write-out '%{http_code}\n' \
  -H "Host: search.$STACK_DOMAIN" "https://$LITELLM_PROD_HOST/"
```

### Check the remote models

For either profile, verify the remote D3 integration through LiteLLM rather
than calling the classifier host directly. For the development profile, also
check that the remote BGE-M3 endpoint returns a 1,024-dimension embedding:

```sh
curl --fail --silent "https://$LITELLM_DEV_HOST/v1/embeddings" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"bge-m3","input":"embedding probe"}' |
  python3 -c 'import json, sys; print(len(json.load(sys.stdin)["data"][0]["embedding"]))'
```

The D3 check:

```sh
curl --fail --silent "https://$LITELLM_DEV_HOST/v1/systemone" \
  -H "Authorization: ******" \
  -H "Content-Type: application/json" \
  -d '{"model":"d3-mini","state":"Fix a local typo.","questions":{"tier":{"type":"choice","instructions":"Choose coding effort.","criteria":{"SIMPLE":"Local change","MEDIUM":"Bounded multi-step change","COMPLEX":"Cross-module change","REASONING":"High-risk reasoning"}}}}'
```

### If curl does not trust the certificate

If command-line `curl` does not use the operating system trust store, point it
at the `mkcert` root certificate:

```sh
curl --cacert "$(mkcert -CAROOT)/rootCA.pem" \
  --fail --show-error --silent \
  "https://$LITELLM_PROD_HOST/health/liveliness"
```

!!! warning
    Do not use `curl -k` as a permanent workaround; it disables certificate
    verification.

### Open the dashboards

1. Open `https://$OPENSEARCH_DASHBOARDS_HOST`.
2. Sign in as `admin` with `OPENSEARCH_ADMIN_PASSWORD` from `.env`.
3. Choose the **Global** tenant.
4. Open **Dashboards → LiteLLM Overview**.

## Stop, restart, and remove

```sh
# Stop containers without deleting them
docker compose stop

# Restart the selected profile
docker compose --profile prod restart

# Remove containers and networks; named data volumes remain
docker compose --profile prod down
```

!!! warning
    Use `docker compose down --volumes` only when you intentionally want to
    delete PostgreSQL, OpenSearch, Grafana, Langfuse, Lens, and Prometheus
    data, including the private OpenSearch CA. That operation is destructive.
