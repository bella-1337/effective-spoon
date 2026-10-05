# LiteLLM stack

A Docker Compose stack for running separate LiteLLM production and development
proxies behind Traefik, with PostgreSQL, Presidio, SearXNG, Prometheus, Grafana,
Loki, Grafana Alloy, and host/container exporters.

The `prod` and `dev` names below describe LiteLLM runtime profiles. The bundled
`mkcert` TLS setup is intended only for a private workstation or trusted local
network; it is not public-production certificate management.

## Prerequisites

- Docker Engine or Docker Desktop with Docker Compose v2
- `mkcert`
- `curl` (also used by the dashboard download script)
- Administrator access to install the local CA and update host resolution

Run all commands from this repository's root.

## 1. Configure the environment

Create `.env` locally. It is intentionally ignored by Git.

```dotenv
LITELLM_PROD_HOST=llm.example.test
LITELLM_DEV_HOST=llm-dev.example.test
GRAFANA_HOST=grafana.example.test
SEARXNG_HOST=search.example.test

PG_USER=litellm
PG_PASSWORD=<strong-random-password>
LITELLM_MASTER_KEY=sk-<strong-random-value>
OPENAI_API_KEY=<provider-api-key>
GRAFANA_ADMIN_PASSWORD=<strong-random-password>
SEARXNG_SECRET=<strong-random-secret>
```

All ten values are required:

| Variable | Purpose |
| --- | --- |
| `LITELLM_PROD_HOST` | Traefik hostname for the production-profile proxy |
| `LITELLM_DEV_HOST` | Traefik hostname for the development-profile proxy |
| `GRAFANA_HOST` | Traefik hostname for Grafana |
| `SEARXNG_HOST` | Public base hostname advertised by SearXNG |
| `PG_USER` | PostgreSQL/LiteLLM database user |
| `PG_PASSWORD` | PostgreSQL password |
| `LITELLM_MASTER_KEY` | Administrative/authentication key for LiteLLM |
| `OPENAI_API_KEY` | Provider credential used by the configured models |
| `GRAFANA_ADMIN_PASSWORD` | Initial Grafana administrator password |
| `SEARXNG_SECRET` | Secret used to sign SearXNG data |

Use literal values in `.env`; Compose does **not** execute commands such as
`$(openssl rand ...)`. Generate values separately, then paste their output:

```sh
# PG_PASSWORD is embedded in DATABASE_URL, so it must not contain URL-reserved
# characters such as @, :, /, ?, #, [, or ].
openssl rand -hex 32

# Suitable for the other secrets.
openssl rand -base64 32
```

Do not commit `.env`, provider keys, database passwords, or TLS private keys.
The example `.test` names are reserved for testing. If you choose other names,
use the same names in `.env`, the certificate command, and host resolution.

## 2. Create local TLS certificates

Install `mkcert` using the package instructions for your operating system, then
run:

```sh
mkcert -install
mkdir -p traefik/certs
mkcert \
  -cert-file traefik/certs/litellm-stack.pem \
  -key-file traefik/certs/litellm-stack-key.pem \
  llm.example.test llm-dev.example.test grafana.example.test search.example.test
```

The filenames must remain exactly:

- `traefik/certs/litellm-stack.pem`
- `traefik/certs/litellm-stack-key.pem`

If `.env` uses different hostnames, substitute all four names in the final
command. Confirm that the certificate covers them:

```sh
openssl x509 -in traefik/certs/litellm-stack.pem \
  -noout -subject -issuer -ext subjectAltName
```

`mkcert` creates a locally trusted CA and a private key. Its certificates are
for local/private use only: browsers on other devices will not trust them
unless that device explicitly trusts the local CA. Never publish the generated
private key or the CA root key. For an internet-facing deployment, replace this
setup with certificates from a publicly trusted CA and appropriate renewal and
key-management procedures.

## 3. Resolve the hostnames

For a stack running on this machine, add one line to `/etc/hosts`:

```text
127.0.0.1 llm.example.test llm-dev.example.test grafana.example.test search.example.test
```

On Windows, edit
`C:\Windows\System32\drivers\etc\hosts` as Administrator. For a remote Docker
host, use that host's reachable private IP instead of `127.0.0.1`. Keep the
names identical to `.env` and the certificate SANs.

Check resolution before starting:

```sh
getent hosts llm.example.test 2>/dev/null || \
  dscacheutil -q host -a name llm.example.test
```

## 4. Fetch Grafana dashboards

The repository's fetch script downloads maintained dashboards into Grafana's
provisioning tree. Run it before the first start and whenever you intentionally
want to refresh those upstream assets:

```sh
bash scripts/fetch-dashboards.sh
```

The script uses `curl` and overwrites its named downloaded files. Review changes
before committing them; dashboard JSON is upstream content, not a secret.

