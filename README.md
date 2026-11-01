# LiteLLM stack

A Docker Compose stack for running separate LiteLLM production and development
proxies behind Traefik, with PostgreSQL, Presidio, SearXNG, Xberg MCP,
development vLLM model servers, Prometheus, host/container exporters, and
OpenSearch with OpenSearch Dashboards as the default multi-user observability
platform for logs, metrics, LiteLLM usage, tracing, alerting, and audit trails.
Grafana/Loki and Langfuse remain available through optional profiles.

The `prod` and `dev` names below describe LiteLLM runtime profiles. The on-prem
deployment expects a certificate delivery process to place the configured TLS
certificate and private key on the host. The bundled `mkcert` workflow remains
available only for a private workstation or trusted development network.

## Prerequisites

- Docker Engine or Docker Desktop with Docker Compose v2.20.0 or newer
  (required for the top-level `include` model)
- Python 3.11 or newer for the local CI validation entrypoint
- An NVIDIA GPU and NVIDIA Container Toolkit for the development profile's
  BGE-M3 vLLM service
- Up to 7 GB of available RAM for the CPU-hosted vLLM Semantic Router
- About 4.5 GB of additional RAM for OpenSearch, OpenSearch Dashboards, and Data
  Prepper. On Linux hosts, OpenSearch requires `vm.max_map_count` of at least
  262144:

  ```sh
  sudo sysctl -w vm.max_map_count=262144
  echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-opensearch.conf
  ```
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) for the
  maintenance scripts
- A delivered TLS certificate and matching private key, or `mkcert` for local
  development only
- `curl` for the documented manual API and health checks
- Administrator access to install the local CA and update host resolution

Run all commands from this repository's root.

## Development and delivery workflow

### Development container

Open the repository in VS Code and select **Dev Containers: Reopen in
Container**. The versioned configuration in `.devcontainer/` provides Python
3.12, `uv`, Docker Buildx, and Docker Compose while reusing the host Docker
daemon. The host and development container must use the same CPU architecture.

On first creation, the container copies `.env.example` to the ignored `.env`
when one does not already exist and creates the ignored runtime directories. It
does not create `.env.optional`, generate production credentials, provider keys,
or TLS material. Replace placeholders before starting any service.

When opening a linked Git worktree, the worktree's common Git directory must
also be accessible inside the container. If the common directory is outside the
opened workspace, clone the repository into a container volume or add a local
bind mount for that directory; this limitation does not apply to a standard
clone.

### Local and continuous integration

Run the core hermetic checks used by GitHub Actions:

```sh
./scripts/ci/validate.sh
```

The command requires Python 3.11 or newer. It compiles Python, runs the
standard-library unit suite, parses JSON and TOML, enforces immutable GitHub
Action references, validates every Compose profile without starting containers,
and runs the game-day safety checks. It creates temporary ignored validation
inputs only when local equivalents are absent and removes those temporary files
on exit. CI additionally runs ShellCheck, Actionlint, and a complete development
container build.

Checks that require external services are intentionally deferred for now:

- starting the full Compose stack and waiting for all service health checks;
- loading the GPU-backed BGE-M3 model or the CPU semantic router model;
- calling OpenAI or other provider APIs;
- downloading vulnerability databases or live CISA feeds;
- probing a deployed on-prem environment.

These are not silently treated as passing CI tests. Run the documented
operational verification procedures against an explicitly provisioned
environment when the required services and credentials are available.

### Continuous delivery

Pull requests and pushes to `master` run the reusable CI workflow. Pushing a
semantic-version tag such as `v1.2.3`, or manually selecting an existing tag in
the Release workflow, reruns CI and then publishes a GitHub release containing a
source bundle and `SHA256SUMS`. The `release` GitHub environment can be
configured with required reviewers before granting the publish job approval.
Existing releases are never replaced.

The release is a validated delivery artifact, not an automatic production
rollout. On-prem deployment still requires an operator to supply secrets and
TLS material, review the release, and follow the upgrade and rollback procedure
in this document.

## 1. Configure the environment

Create the ignored local environment file from the versioned template:

```sh
cp .env.example .env
chmod 600 .env
```

Optional services live in `compose.optional.yml` and remain gated by Compose
profiles. Only when enabling Grafana, Langfuse, or the image-security monitor,
create their separate environment file:

```sh
cp .env.optional.example .env.optional
chmod 600 .env.optional
```

Replace every required placeholder, review the operational defaults, and
validate the core result before startup. Both files must be regular files:
maintenance commands refuse symlinked environment files. If a secrets manager
renders them, copy its output into place with `install -m 600 SOURCE .env`.

```sh
docker compose config --quiet
```

See the [environment variable reference](docs/environment-variables.md) for the
complete catalog, accepted formats, defaults, security classification, rotation
guidance, change impact, and operational practices.

Do not commit `.env`, `.env.optional`, provider keys, database passwords,
rendered Compose configuration, or TLS private keys. The example `.test` names
are reserved for testing. If you choose other names, use the same names in the
applicable environment file, the certificate command, and host resolution.

### Automatic virtual-key rotation

LiteLLM 1.104 exposes per-key auto-rotation settings in the UI, but key
regeneration and scheduled rotation require a LiteLLM Enterprise license. The
current stack has no `LITELLM_LICENSE`, so the background worker is intentionally
disabled. Enabling only the UI toggle or worker would leave due rotations
failing with an Enterprise-feature error.

After obtaining a valid license, pass `LITELLM_LICENSE` securely to both proxy
services and add these stack-owned worker settings:

```yaml
LITELLM_KEY_ROTATION_ENABLED: "true"
LITELLM_KEY_ROTATION_CHECK_INTERVAL_SECONDS: "3600"
LITELLM_KEY_ROTATION_GRACE_PERIOD: 24h
```

Then use **Virtual Keys**, select a key, enable **Auto-Rotation**, and choose its
rotation interval. The worker generates a replacement key, keeps the previous
key valid for the configured grace period, and carries the rotation schedule to
the replacement.

The check interval is how often LiteLLM looks for due keys; it is not the
per-key rotation interval selected in the UI. A rotation may therefore occur up
to one check interval after its scheduled time. In a multi-replica deployment,
configure LiteLLM's supported distributed locking before relying on one
rotation execution per schedule.

#### Optional email delivery for generated and rotated keys