## 5. Configure Prometheus metrics authentication

LiteLLM protects its metrics endpoint with the master key. Before starting the
stack, create the ignored file that Prometheus reads:

```sh
mkdir -p litellm/secrets
grep '^LITELLM_MASTER_KEY=' .env | cut -d= -f2- \
  > litellm/secrets/litellm-master-key
chmod 600 litellm/secrets/litellm-master-key
```

Recreate this file whenever `LITELLM_MASTER_KEY` changes.

## 6. Validate configuration

Validate both Compose profiles without starting containers:

```sh
docker compose --profile prod config --quiet
docker compose --profile dev config --quiet
```

To inspect the fully interpolated model, use `docker compose config` carefully:
its output can contain resolved secrets. Do not paste or commit that output.

## 7. Start a profile

Services without a profile are shared. Select the LiteLLM proxy with exactly
one of:

```sh
# Production LiteLLM config
docker compose --profile prod up -d

# Development LiteLLM config, including detailed debug logging
docker compose --profile dev up -d
```

Both proxies can be run together when needed:

```sh
docker compose --profile prod --profile dev up -d
```

The production and development proxies use separate databases and config files,
while PostgreSQL, Traefik, Grafana, Prometheus, SearXNG, Presidio, and exporters
are shared.

## 8. Verify the stack

Check container state and recent logs:

```sh
docker compose --profile prod ps
docker compose --profile prod logs --tail=100
```

Replace `prod` with `dev` when that is the selected profile. Then verify the
HTTPS routes (substitute your configured hosts):

```sh
curl --fail --show-error --silent https://llm.example.test/health/liveliness
curl --fail --show-error --silent https://grafana.example.test/api/health
curl --fail --show-error --silent --output /dev/null https://search.example.test/
```

If command-line `curl` does not use the operating system trust store, point it
at the `mkcert` root certificate:

```sh
curl --cacert "$(mkcert -CAROOT)/rootCA.pem" \
  --fail --show-error --silent \
  https://llm.example.test/health/liveliness
```

Do not use `curl -k` as a permanent workaround; it disables certificate
verification. Open `https://grafana.example.test` to verify provisioning and
sign in with the Grafana admin password from `.env`.

## 9. View container logs in Grafana

Grafana Alloy sends container standard output and standard error to Loki, so raw
container logs are now available in Grafana. To view them:

1. Open Grafana and select **Explore**.
2. Select the **Loki** data source.
3. Open the label browser, choose a label and value to identify a container,
   and run the query. You can add more labels to narrow the stream.

Grafana Alloy provides `compose_project`, `compose_service`, `container`, and
`stream` (`stdout` or `stderr`) labels, plus `job="docker"`. For example, run
either of these Loki label selectors to show all discovered container streams
or one Compose service:

```logql
{compose_service=~".+"}
{compose_service="litellm-prod"}
```

Append a line filter when useful, for example
`{compose_service="litellm-prod"} |= "error"`. Label values reflect the
currently running profile and services; use the label browser rather than
assuming that every example value is present.

Use each Grafana data source for its intended telemetry:

- **Loki** contains raw container log lines collected by Grafana Alloy.
- **Prometheus** contains time-series service and infrastructure metrics.
- **LiteLLM PG (prod)** and **LiteLLM PG (dev)** query structured LiteLLM
  database telemetry, such as spend records; they are not log stores.

## 10. Verify the LiteLLM database schema

LiteLLM schema and column names can change between image releases. After the
selected proxy has completed migrations (and preferably served a request), run
the repository's read-only schema query against the matching database:

```sh
# Production
docker compose exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d litellm_prod' \
  < scripts/verify-schema.sql

# Development
docker compose exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d litellm_dev' \
  < scripts/verify-schema.sql
```

Compare the reported `LiteLLM_SpendLogs` columns with the SQL used by the cost
dashboard before relying on its figures. The script's index and read-only-role
statements are comments; review and apply them manually only when appropriate.

## Stop, restart, and remove

```sh
# Stop containers without deleting them
docker compose stop

# Restart the selected profile
docker compose --profile prod restart

# Remove containers and networks; named data volumes remain
docker compose --profile prod down
```

Use `docker compose down --volumes` only when you intentionally want to delete
PostgreSQL, Grafana, and Prometheus data. That operation is destructive.

## Security notes

- Keep `.env` and everything under `traefik/certs/` private.
- Rotate any credential that is accidentally exposed.
- The Docker socket mounted by Traefik is security-sensitive; restrict host
  access.
- Development detailed-debug logs may contain request metadata. Do not expose
  the `dev` profile or its logs to untrusted users.
- Review and pin container image versions before relying on this stack beyond a
  local environment; floating `latest`/branch tags can change unexpectedly.