Email is not enabled by default because this repository has no mail credentials.
LiteLLM's
[email notification documentation](https://docs.litellm.ai/docs/proxy/email)
supports SMTP, Resend, and SendGrid callbacks. For SMTP, add the callback to
`litellm_settings` in both profile configuration files:

```yaml
litellm_settings:
  callbacks: ["smtp_email"]
```

Pass these variables to each LiteLLM service from the ignored local `.env`:

```dotenv
SMTP_HOST=smtp.example.com
SMTP_TLS=True
SMTP_PORT=587
SMTP_USERNAME=<smtp-user>
SMTP_PASSWORD=<smtp-password>
SMTP_SENDER_EMAIL=ai-notifications@example.com

# Recommended: do not place live API keys in email.
EMAIL_INCLUDE_API_KEY=false
```

For Resend use `callbacks: ["resend_email"]` with `RESEND_API_KEY`; for
SendGrid use `callbacks: ["sendgrid_email"]` with `SENDGRID_API_KEY` and
`SENDGRID_SENDER_EMAIL`. Keep every provider secret in `.env` or a production
secrets manager, never in the YAML files.

The same callback handles both key-generation and key-rotation notifications.
For a generated key, select an owner with a valid email address and enable
**Advanced Settings > Send Email** in the Virtual Keys creation dialog. API
clients must send the equivalent request flag:

```json
{
  "user_id": "<owner-user-id>",
  "send_invite_email": true
}
```

LiteLLM does not send a key-generation message when `send_invite_email` is
omitted or false. The owner and opt-in must therefore be supplied for every key
that should trigger a notification.

The SMTP callback, key-generation event, and owner-email lookup were validated
locally with a disposable user and key while `EMAIL_INCLUDE_API_KEY=false`; the
notification was delivered to the owner's address and contained no plaintext
API key. Rotation-email delivery could not be exercised because this unlicensed
deployment cannot regenerate a virtual key.

For generated and rotated keys, the recipient is resolved from the key's
`user_id`, then from that owner's `user_email` in LiteLLM's user table. A key
without an owning user or an owner without an email address cannot receive a
notification. Team membership or the identity of the administrator who created
the key is not a substitute for explicit key ownership.

With `EMAIL_INCLUDE_API_KEY=true` (LiteLLM's upstream default), the email
contains the new plaintext key. This is convenient but exposes a credential to
mail transport, mailboxes, forwarding, retention, and account compromise. The
recommended `false` setting sends a notification with a placeholder instead;
the owner retrieves the generated or replacement key from the authenticated
LiteLLM dashboard. For rotation, the owner updates clients during the 24-hour
grace period, after which the old key is revoked.

Before enabling key email in production:

1. Confirm every notified human-owned key has the correct `user_id`, the owner
   has a verified `user_email`, and key creation sets `send_invite_email: true`.
2. Install and validate the Enterprise license before enabling rotation or its
   worker; generation notifications do not require the rotation license.
3. Use a TLS-enabled mail provider and a narrowly scoped sending credential.
4. Keep `EMAIL_INCLUDE_API_KEY=false` unless an approved risk assessment
   explicitly permits sending plaintext credentials by email.
5. Test generation with a disposable user and key and verify delivery. If
   rotation is licensed, also update a test client and confirm the previous key
   works only during the grace period.
6. Alert on rotation or email failures; do not assume the UI setting proves
   delivery.

## 2. Deliver TLS certificates

Set `TRAEFIK_TLS_CERT_FILE` and `TRAEFIK_TLS_KEY_FILE` to the host paths populated
by the on-prem certificate delivery process. The defaults are:

- `traefik/certs/litellm-stack.pem`;
- `traefik/certs/litellm-stack-key.pem`.

The certificate must include the full required chain and cover every configured
public hostname. The private key must match the certificate and be readable only
by the deployment account and Docker daemon. After certificate renewal, recreate
Traefik so the updated bind-mounted files are loaded:

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
mkcert \
  -cert-file traefik/certs/litellm-stack.pem \
  -key-file traefik/certs/litellm-stack-key.pem \
  llm.example.test llm-dev.example.test opensearch.example.test \
  langfuse.example.test langfuse-s3.example.test grafana.example.test
```

If `.env` uses different hostnames, substitute all names in the final command.
`grafana.example.test` is needed only for the optional `grafana` profile.

`mkcert` creates a locally trusted CA and a private key. Its certificates are
for local/private use only: browsers on other devices will not trust them
unless that device explicitly trusts the local CA. Never publish the generated
private key or the CA root key. Do not use the local fallback for the on-prem
deployment.

## 3. Resolve the hostnames

For a stack running on this machine, add one line to `/etc/hosts`:

```text
127.0.0.1 llm.example.test llm-dev.example.test opensearch.example.test langfuse.example.test langfuse-s3.example.test grafana.example.test
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

## 4. Fetch Grafana dashboards (optional `grafana` profile only)

OpenSearch Dashboards objects are generated and imported automatically by
`opensearch-dashboards-setup`; no download is required for the default stack.
When you enable the optional `grafana` profile, the repository's fetch script
downloads maintained dashboards into Grafana's provisioning tree. Run it before
the first Grafana start and whenever you intentionally want to refresh those
upstream assets:

```sh
./scripts/fetch-dashboards.py
```

The script uses Python's HTTPS client with certificate verification, immutable
dashboard revisions or source commits, size limits, JSON validation, and pinned
SHA-256 checksums. Updating a dashboard therefore requires reviewing the
upstream change and updating both its immutable URL and expected checksum.

## 5. Configure Prometheus metrics authentication

LiteLLM protects its metrics endpoint with the master key. Before starting the
stack, create the ignored file that Prometheus reads:

```sh
mkdir -p litellm/secrets
grep '^LITELLM_MASTER_KEY=' .env | cut -d= -f2- \
  > litellm/secrets/litellm-master-key
chmod 600 litellm/secrets/litellm-master-key
```

The one-shot `prometheus-secrets-init` service copies this host-private file into
a named volume with ownership and mode readable by the non-root Prometheus
process. Recreate this file and restart `prometheus-secrets-init` and Prometheus
whenever `LITELLM_MASTER_KEY` changes:

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
docker compose --env-file .env --env-file .env.optional --profile security config --quiet
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

OpenSearch, OpenSearch Dashboards, Data Prepper, Alloy, and `opensearch-bridge`
are shared services and start with every profile. The first start generates the
private OpenSearch CA and certificates, provisions users, roles, index
templates, lifecycle policies, alert monitors, and the Prometheus data source,
and imports the dashboards. Grafana is optional; add the `grafana` profile to
any command to start Grafana, Loki, and the Loki collector as well:

```sh
docker compose --env-file .env --env-file .env.optional \
  --profile prod --profile grafana up -d
```

The Grafana container refuses to start until `GRAFANA_ADMIN_PASSWORD`,
`GRAFANA_SECURITY_SECRET_KEY`, and `GRAFANA_DATABASE_PASSWORD` are set in
`.env.optional`. The profile's one-shot `grafana-db-init` service creates and
updates the least-privilege `grafana_ro` database role.

Langfuse is also optional. OpenSearch already receives standards-based LiteLLM
traces, so Langfuse is not needed when it would only duplicate observability.
Enable it when you specifically want Langfuse's LLM trace UI, datasets, prompt
management, or evaluations:

```sh
docker compose --env-file .env --env-file .env.optional \
  --profile prod --profile langfuse up -d
```

Set the Langfuse infrastructure values in `.env.optional` first. The three
core-side integration values (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and
`LANGFUSE_USER_ID_SALT`) remain in `.env` because LiteLLM and the OpenSearch
bridge consume them during ordinary core lifecycle commands. The
`langfuse-preflight` job rejects missing credentials before any Langfuse
datastore starts. When the profile is absent, LiteLLM removes the Langfuse
callback and attribution hook from its generated runtime config; OpenSearch
tracing remains enabled.

Synthetic production dashboard data is opt-in. To seed it explicitly, activate
the `demo` profile together with production:

```sh
docker compose --profile prod --profile demo up -d
```

The production and development proxies use separate databases and config files,
while PostgreSQL, Traefik, OpenSearch, Prometheus, SearXNG, Presidio, and
exporters are shared. Langfuse is shared only when its optional profile is
enabled.

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
# OpenSearch Dashboards requires authentication; 401 proves the route is up.
curl --silent --output /dev/null --write-out '%{http_code}\n' \
  https://opensearch.example.test/api/status
# Optional langfuse profile only:
curl --fail --show-error --silent \
  'https://langfuse.example.test/api/public/health?failIfDatabaseUnavailable=true'
# SearXNG is internal-only; Traefik must not route it (expect 404).
curl --silent --output /dev/null --write-out '%{http_code}\n' \
  -H 'Host: search.example.test' https://llm.example.test/
```

For either profile, verify the remote D3 integration through LiteLLM rather than
calling the classifier host directly:

```sh
curl --fail --silent https://llm-dev.example.test/v1/systemone \
  -H "Authorization: ******" \
  -H "Content-Type: application/json" \
  -d '{"model":"d3-mini","state":"Fix a local typo.","questions":{"tier":{"type":"choice","instructions":"Choose coding effort.","criteria":{"SIMPLE":"Local change","MEDIUM":"Bounded multi-step change","COMPLEX":"Cross-module change","REASONING":"High-risk reasoning"}}}}'
```

If command-line `curl` does not use the operating system trust store, point it
at the `mkcert` root certificate:

```sh
curl --cacert "$(mkcert -CAROOT)/rootCA.pem" \
  --fail --show-error --silent \
  https://llm.example.test/health/liveliness
```

Do not use `curl -k` as a permanent workaround; it disables certificate
verification. Open `https://opensearch.example.test`, sign in as `admin` with
`OPENSEARCH_ADMIN_PASSWORD` from `.env`, choose the **Global** tenant, and open
**Dashboards → LiteLLM Overview**.

## 9. Use the observability pipeline

OpenSearch is the default trace and log backend. LiteLLM sends standards-based
OpenTelemetry spans to Data Prepper, and Alloy sends stdout/stderr from every
Compose service through the same default platform. The optional `grafana`
profile also sends logs to Loki.

With the optional `langfuse` profile, LiteLLM additionally loads its
`langfuse_otel` integration. Open `https://langfuse.example.test`, sign in with
the initial administrator account, and select the pre-provisioned **LiteLLM**
project. Public sign-up is disabled (`AUTH_DISABLE_SIGNUP=true`), so only the
initial administrator account can sign in. Keep its credentials in the secrets
manager, and follow the
[Langfuse operator account runbook](docs/environment-variables.md#langfuse-operator-accounts-and-lockout-recovery)
to add operators or recover access. Traces include model, provider, token, cost, timing, and error metadata.
Prompt and response content is deliberately excluded by both
`turn_off_message_logging: true` and
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=no_content`. Do not enable
the legacy `langfuse` callback alongside `langfuse_otel`, because doing so
duplicates traces.

When enabled, the callback is global in both LiteLLM profiles, so requests made
by every authenticated LiteLLM user or virtual key are traced to the
administrator project. The trusted pre-call hook replaces any caller-supplied
`trace_user_id`
with a stable HMAC pseudonym derived from the authenticated LiteLLM user ID (or
the authenticated key identity when no user owns the key). It also strips
caller-supplied `langfuse_*` headers and pseudonymizes a supplied `user` field.
The same hook maps the common agent identifiers `session_id`, `conversation_id`,
`thread_id`, and `run_id` from request metadata to Langfuse `session.id`.
LiteLLM's standard `x-litellm-session-id`, `x-session-id`, and
`x-<toolkit>-session-id` headers are also supported. Session identifiers are
validated, removed from exported metadata, and replaced with stable
principal-scoped HMAC identifiers, preventing two authenticated users or keys
from merging traces by supplying the same identifier.

For reliable multi-turn grouping, send one stable identifier on every request in
the logical conversation. The portable OpenAI-compatible form is:

```python
client.chat.completions.create(
    model="your-model",
    messages=[{"role": "user", "content": "Hello"}],
    extra_body={"metadata": {"session_id": conversation_id}},
)
```

When a toolkit supplies no session identifier, the hook uses its propagated OTel
trace ID. If neither exists, it creates a request-scoped fallback so the trace is
still visible in Langfuse Sessions. A request-scoped fallback cannot infer or
merge later turns; clients that need multi-turn sessions must send a stable
identifier or propagate one OTel trace across those calls.
The OTel v2 baggage allowlists are empty, so raw user IDs, key identities, and
client IP metadata are not copied to child spans, and FastAPI server spans are
excluded to prevent request URLs, query strings, user agents, or peer IPs from
reaching Langfuse. Team ID and alias remain on administrator-only generation
traces for team analytics. Rotate
`LANGFUSE_USER_ID_SALT` only when intentionally breaking historical user
correlation.

Per-LiteLLM-user Langfuse UI access is **not available in this OSS deployment**.
Langfuse does not treat `trace_user_id` as an authorization boundary, so a
project viewer can remove a user filter and see every trace in that project.
LiteLLM and Langfuse accounts are not synchronized, and project-level membership
is not available on the installed plan. Supported automated provisioning through
Langfuse organization/SCIM APIs requires Enterprise. Keep the shared Langfuse
project restricted to administrators and operators; do not present a saved user
filter as access isolation.

The production LiteLLM proxy does not require Xberg or an inactive Langfuse
profile to start. When Langfuse is explicitly enabled, LiteLLM waits for its web
service before retaining the optional callbacks in the runtime config. Later
Langfuse outages do not block model requests; their failures remain visible
through service health, callback logs, and the default OpenSearch observability
checks.

### OpenSearch: the default multi-user observability platform

OpenSearch replaces Grafana as the default UI because Grafana OSS cannot
restrict data sources or dashboards per user. The OpenSearch security plugin
provides internal users, role-based index and document/field-level security,
read-only users, Global/Private tenants, and an audit log without a commercial
license. Every OpenSearch transport and HTTP connection uses TLS with a private
CA created by `opensearch-certs`; only Traefik exposes OpenSearch Dashboards.

| Component | Role |
| --- | --- |
| `opensearch` | Single-node store with security, alerting, ISM, SQL/PPL, observability, and notifications plugins |
| `opensearch-dashboards` | Multi-user UI at `https://${OPENSEARCH_DASHBOARDS_HOST}` |
| `alloy` → `data-prepper` | Container stdout/stderr over OTLP into `litellm-logs-*`, with multi-line entries joined, a normalized severity, ANSI colours stripped, search terms redacted, and Traefik access logs parsed into `http.*` fields |
| LiteLLM → `data-prepper` | Content-free OpenTelemetry request, guardrail, model, and database spans into Trace Analytics (`otel-v1-apm-span-*`, `otel-v1-apm-service-map`) |
| `opensearch-bridge` | Prometheus expressions and raw series into `litellm-metrics-*`, alert-rule state into `litellm-alert-state-*`, non-content LiteLLM spend, provider-error, audit, user, team, and key data into `litellm-spend-*`, `litellm-provider-errors-*`, `litellm-audit-*`, and `litellm-entities` |
| `opensearch-setup` | Idempotent provisioning of users, roles, templates, lifecycle policies, the Prometheus direct-query data source, and alert monitors |
| `opensearch-dashboards-setup` | Imports index patterns, visualizations, saved searches, dashboards, and troubleshooting notebooks into the Global tenant |

Dashboards in the Global tenant (start at **LiteLLM Overview**):

- **LiteLLM Management Summary** – executive usage, model spend, reliability,
  user/team/key adoption, budget coverage, unassigned spend, administrative
  changes, provider errors, and a directional ROI indication. Request-level,
  distinct-session, realization, hourly-cost, and currency assumptions come
  from `.env`; raw session identifiers are pseudonymized before OpenSearch.
- **LiteLLM Stack (dev/prod)** – every panel of the Grafana stack dashboard plus
  tokens, provider latency, time to first token, proxy overhead, proxy status
  codes and exceptions, deployment state, guardrail errors, cache hit ratio,
  and in-flight requests from the upstream LiteLLM dashboard.
- **LiteLLM Cost Attribution**, **LiteLLM Admin Overview**, and **LiteLLM Team
  Costing** – every panel of the matching Grafana SQL dashboards, with
  environment, model, and team filters.
- **Container Logs** – log volume by service and severity, error, warning, and
  stderr lines per Compose service, plus searchable log tables.
- **Traefik Access Logs** – every HTTPS request with router, status, method,
  path, and latency percentiles; error responses and slowest requests.
- **LiteLLM Request Traces** (operators) – requests by route, span latency by
  operation, model calls, guardrail outcomes, database latency by table, error
  spans, and the slowest spans. **Observability → Trace Analytics** adds trace
  waterfalls and the service map.
- **Infrastructure: Host & Containers**, **Infrastructure: PostgreSQL**, and
  **Infrastructure: Traefik, Probes & Targets** – the core Node Exporter Full,
  cAdvisor, PostgreSQL, Traefik, blackbox, TLS-expiry, and scrape-target panels.
- **Security, Backups & Alerts** – image-security metrics, backup age, and the
  latest state of every alert rule.
- **Metric Explorer** – any mirrored Prometheus metric by name and series.
- **OpenSearch Security Audit** – failed logins, privilege denials, and security
  configuration changes.

Troubleshooting notebooks under **Observability → Notebooks** turn the repository's
tested verification and recovery paths into a top-to-bottom, user-symptom workflow:

1. **Stack First Response** – unavailable UI/API, ingress status, scrape targets,
   service errors, and restarts.
2. **LiteLLM Requests and Integrations** – failed or slow requests, providers,
   routing, guardrails, pgvector, SearXNG, Xberg MCP, and Langfuse.
3. **Missing Logs, Metrics, Traces, or Alerts** – freshness and completeness at
   every producer, collector, bridge, Data Prepper, and OpenSearch layer.
4. **Data, Cost, Backup, and Upgrade Recovery** – PostgreSQL, spend freshness,
   backups, game-day preflight, and guarded rollback paths.
5. **Access, Authentication, and Audit** – 401/403 responses, Dashboards roles,
   tenants, failed logins, and missing privileges.

Each notebook starts with the user-visible symptom, narrows the failing layer with
live PPL, and ends with the smallest matching `verify-litellm-upgrade.py` or
recovery command. PPL cells use OpenSearch's native `QUERY` paragraph schema, so
they execute with the signed-in user's read permissions when the notebook opens;
running them does not require granting shared-dashboard write access. Index
authorization still applies: the first-response notebook needs
`litellm_ops_viewer` or broader, cost/recovery checks need `litellm_viewer` or
`litellm_operator`, and trace and security-audit queries remain operator-only even
though the shared notebook itself is visible in the Global tenant.

Beyond the dashboards, OpenSearch Dashboards provides **Discover** over every
index pattern, **Observability → Logs** (PPL, for example
``source = litellm-logs-* | where `log.attributes.stream` = 'stderr'``),
**Observability → Metrics** and PPL over every Prometheus series through the
registered `prometheus` data source (for example
`source = prometheus.query_range('rate(litellm_requests_metric_total[5m])', now() - 1h, now(), '1m')`),
**Query Workbench**, **Notebooks**, **Reporting**, **Anomaly Detection**, and
**Alerting**. The PromQL expressions and raw-series selectors stored in
OpenSearch are defined in `opensearch/tools/metrics.yml`.

#### Users, roles, and tenants

Sign in as `admin` and create people under **Security → Internal users**, or
connect an external identity provider through the security plugin's
`config.yml`. Map users to the provisioned roles:

| Role | Grants |
| --- | --- |
| `litellm_viewer` | Read logs, metrics, alert state, spend, provider errors, and entities; Global tenant read-only |
| `litellm_ops_viewer` | Read logs, metrics, and alert state only (no cost or identity data) |
| `litellm_cost_viewer` | Read spend, provider errors, and entities only |
| `litellm_operator` | Read everything above plus LiteLLM request traces and the LiteLLM and OpenSearch audit trails; edit shared dashboards |
| `litellm_dashboards_read_only` | Hides editing UI in OpenSearch Dashboards |

Every human user also needs the built-in `kibana_user` role. Add the built-in
`alerting_read_access` or `alerting_full_access` roles for alert visibility or
management. For example, a read-only team lead:

```sh
curl --cacert ca.pem -u "admin:${OPENSEARCH_ADMIN_PASSWORD}" -X PUT \
  https://opensearch:9200/_plugins/_security/api/internalusers/alice \
  -H 'Content-Type: application/json' \
  -d '{"password":"<16+ chars>","opendistro_security_roles":["kibana_user","litellm_viewer","litellm_dashboards_read_only"]}'
```

The same call is easier through **Security → Internal users** in the UI. To
limit someone to one team's costs, create a role with document-level security,
for example index pattern `litellm-spend-*` with the DLS query
`{"term": {"team_id": "demo-team-atlas"}}`. The Admin Overview dashboard
exposes identities, budgets, key aliases, and audit activity; grant it only to
operators. Private tenants let each user build personal dashboards without
affecting the shared Global tenant.

#### Alerting

`opensearch-setup` translates every rule in `grafana/alerting/` into an
OpenSearch Alerting monitor named `[LiteLLM] <rule title>`, so the Grafana files
remain the single source of truth. A monitor fires only after its rule's
condition held for the full Grafana `for` duration, honoring the rule's
`noDataState` and `execErrState`. Four extra monitors fire when alert-rule
evaluation, Prometheus metrics, container logs, or LiteLLM trace spans stop
reaching OpenSearch.

The repository contains no notification credentials. Create a channel under
**Notifications → Channels** (Slack, Microsoft Teams, Chime, email, SNS, or a
webhook), copy its ID into `OPENSEARCH_ALERT_CHANNEL_ID`, and re-run
`docker compose up -d opensearch-setup` to attach it to every monitor with a
one-hour throttle. Without a channel, alerts are visible under **Alerting** but
notify no one.

#### Rotation and recovery

`opensearch-setup` re-applies the service passwords from `.env` on every run
using the private admin certificate, so rotate a service password by updating
`.env` and running:

```sh
docker compose up -d --force-recreate opensearch-setup
docker compose up -d --force-recreate opensearch-dashboards data-prepper opensearch-bridge
```

Users created in the UI are not overwritten. `opensearch-certs` renews node and
admin certificates 30 days before expiry whenever it runs. OpenSearch volumes are
not part of the database backup; use OpenSearch snapshots if the observability
history itself must be recoverable. The LiteLLM usage indices can always be
rebuilt from PostgreSQL by deleting `litellm-bridge-state` and restarting
`opensearch-bridge`.

### Optional Grafana profile

With `--profile grafana`, the `alloy-loki` collector also sends container logs to
Loki and Grafana provisions its original dashboards, data sources, and alert
rules. To view logs in Grafana:

1. Open Grafana and select **Explore**.
2. Select the **Loki** data source.
3. Open the label browser, choose a label and value to identify a container,
   and run the query. You can add more labels to narrow the stream.

Grafana Alloy provides `compose_project`, `compose_service`, `container`, and
`stream` (`stdout` or `stderr`) labels, plus `job="docker"`. The OpenSearch log
documents carry the same values under `log.attributes.*`. For example, run
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

Grafana provisions five dashboards in the **LiteLLM** folder:

- **LiteLLM Management Summary** for executive usage, cost, reliability,
  governance, and directional ROI. It displays both successful-request and
  distinct-successful-session contributions plus the active `.env` assumptions.
- **LiteLLM Stack (dev/prod)** for request, latency, failure, spend, deployment,
  and guardrail metrics.
- **LiteLLM Cost Attribution** for model, key, token, and request-level cost.
- **LiteLLM Admin Overview** for users, usable keys, teams, budgets, spend by
  user/team, provider errors, and audit changes. Its database selector switches
  between production and development.
- **LiteLLM Team Costing** for per-team spend, requests, tokens, models, member
  attribution, and expensive requests. Select any current team; new teams appear
  automatically.

Grafana sign-up and anonymous access remain disabled. Grafana OSS cannot
restrict data sources per user, so keep Grafana access limited to operators and
use OpenSearch Dashboards for everyone else.

#### Management views across the stack

Use the provisioned management summaries as the executive entry point, then
drill into each product's native view. This avoids duplicating sensitive trace
content or creating competing calculations:

| Tool | Management use | Scope and limitation |
| --- | --- | --- |
| OpenSearch Dashboards | **LiteLLM Management Summary** in the Global tenant | Default multi-user summary with role-controlled usage, governance, reliability, and the environment-managed ROI scenario |
| Grafana (`grafana` profile) | **LiteLLM Management Summary** in the **LiteLLM** folder | Operator-only summary with request/session contribution breakdown and direct read-only PostgreSQL queries |
| LiteLLM Admin UI | Native Usage, Models, Teams, Users, Keys, and Budgets pages | System-of-record operational administration and entity-level drill-down; no separate provisioned dashboard is required |
| Langfuse | Native project dashboard, usage, latency, score, and trace analytics | Administrator/operator trace-quality and adoption drill-down; it is not an authorization-safe per-user management portal in this OSS deployment |
| Prometheus | Expression browser and source metrics for usage, reliability, and infrastructure | Diagnostic source of truth rather than a curated management dashboard; use Grafana or OpenSearch for reviewed summaries |
| Loki | Explore container logs through Grafana | Operational drill-down only; it has no independent management dashboard UI |

The ROI panels are **indications, not booked savings**. Configure
`ROI_MINUTES_SAVED_PER_REQUEST`, `ROI_MINUTES_SAVED_PER_SESSION`,
`ROI_VALUE_REALIZATION_FACTOR`, `ROI_LOADED_HOURLY_COST`, and `ROI_CURRENCY` in
`.env`. The formula adds the successful-request and distinct-successful-session
contributions. Set either minutes value to zero when both represent the same
saved work, or the estimate will double count it. The default session value is
zero until a local session-level measurement exists. The loaded hourly cost and
LiteLLM model spend must be expressed in the same configured currency.

The calculation subtracts direct model spend but does not include platform
labor, hardware, licensing, change-management, review time, or the cost of
incorrect output. Validate both minutes values and realization through sampled
local workflows—particularly for regional labor costs—and compare the result
with finance-approved costs before using it in a business case. Changes take
effect after rerunning `postgres-init`, recreating `opensearch-bridge`, and
rerunning `opensearch-dashboards-setup`; Grafana reads the applied values from
the read-only `LiteLLM_ManagementROIConfig` table.

### Demonstration data and telemetry stores

D3 classifier calls flow through LiteLLM and therefore use the normal LiteLLM
request, failure, latency, spend-log, and trace surfaces. The external GPU host
is intentionally not scraped across the trust boundary by this Compose stack;
its owner must monitor GPU memory, utilization, temperature, process readiness,
and TLS certificate expiry.

The development profile automatically seeds two persistent demonstration teams
and four users after its database schema is ready. Production seeds the same
data only when the explicit `demo` profile is enabled:

- **Demo Team Atlas**: Demo Alice and Demo Bob
- **Demo Team Orion**: Demo Carol and Demo Dave

The seed also adds blocked, non-authenticating placeholder key records and
twelve content-free spend rows so the admin and team dashboards render useful
test data without exposing a usable credential. The seed is idempotent.
Development refreshes it when that profile starts; production refreshes it only
when the `demo` profile is explicitly started. Existing seeded rows remain in
the persistent database until they are intentionally removed or expire.

Use each observability store for its intended telemetry:

- **OpenSearch** contains container logs, mirrored metrics, alert-rule state,
  non-content copies of LiteLLM usage data, and the OpenSearch security audit
  log.
- **Langfuse** contains content-redacted LiteLLM request traces.
- **Prometheus** contains every scraped time series and remains the metrics
  source of truth.
- **LiteLLM PostgreSQL** remains the system of record for spend and identity
  data; OpenSearch holds read-only copies.
- **Loki** (optional `grafana` profile) contains raw container log lines.

## 10. Use SearXNG search through LiteLLM

Both LiteLLM profiles expose the internal SearXNG service as the native
`searxng-search` search tool. SearXNG is internal-only in every profile: it is
attached only to the internal Compose network, has no Traefik route, and
publishes no host port. Clients authenticate to LiteLLM, which is the only way
to reach it; there is no public search UI.

Test production search with:

```sh
curl --fail --show-error --silent \
  https://llm.example.test/v1/search/searxng-search \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"query":"LiteLLM SearXNG integration","max_results":5}'
```

Use `https://llm-dev.example.test` to test the development profile. SearXNG
returns a fixed page of results, so `max_results` may not reduce the number of
results returned by the upstream service.

The beta LiteLLM Chat UI is enabled only for the development profile at
`https://llm-dev.example.test/ui/chat`. Production keeps the Chat UI disabled.
Users must sign in and can only select models allowed by their LiteLLM access
policy. The native `searxng-search` endpoint is available through both gateways;
it is separate from the Chat UI's MCP tool picker.

### Use Xberg MCP document extraction

Both LiteLLM profiles register the internal Xberg Streamable HTTP MCP server as
`xberg`. The container has no published host port and LiteLLM is the
authentication boundary. Only the `extract`, `detect_mime_type`, `get_version`,
and `list_formats` tools are exposed; cache mutation and warming tools are
intentionally excluded.

List the available tools:

```sh
curl --fail --show-error --silent \
  "https://llm.example.test/mcp-rest/tools/list?server_id=xberg" \
  -H "x-litellm-api-key: ******"
```

Extract the official Xberg Docker guide through LiteLLM:

```sh
curl --fail --show-error --silent \
  https://llm.example.test/mcp-rest/tools/call \
  -H "x-litellm-api-key: ******" \
  -H "Content-Type: application/json" \
  -d '{
    "server_id": "xberg",
    "name": "extract",
    "arguments": {
      "input": {
        "kind": "uri",
        "uri": "https://docs.xberg.io/guides/docker/"
      }
    }
  }'
```

Use `https://llm-dev.example.test` for the development profile. Xberg accepts
remote URLs and therefore acts as a server-side fetcher. Grant MCP access only
to trusted keys and teams. Xberg has no direct route to the Internet or the
stack's `internal` network: outbound HTTP and HTTPS must pass through the Squid
proxy, which denies private, loopback, link-local, and non-allowlisted
destinations. Review additions to `security/xberg-egress-allowlist.txt`; the
secure default permits only the documented Xberg documentation host.

The development gateway uses `BAAI/bge-m3` in the internal `bge-m3-vllm`
container to semantically select up to three relevant MCP tools with a minimum
similarity score of `0.3`.
This affects MCP tools offered automatically through Chat Completions and the
Responses API; it does not change MCP permissions or direct
`/mcp-rest/tools/call` requests. Production keeps semantic filtering disabled
because the local model containers are intentionally development-only.

Both LiteLLM profiles register a remote `vllm-sr/d3-mini` deployment as a native
Decisions model. The `coding-router` Auto Router sends only the classification
request to D3, then selects `gpt-4o-mini` for SIMPLE/MEDIUM work and `gpt-4o` for
COMPLEX/REASONING work. If the classifier is unavailable or times out after 15
seconds, routing fails safe to `gpt-4o`. BGE-M3 remains an independent local
pooling service; embeddings never pass through D3.

Deploy D3-mini on the separate NVIDIA GPU machine before starting this stack.
The script requires Docker Compose, one CUDA-capable GPU, a DNS name, an
operator-provided TLS certificate/key, and a random API key of at least 32
characters:

```sh
export D3_SERVER_NAME=d3-mini.example.test
export D3_API_KEY="$(openssl rand -hex 32)"
export D3_TLS_CERT_FILE=/secure/path/d3-mini.crt
export D3_TLS_KEY_FILE=/secure/path/d3-mini.key
export D3_CA_CERT_FILE=/secure/path/issuing-ca.crt
./scripts/deploy-d3-mini.sh
```

The deployment uses immutable CUDA runtime and unprivileged Nginx image digests,
pins the built-in D3-mini model revision, persists model downloads, exposes only
the three Decisions routes over TLS, and checks a real four-tier decision before
reporting success. It is sized for a single 16 GB GPU; use `d3-nano` instead if
the actual GPU cannot provide sufficient runtime headroom. Restrict the remote
port at the firewall to the LiteLLM host. Copy the endpoint and the same secret
to `D3_DECISION_API_BASE` and `D3_DECISION_API_KEY` in this stack's `.env`.
Create `D3_DECISION_CA_BUNDLE` as a combined PEM containing the normal public
root bundle plus the CA that issued D3's certificate. LiteLLM uses that combined
bundle for both D3 and completion-provider TLS verification.

```sh
docker compose --profile dev up -d
```

Call the router through the normal LiteLLM hostname and credential:

```sh
curl --fail --silent https://llm-dev.example.test/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "coding-router",
    "messages": [{
      "role": "user",
      "content": "Analyze this architecture and compare its tradeoffs."
    }],
    "temperature": 0
  }'
```

LiteLLM remains the only client-facing authentication, policy, guardrail,
provider, spend, and observability boundary. The remote listener uses a separate
bearer credential, TLS 1.2+, and a narrow route allowlist. Neither endpoint logs
prompt content by default. Validate D3's coding-tier accuracy on representative
internal prompts before relying on cost savings.

### Development semantic response cache

The development profile uses an authenticated, internal Qdrant service for an
opt-in semantic response cache. LiteLLM embeds request messages with the local
`bge-m3` vLLM model and reuses a previous complete response only when cosine
similarity is at least `0.92`. A cache hit does not call the configured
generation model.

The cache is intentionally:

- development-only and disabled by default for each request;
- limited to asynchronous Chat Completions calls;
- scoped by the authenticated end-user identity in addition to the LiteLLM
  virtual key, team, and organization;
- stored on a bounded 1 GB `tmpfs`, so stopping or recreating Qdrant deletes all
  cached prompts and responses.

Set `QDRANT_API_KEY` to a separate random secret. Opt in only for single-turn,
non-sensitive requests and send a stable authenticated end-user identifier:

```sh
curl --fail --silent https://llm-dev.example.test/v1/chat/completions \
  -H "Authorization: ******" \
  -H "x-litellm-customer-id: example-user-123" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "What is LiteLLM?"}],
    "cache": {"use-cache": true}
  }'
```

Do not opt in multi-turn conversations, tool-calling or agent loops,
permission-sensitive RAG, personalized responses, or time-sensitive queries.
Semantically similar text can have materially different answers, and a false
match replays the earlier answer without calling the model.

After BGE-M3 vLLM and vLLM Semantic Router are healthy, run the end-to-end verification:

```sh
./scripts/verify-semantic-cache.py
```

The check authenticates to Qdrant, sends two different but semantically similar
single-turn prompts, and requires the second request to replay the first
response without creating another cached point. Semantic-cache hits also carry
the `x-litellm-semantic-similarity` response header for clients that inspect
response headers.

## 11. Presidio PII masking

Both LiteLLM profiles use the internal Presidio Analyzer and Anonymizer through
the `presidio-pii` guardrail. It runs in `pre_call` and `post_call` modes by
the globally attached `eu-data-protection` policy, masks detected
English-language PII before input reaches the model provider and before output
reaches the user. The analyzer's NLP configuration includes `PERSON` for
individual/client names and `ORGANIZATION` for company/client organization
names; organization matches require a minimum confidence of 0.7.

Clients may also select the guardrail explicitly by including:

```json
{
  "guardrails": ["presidio-pii"]
}
```

Presidio masking is pseudonymisation, not anonymisation. It is language,
endpoint, model, and confidence dependent; test it against the data and API
surfaces used by each deployment. Do not claim that all personal data is
detected or that no personal data reaches a provider.

### Manage the configured guardrails

Guardrail behavior is owned by three repository files:

1. `litellm/modules/guardrails/presidio.yaml` defines the LiteLLM guardrail,
   its execution modes, Presidio endpoints, language, filtering scope, and
   per-entity confidence thresholds.
2. `litellm/modules/policies/eu-data-protection.yaml` adds that guardrail to a
   policy and attaches the policy globally.
3. `presidio/analyzer.yaml` controls the analyzer's NLP model and maps model
   labels such as `PER` and `ORG` to Presidio entities.

Both `litellm/config.prod.yaml` and `litellm/config.dev.yaml` include the
guardrail and policy modules. A name must match exactly across the guardrail
definition, policy, and any request that selects it explicitly.

#### Change the existing Presidio guardrail

Edit `litellm/modules/guardrails/presidio.yaml` to change when or where it runs:

```yaml
guardrails:
  - guardrail_name: presidio-pii
    litellm_params:
      guardrail: presidio
      mode: [pre_call, post_call]
      presidio_language: en
      presidio_filter_scope: both
      presidio_score_thresholds:
        ORGANIZATION: 0.7
```

- Remove `post_call` only if model output must intentionally remain unscanned.
- Remove `pre_call` only if unmasked input may intentionally reach the model
  provider.
- Change `presidio_filter_scope` only after testing the effect on request and
  response fields.
- Raise an entity threshold to reduce false positives; lower it to increase
  detection sensitivity. Validate both false positives and false negatives.

Edit `presidio/analyzer.yaml` when changing the spaCy model or entity-label
mapping. Removing `ORG: ORGANIZATION`, for example, disables that model label as
a source of organisation detections; it does not guarantee that no other
Presidio recognizer can detect an organisation. This file configures NLP
mapping, not arbitrary LiteLLM policies. Exact company/client deny lists or new
custom entity types require a supported Presidio recognizer implementation and
representative tests; do not place sensitive production client lists in Git.

#### Add another LiteLLM guardrail

Add a new item under `guardrails:` in a file under
`litellm/modules/guardrails/`, or create a separate module and add its path to
the `include:` list in both profile configurations. Give it a unique
`guardrail_name` and use only parameters supported by the selected LiteLLM
guardrail provider.

To apply it automatically, add its exact name to the policy:

```yaml
policies:
  eu-data-protection:
    guardrails:
      add:
        - presidio-pii
        - new-guardrail-name
```

Defining a guardrail does not apply it globally by itself. It must be added by
an attached policy, selected explicitly in a request, or attached through
another supported LiteLLM scope. Keep global privacy controls in the shared
policy rather than duplicating them in individual model definitions.

#### Disable or remove a guardrail

Use the least destructive option that matches the intended change:

- **Stop automatic application but keep it available:** remove its name from
  `guardrails.add` in `eu-data-protection.yaml`. Callers can still request a
  guardrail that remains defined.
- **Disable the entire global policy:** remove its entry from
  `policy_attachments`. The policy remains defined but is not applied.
- **Remove the guardrail completely:** first remove every policy and request
  reference, then remove its definition. A dangling policy reference can stop
  LiteLLM from loading successfully.
- **Remove Presidio from the stack:** only after removing the guardrail and
  policy references, remove the Analyzer and Anonymizer services and their
  Compose dependencies. Do not leave a configured guardrail pointing at absent
  services.

Disabling `pre_call`, removing the global attachment, or removing Presidio
changes the data-protection boundary and may allow personal data to reach model
providers. Review that change before deployment rather than treating it as a
routine operational toggle.

#### Apply and verify a change

Restart the components that consume the changed files:

```sh
# LiteLLM guardrail or policy module changed
docker compose --profile prod up -d --force-recreate litellm-prod
docker compose --profile dev up -d --force-recreate litellm-dev

# Presidio analyzer configuration changed
docker compose up -d --force-recreate presidio-analyzer
```

Check both profiles after shared-module changes. Resolve the live policy and
send representative positive and negative examples:

```sh
curl --fail --silent https://llm.example.test/policies/resolve \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini"}'

curl --fail --silent https://llm-dev.example.test/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"gpt-4o-mini",
    "messages":[{"role":"user","content":"Reply exactly: Example Client Limited"}],
    "temperature":0
  }'
```

The resolved policy must list every automatically applied guardrail. For the
masking test, the response must not contain the original organisation name.
Also test harmless text that should remain unchanged, inspect LiteLLM and
Presidio logs for errors, and confirm prompt/response bodies were not written to
logs. Never use real personal or client data in a guardrail test.

## 12. EU technical-control baseline

The stack includes a repository-owned technical baseline informed by:

- [GDPR Articles 5, 25, 30, 32, 33, 35, and 44](https://eur-lex.europa.eu/eli/reg/2016/679/oj)
- [EU AI Act Articles 4, 12, 14, 26, and 50](https://eur-lex.europa.eu/eli/reg/2024/1689/oj)
- [EDPB Opinion 28/2024 on AI models](https://www.edpb.europa.eu/documents/opinion-of-the-board-art-64/opinion-282024-on-certain-data-protection-aspects-related-to_en)
- [ENISA's multilayer framework for AI cybersecurity](https://www.enisa.europa.eu/publications/multilayer-framework-for-good-cybersecurity-practices-for-ai)

This is a technical-control baseline, not legal advice, certification, or a claim
that a deployment complies with the GDPR or EU AI Act.

### Modular configuration

Both profile roots use LiteLLM's native `include` support:

- `litellm/modules/search.yaml` defines the internal SearXNG integration.
- `litellm/modules/guardrails/presidio.yaml` defines bidirectional PII masking.
- `litellm/modules/policies/eu-data-protection.yaml` defines and globally
  attaches the `eu-data-protection` policy.
- `litellm/config.prod.yaml` and `litellm/config.dev.yaml` retain only
  profile-specific models, timeouts, UI behavior, and shared privacy settings.

Policy attachments are required: defining a policy without attaching it does not
apply its guardrails. Check the live configuration with:

```sh
curl --fail --silent https://llm.example.test/policies/list \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY"

curl --fail --silent https://llm.example.test/policies/resolve \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini"}'
```

The resolved policy must include `presidio-pii`. Repeat the check on the
development hostname after policy changes.

### Data minimisation and retention

- LiteLLM message logging is disabled while non-content spend metadata remains
  available for accountability and operations.
- Langfuse receives LiteLLM OTel traces with prompt and response content
  disabled. Custom request metadata can still contain sensitive values and must
  be allowlisted or scrubbed by clients.
- Prompt and response bodies are explicitly excluded from spend logs.
- LiteLLM spend metadata is retained for 180 days, then deleted by a daily
  cleanup job. This duration supports the six-month high-risk deployer logging
  period in AI Act Article 26(6) when that obligation applies; it does not by
  itself establish that a system is high risk or that the retention period is
  appropriate for every purpose.
- LiteLLM health-check rows are retained for 7 days.
- OpenSearch container logs are retained for 7 days, LiteLLM trace spans,
  mirrored metrics, and alert-rule state for 15 days, LiteLLM usage copies for 180 days, and the
  OpenSearch security audit log for 90 days (monthly indices, so up to one
  extra month). See `OPENSEARCH_*_RETENTION_DAYS`.
- Loki container logs (optional `grafana` profile) are retained for 7 days.
- Prometheus metrics are retained for 15 days.
- Traefik omits client addresses, ports, and user names from its JSON access
  logs to avoid persisting client IP addresses in OpenSearch or Loki.
- Traefik writes JSON access logs without client addresses, ports, user names,
  or request headers. Alloy redacts `q=`/`query=` URL parameters in every log
  line, so search terms (for example in SearXNG upstream errors) are not
  persisted.
- LiteLLM trace spans carry the same content-free attributes as the Langfuse
  traces (model, route, token counts, cost, guardrail outcome, and the
  pseudonymous user ID) and are readable only by `litellm_operator`. Set
  `LITELLM_OTEL_TRACE_EXPORTER=console` only for short debugging sessions; it
  prints spans to stdout and therefore into the container logs.
- `opensearch-bridge` never copies prompts, responses, request metadata,
  client IP addresses, virtual-key hashes, provider exception text, or audit
  before/after values out of PostgreSQL.
- LiteLLM audit logging is enabled for supported administrative changes.
- Langfuse trace metadata is stored in its dedicated PostgreSQL, ClickHouse,
  Redis, and MinIO volumes. ClickHouse keeps warning-or-higher server logs
  continuously, rotates each file channel at 100 MB with three files retained,
  and retains query, view, part, scheduler, and structured warning logs for 7
  days plus error logs for 30 days. High-volume query profiler, internal metric,
  and internal OpenTelemetry tables are disabled; equivalent live capacity and
  engine counters are scraped by Prometheus and surfaced on **Infrastructure:
  Host & Containers** with disk-pressure, scrape-failure, and log-storm alerts.
  This preserves always-on incident evidence without requiring an operator to
  predict when to enable diagnostics.
- Langfuse application event retention remains a separate concern. The
  project-level automatic retention feature is available only in Langfuse
  Enterprise Edition; OSS self-hosted event data is indefinite by default.
  Define and operate a supported Langfuse deletion/export policy and a complete
  backup policy before production use. The database-only backup scripts in this
  repository do not constitute a complete Langfuse backup.

These periods must be recorded in the deployment's retention schedule and
reviewed against its documented purposes. Restrict proxy-admin access because
database-backed UI settings can alter runtime behavior. If another callback,
cache, object store, tracing system, or provider logging feature is enabled, add
that system to the data-flow and retention review.

### Integrity and transport

Every registry image is pinned to an immutable digest. The development
`litellm-pgvector` image is built from the official BerriAI repository at a
full, signed Git commit. Renovate or manually review upstream release notes,
update the matching digest or source commit, and re-run the complete validation
before deploying an update.

External routes use TLS through Traefik. The LiteLLM and Langfuse containers
trust forwarded headers because they are not published directly and only
Traefik is expected to reach them on the edge network. Internal Compose traffic,
including PostgreSQL, ClickHouse, Redis, MinIO, Loki, Presidio, SearXNG, and the
Alloy-to-Data Prepper OTLP stream, is not encrypted. OpenSearch traffic always
uses verified TLS. Treat the isolated Docker networks as a compensating
control for local use; enable authenticated internal TLS before using an
untrusted or multi-host network.

### Required deployment decisions outside this repository

Before operating the stack with personal data, the responsible organisation
must separately:

- identify controller, processor, provider, and deployer roles;
- document purposes, lawful basis, data categories, recipients, retention, and
  technical measures in its record of processing;
- assess whether a DPIA is required and classify every AI Act use case,
  including prohibited and high-risk uses;
- execute processor agreements and subprocessor controls;
- establish a valid Chapter V transfer mechanism and transfer impact assessment
  for non-EU recipients, or configure an eligible EU-resident provider;
- implement data-subject access, correction, export, restriction, and deletion
  procedures across PostgreSQL, OpenSearch, Loki, provider systems, and backups;
- define breach detection, documentation, escalation, and the GDPR 72-hour
  notification process;
- appoint trained human oversight with the authority to revoke keys, disable
  models, stop processing, and override model output;
- inform users when they interact with AI and address provider-side marking of
  AI-generated content where Article 50 applies; and
- test Presidio and policy effectiveness regularly with representative,
  multilingual data. The bundled analyzer supports English only.

The default models call OpenAI and this configuration does not prove EU data
residency. Confirm the provider account's region, retention, training, and abuse
monitoring settings plus the applicable contractual transfer safeguards before
sending personal data.

## 13. Verify the LiteLLM database schema

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

## 14. pgvector storage

The stack deploys a separate PostgreSQL 16 database with pgvector 0.8.7. Its
named `pgvector-data` volume starts with an empty `sme-knowledge` store. In the development profile, Xberg extraction indexing is disabled by default.
When the operator explicitly sets `XBERG_AUTO_INDEX_ENABLED=true`, successful
Xberg `extract` calls with trusted principal attribution are anonymized through
Presidio, chunked, and indexed in this store using local `bge-m3` embeddings.
Docker named volumes grow with stored data; Compose does not preallocate 50 GB.
Monitor the volume and host filesystem and alert before either is exhausted.

The schema uses 1,024-dimensional vectors for the local multilingual `bge-m3`
embedding model, an HNSW cosine-distance index, JSON metadata filtering,
cascading deletion by store, and content length constraints. The database is
only reachable on the internal Compose network. A separate PostgreSQL exporter
exposes database metrics to Prometheus.

The development profile builds BerriAI's official `litellm-pgvector` connector
from commit `5bd8f3ab1fa9129e758a2c4b17ba1c6f3047ba24`. The connector is internal
only, authenticates every vector-store request, and uses development LiteLLM to
embed search queries with the vLLM-backed `bge-m3` model. `sme-knowledge` is declared
in `litellm/config.dev.yaml`, so it appears in the development LiteLLM Vector
Stores UI and survives proxy database resets because the registration is
repository-owned configuration.

Set `PG_VECTOR_API_KEY` to a separate, cryptographically random secret in
`.env`, then build and start the development profile:

```sh
docker compose --profile dev up -d --build
```

The official connector is intentionally development-only. Production neither
lists the store nor depends on the local vLLM model services. Before enabling it in
production, select and validate a production embedding model, data residency,
credentials, capacity, and availability independently.

Validate the store and rejection controls with:

```sh
./scripts/verify-pgvector.py
```

The check verifies unauthenticated rejection at LiteLLM and the connector,
confirms the managed store appears in LiteLLM, searches it through the official
connector and local embedding model, and attempts invalid database
writes with an unknown store, the wrong vector dimensions, and empty content.
Every invalid attempt must fail and the final vector row count must remain
unchanged.

Opt-in automatic indexing is implemented by
`litellm/modules/xberg_auto_index.py` through LiteLLM's
`async_post_mcp_tool_call_hook`. It applies only to the `xberg` server's
`extract` tool and stores the trusted pseudonymous principal in chunk metadata.
Each source is limited to 1,000,000 extracted characters and
256 chunks; chunks are approximately 4,000 characters with overlap. Repeating
an extraction atomically replaces that source's existing chunks instead of
creating duplicates. URL query strings, fragments, and credentials are not
stored in source metadata.

Extracted content must pass through Presidio before embedding; a Presidio
failure fails closed for indexing while leaving the original tool response
available. Indexing is best effort and runs as a bounded background task after extraction:
an embedding-service or vector-database failure is logged while the original
Xberg response is still returned to the caller. The task times out after three
minutes and can be lost if the development proxy stops before it finishes; call
`extract` again to retry. The callback is enabled only in development because
the pgvector connector and local `bge-m3` service are development-only.

Before production ingestion, select a reviewed component that provides:

- authenticated ingestion and search APIs;
- document-, tenant-, and team-level authorization;
- chunking, embedding, and prompt-injection controls;
- source identifiers that support complete update and erasure;
- bounded request sizes, rate limits, and generic errors; and
- retention, backup, restore, and data-subject deletion procedures.

## 15. Install the maintenance runtime

Repository maintenance commands are executable
[uv scripts](https://docs.astral.sh/uv/guides/scripts/). Install `uv` using one
of its [documented installation methods](https://docs.astral.sh/uv/getting-started/installation/)
and make sure `uv` is on `PATH`:

```sh
uv --version
```

Each entry point has a `uv run --script` shebang and PEP 723 inline metadata, so
the documented `./scripts/<name>.py` commands need neither an activated virtual
environment nor a separately managed system Python. The scripts currently use
Python's standard library for maintenance logic. The two HTTPS verification
scripts additionally pin `truststore==0.10.5` so certificate validation uses the
operating system's native trust store, including mkcert roots installed there.
The full upgrade suite also locates the standard or `CAROOT`-configured mkcert
root with Python for roots installed only in an NSS/user store. Their adjacent uv
lock files record the exact resolution. `uv` enforces the declared Python version
and can acquire a matching interpreter when one is not installed.

### Why use Python and uv for these scripts?

For this repository, the practical advantages are:

- **Less shell parsing and platform branching.** HTTP, JSON, hashing, paths,
  permissions, temporary directories, timestamps, and retention traversal use
  typed Python APIs instead of pipelines of `curl`, `jq`, `grep`, `find`,
  `sha256sum`, and platform-specific alternatives.
- **Safer process boundaries.** The remaining external boundary is primarily
  Docker Compose. Commands are passed as argument arrays without a shell, which
  avoids quoting ambiguity and prevents command interpolation.
- **One declared runtime.** PEP 723 metadata records the supported Python version
  next to each entry point. According to the uv script documentation, `uv`
  creates managed environments on demand, respects `requires-python`, and keeps
  script dependencies separate from project dependencies.
- **Reproducible growth.** If a future script needs a third-party package, it can
  declare and pin it inline and use `uv lock --script` to create an adjacent lock
  file instead of relying on whatever happens to be installed globally. The
  HTTPS verification scripts already follow this pattern for `truststore`.
- **Testable orchestration.** Parsing, validation, checksums, retention rules,
  HTTP handling, and workflow selection are regular Python functions rather than
  shell blocks that require process-level fixtures to exercise.

These are maintainability and correctness benefits, not proof that Python is
universally better than shell. A tiny command wrapper may start faster as shell,
and this approach adds `uv` as a host prerequisite. uv publishes substantial
package-management speedups, but these scripts install only one small pinned
dependency for two verification entry points, and only when it is not cached.
Their wall-clock time is dominated by Docker, PostgreSQL, and network operations.
The migration therefore claims no meaningful workflow speedup. Its success
criterion is behavioral parity with a smaller dependence on host shell utilities.

## 16. Monitor deployed image vulnerabilities

The repository includes a fail-closed, scheduled image-security monitor. It
uses Trivy `0.76.1` from an immutable container digest, downloads a fresh
vulnerability database, retrieves and validates the CISA Known Exploited
Vulnerabilities (KEV) catalog, and scans the platform-specific digest actually
used by each configured gateway and local-model container:

```bash
./scripts/maintenance.py security-scan
```

The default scope is every running service in the Compose project. This includes
`bge-m3-vllm` whenever the development profile is active, without
reporting intentionally inactive profile services as unknown. Restrict a manual
scan only when a narrower reviewed scope is required:

```bash
./scripts/maintenance.py security-scan \
  --services litellm-dev,bge-m3-vllm
```

The monitor reads running-container metadata directly from Docker. It never
persists or logs rendered Compose configuration, which may contain interpolated
secrets. Digest-pinned registry images are scanned remotely so the Trivy
container does not need the Docker socket. A running local or mutable image is
reported as `not-applicable`/unknown instead of being presented as clean.

The normal scheduled mode reports findings and returns nonzero only for
operational failures. Use `--enforce` in a reviewed release gate to also fail on
an unexcepted CISA KEV match or fixable Critical finding:

```bash
./scripts/maintenance.py security-scan --enforce
```

### Reports, state, and metrics

Runtime output is ignored by Git and separated by sensitivity:

| Directory | Mode | Contents |
|---|---:|---|
| `security-state/` | `0700` | Private state, Trivy and uv caches, and scheduler lock |
| `security-reports/` | `0700` | Timestamped private Trivy JSON and KEV evidence; files are `0600` |
| `security-metrics/` | `0755` | One aggregate `0644` Prometheus textfile with no CVE IDs or secrets |

Override these roots with `IMAGE_SECURITY_STATE_DIR`,
`IMAGE_SECURITY_REPORT_DIR`, and `IMAGE_SECURITY_METRICS_DIR`. Set
`IMAGE_SECURITY_RETENTION_DAYS` to change the default 30-day report retention,
and `IMAGE_SECURITY_COMMAND_TIMEOUT_SECONDS` to change the default 900-second
per-command limit.

Private JSON state is the source of truth. Every attempt regenerates the entire
Prometheus file, including explicit failed/never-successful sentinels. A failed
database update, registry request, KEV retrieval, or image scan therefore
cannot leave a stale `success=1` result. The metrics are written to a non-`.prom`
temporary file, flushed, synchronized, and atomically renamed without
client-side sample timestamps.

### Time-limited exceptions

Security exceptions live in `security/exceptions.toml`. Each exception must
identify the service, vulnerability, package, installed version, reviewer,
technical reason, and expiry date. Raw reports remain complete; exceptions
only affect the unexcepted policy metrics and `--enforce` result.

Do not add permanent wildcard exceptions. An expired exception is no longer
applied, and Grafana warns seven days before the earliest active exception
expires.

### Prometheus, OpenSearch, and Grafana alerts

The Compose configuration enables node-exporter's official textfile collector
and mounts `security-metrics/` read-only. The rules under `grafana/alerting/`
become OpenSearch Alerting monitors by default and are also provisioned into
Grafana when the optional `grafana` profile runs. Recreate these services after
changing a rule:

```bash
docker compose up -d --force-recreate node-exporter opensearch-setup opensearch-bridge
docker compose --env-file .env --env-file .env.optional \
  --profile grafana up -d --force-recreate grafana
```

Provisioned rules alert on:

- unexcepted fixable Critical and CISA KEV findings;
- per-service scan failure or a result older than 36 hours;
- failed, missing, or stale scheduler runs;
- missing/unparseable node-exporter textfile metrics;
- Trivy database and KEV feed staleness;
- running images outside scan coverage; and
- exceptions expiring within seven days.
- production LiteLLM becoming unavailable after previously being healthy;
- PostgreSQL exporter loss and host disk exhaustion; and
- a missing or stale complete database backup.

The repository deliberately does not commit notification credentials. Set
`OPENSEARCH_ALERT_CHANNEL_ID` to an OpenSearch Notifications channel (see
**Alerting** in section 9); with the Grafana profile, also configure a Grafana
contact point and notification policy. A firing rule without a routed channel is
visible in the UI but cannot notify an operator.

Traefik and Alloy reach Docker metadata through dedicated read-only socket
proxies with Docker API `POST` disabled. They do not mount the Docker socket
directly. Keep those proxies on their isolated internal networks and do not
enable additional API sections unless the consumer demonstrably requires them.

The same maintenance workflow verifies coding routing after upgrades. With the
development profile running, the check fails unless the D3 Decisions request
succeeds and the Auto Router response reports the decision classifier rather
than a silent fallback:

```bash
./scripts/maintenance.py upgrade-verify \
  --only dev-vllm-embedding,coding-routing,observability
```

### Run the scheduler inside the stack

The optional `security` Compose profile runs the same scanner and policy inside
the stack. It executes once when started and then daily at 02:45 in the host's
timezone. Set the deployment account's Linux UID and GID in `.env.optional` so the
non-root monitor can write the three bind-mounted runtime directories:

```bash
IMAGE_SECURITY_UID=1000
IMAGE_SECURITY_GID=1000
```

Use `id -u` and `id -g` on the Linux deployment host rather than assuming those
example values. Start only the security services:

```bash
docker compose --env-file .env --env-file .env.optional \
  --profile security up -d --build image-security-monitor
docker compose --env-file .env --env-file .env.optional \
  logs -f image-security-monitor
```

Change the daily time or disable the immediate startup scan with
`IMAGE_SECURITY_SCHEDULE_HOUR`, `IMAGE_SECURITY_SCHEDULE_MINUTE`, and
`IMAGE_SECURITY_RUN_ON_START` in `.env.optional`. `COMPOSE_PROJECT_NAME` must match the
project label on the containers being scanned; `.env.example` pins it to
`litellm-stack` so it does not vary with the checkout directory.

The in-stack scheduler scans every running service. Profile-specific services
that are intentionally stopped are not emitted as unknown coverage, while any
running immutable image outside the former fixed list is included automatically.

The monitor image contains the same pinned Trivy release and invokes it directly,
so it does not create nested scanner containers. It obtains only read-only
container and image metadata through the separately pinned
`image-security-docker-proxy`; the monitor itself never receives the host Docker
socket. The proxy is isolated on a dedicated, unpublished network, permits only
Docker `GET` requests for the container and image API sections, and denies all
write requests. The proxy necessarily mounts the Docker socket, so keep it
isolated and pinned as configured.

Stop and remove the in-stack scheduler with:

```bash
docker compose --env-file .env --env-file .env.optional \
  --profile security rm -sf image-security-monitor image-security-docker-proxy
```

### Install the nightly Linux host schedule

The alternative host-scheduled mode uses a system-level systemd timer. Install
it at 02:45 local time while explicitly selecting a non-root account that already
has access to the Docker daemon:

```bash
sudo /usr/local/bin/uv run --script scripts/maintenance.py \
  security-schedule install \
  --service-user litellm \
  --uv-path /usr/local/bin/uv \
  --project litellm-stack
```

Choose another time when needed:

```bash
sudo /usr/local/bin/uv run --script scripts/maintenance.py \
  security-schedule install \
  --service-user litellm \
  --uv-path /usr/local/bin/uv \
  --project litellm-stack \
  --hour 3 \
  --minute 30
```

Replace `litellm` and `/usr/local/bin/uv` with the deployment account and the
absolute `uv` path on the host. Do not use `root` as the service account. The
installer writes `litellm-stack-image-security.service` and
`litellm-stack-image-security.timer` under `/etc/systemd/system`, enables the
persistent timer, and grants write access only to the state, report, and metrics
directories. The service applies systemd filesystem, device, privilege, kernel,
and process hardening while retaining the network and Docker access required by
the scanner. Output is available in the system journal:

When the deployment uses non-default runtime locations, pass them explicitly
rather than relying on environment preservation through `sudo`:

```bash
--state-dir /var/lib/litellm-security/state \
--report-dir /var/lib/litellm-security/reports \
--metrics-dir /srv/litellm-stack/security-metrics \
--exceptions-file /srv/litellm-stack/security/exceptions.toml
```

```bash
systemctl status litellm-stack-image-security.timer
journalctl -u litellm-stack-image-security.service
```

Inspect or remove the schedule through the maintenance entry point:

```bash
./scripts/maintenance.py security-schedule status
sudo /usr/local/bin/uv run --script scripts/maintenance.py \
  security-schedule uninstall
```

The scanner service fails if the selected account cannot access Docker. Verify
that access before installation with `sudo -u litellm docker info`. Keep the
working tree and `security-metrics/` path accessible to both that account and
Docker.

Use either the Compose scheduler or the systemd timer for a deployment, not both.
The scanner lock prevents concurrent corruption, but duplicate schedules add no
coverage and can create avoidable failed-run alerts.

Before enabling vulnerability pages, verify scanner-health alerts by testing a
missing metrics file, unavailable registry, failed Trivy database update, stale
KEV feed, and a partial service-scan failure. Enable KEV/Critical notification
routing only after these failure signals are proven.

## 17. Back up the databases

Run the repository-owned backup script while `postgres` and `vector-postgres`
are healthy:

```bash
./scripts/backup-databases.py
```

It creates PostgreSQL custom-format dumps for `litellm_prod`, `litellm_dev`, and
`vector_store`, validates that each archive can be read by `pg_restore`, writes
SHA-256 checksums, and atomically publishes the completed directory under
`backups/`. Backup directories and files are private to the current operating
system user. Partial backups are removed on failure and are never published as
complete.

If a configured database service is unavailable or a database does not exist,
the script writes a warning, records the reason in `metadata.txt`, and continues
with the next database. A backup containing the remaining resources is still
published and its checksum manifest includes only files that were created, but
the command exits nonzero and does not prune older backups. This prevents a run
of partial backups from aging out the last complete recovery point. The script
fails without publishing an empty backup when no configured database is
available. Errors while dumping or validating an existing database remain fatal
so a corrupt archive is never reported as successful.

After a complete backup, the script atomically publishes
`litellm_backup_last_success_timestamp_seconds` in
`security-metrics/litellm_backup.prom`. Node exporter exposes that value to
Prometheus. Partial and failed backups do not advance it, so the provisioned
backup-age alert detects a stale recovery point.

Choose a destination on encrypted storage and change the default 14-day
retention when needed:

```bash
BACKUP_DIR=/secure/litellm-backups \
BACKUP_RETENTION_DAYS=30 \
./scripts/backup-databases.py
```

Only directories created by the script with the expected name, metadata,
checksum manifest, and at least one known database dump are eligible for
retention cleanup. A directory containing an unexpected file, subdirectory, or
symbolic link is retained and produces a warning.

For a daily cron job at 02:15 UTC, use absolute repository and `uv` paths.
Find the latter with `command -v uv` in the account that owns the cron job:

```cron
CRON_TZ=UTC
15 2 * * * cd /absolute/path/to/litellm-stack && BACKUP_DIR=/secure/litellm-backups BACKUP_RETENTION_DAYS=30 /absolute/path/to/uv run --script ./scripts/backup-databases.py >>/var/log/litellm-backup.log 2>&1
```

The script intentionally does not copy `.env`, TLS private keys, or live
OpenSearch, Grafana, Loki, and Prometheus volume files. Keep secrets in their existing
secret-management system. Use storage-level snapshots designed for those
observability volumes if their historical data must be retained. Replicate
completed backups to access-controlled, encrypted off-host storage and test
restores regularly; a local backup alone does not protect against host loss.

To restore into a new instance, copy the complete timestamped backup directory
to the target host, configure its `.env`, and start only the database services:

```bash
docker compose up -d --wait postgres vector-postgres
./scripts/restore-databases.py \
  --confirm-restore \
  /secure/litellm-backups/<backup>
```

The confirmation flag is mandatory because each database found in the backup
replaces its current target. The restore script refuses to run while
`litellm-prod`, `litellm-dev`, `litellm-pgvector`, Grafana, `opensearch-bridge`,
or the pgvector exporter is running. It rejects symbolic links, unexpected files, unknown
databases, malformed metadata, unsafe checksum entries, and checksum mismatches
before changing a target database.

Full and fault-tolerant partial backups are supported. A database omitted from
the backup metadata is skipped. If a required target database service is
unavailable, the script records that restore as failed, continues with the
remaining databases, and exits nonzero after reporting all failures. Each
archive is restored with a single transaction into a staging database and
checked for restored tables before the existing target is replaced. An archive
error therefore leaves the existing target untouched. Before promotion, an
existing target is renamed to a process-specific `*_pre_restore_*` safety
database rather than dropped. The script terminates stale database sessions and
automatically restores that prior database if promotion or the post-promotion
connection check fails. After a successful application-level verification,
remove the preserved safety database explicitly to reclaim its storage.

`SHA256SUMS` detects accidental corruption but is not a cryptographic signature:
someone able to replace both a dump and its manifest can produce matching new
checksums. Use access-controlled backup storage and add signed manifests through
the organization's backup system when integrity against a malicious writer is
required.

Test the procedure in a non-production instance regularly. After a successful
restore, start the application services and rerun the stack verification checks.

For the quarterly failure and recovery exercise, follow
[`OPERATIONS-GAME-DAY.md`](OPERATIONS-GAME-DAY.md). Its non-disruptive preflight
checks the topology, alert, backup, restore, and rollback contracts before any
live drill begins:

```bash
./scripts/maintenance.py game-day-preflight --env-file .env
```

## 18. Validate and roll back a LiteLLM image upgrade

Before evaluating which development-only capabilities can move to production,
review the versioned
[`development-to-production capability promotion register`](docs/dev-to-prod-promotion-register.md).
For each new stable LiteLLM candidate, use the repository-owned
[`release-delta prompt`](docs/prompts/create-dev-to-prod-delta.md) to create a
separate evidence-based delta. Image upgrades and capability activation are
separate rollout steps.

The single maintenance entry point exposes the routine operational workflows:

```bash
./scripts/maintenance.py help
./scripts/maintenance.py doctor
./scripts/maintenance.py status
./scripts/maintenance.py game-day-preflight
./scripts/maintenance.py backup
./scripts/maintenance.py post-upgrade
```

It delegates to the focused scripts below without weakening their validation,
confirmation flags, or exit codes. Restore and rollback remain explicit:

```bash
./scripts/maintenance.py restore \
  --confirm-restore \
  /secure/litellm-backups/<backup>

./scripts/maintenance.py rollback \
  --confirm-rollback \
  --mode image-only
```

Use an immutable candidate reference containing both a tag and digest. Run the
compatibility check before editing `docker-compose.yml`; `--pull` is required
only when the candidate is not already present locally:

```bash
CANDIDATE_IMAGE='ghcr.io/berriai/litellm-database:<version>@sha256:<digest>'

./scripts/check-litellm-upgrade-compatibility.py \
  --candidate-image "$CANDIDATE_IMAGE" \
  --pull
```

The compatibility check and rollback-point preparation can also run as one
fail-fast command:

```bash
./scripts/maintenance.py pre-upgrade \
  --candidate-image "$CANDIDATE_IMAGE" \
  --pull
```

The compatibility script never reads `.env`, connects to the stack databases,
or changes running services. It performs static configuration checks and runs
the candidate's own configuration validator in network-isolated, read-only,
capability-dropped containers with placeholder credentials.

Immediately before changing the image digest, capture the deployed images,
configuration checksums, and a fresh database backup:

```bash
./scripts/prepare-litellm-upgrade.py
```

The resulting private state is stored under ignored `upgrade-state/`. Do not
edit or move its files independently: the rollback script validates their
checksums and configuration drift. `--skip-backup` is available only when an
image-only rollback is sufficient and the database schema cannot become
incompatible.

After updating both LiteLLM image references to the tested digest and recreating
the proxies, run the complete regression suite:

```bash
./scripts/verify-litellm-upgrade.py
```

List or select checks when diagnosing a failure:

```bash
./scripts/verify-litellm-upgrade.py --list
./scripts/verify-litellm-upgrade.py --only compose-health,prod-auth,logs
```

The suite checks Compose health and exposure, authentication, prod/dev
isolation, vLLM embeddings, vLLM Semantic Router routing,
pgvector, Presidio behavior, SearXNG, Xberg MCP extraction, Prometheus targets,
OpenSearch health, dashboards, monitors, data sources, bridge freshness, and log
delivery, plus the Grafana data sources and Loki log delivery when the optional
`grafana` profile runs. A
missing optional prerequisite is reported as `SKIP`; once a prerequisite is
present, a failed assertion makes the command exit nonzero.

If the prior image can read the current schema, roll back images only:

```bash
./scripts/rollback-litellm-upgrade.py \
  --confirm-rollback \
  --mode image-only
```

If the upgrade migrated the database incompatibly, restore the pre-upgrade
backup and images together:

```bash
./scripts/rollback-litellm-upgrade.py \
  --confirm-rollback \
  --mode restore-db
```

Rollback refuses configuration drift by default, uses an exclusive lock,
validates immutable image references and state checksums, stops application
services before database restoration, checks proxy liveliness afterward, and
writes a private report. It leaves application services stopped when database
restoration fails.

The runtime rollback writes the validated image override to the ignored
`docker-compose.override.yml` file, which Docker Compose loads automatically.
The override remains active across ordinary `docker compose up` and maintenance
commands until the pinned image in `docker-compose.yml` is deliberately updated
and the override is removed. A copy is also retained with the rollback report.

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
PostgreSQL, OpenSearch, Grafana, and Prometheus data, including the private
OpenSearch CA. That operation is destructive.

## Security notes

- Keep `.env` and everything under `traefik/certs/` private.
- Rotate any credential that is accidentally exposed.
- The Docker socket mounted by Traefik is security-sensitive; restrict host
  access.
- Do not expose the `dev` profile or its logs to untrusted users.
- Keep image digests immutable in deployed revisions and review updates rather
  than returning to floating tags.
