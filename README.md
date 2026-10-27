# LiteLLM stack

A Docker Compose stack for running separate LiteLLM production and development
proxies behind Traefik, with PostgreSQL, Presidio, SearXNG, Xberg MCP,
Langfuse, development vLLM model servers, Prometheus, Grafana, Loki, Grafana
Alloy, and host/container exporters.

The `prod` and `dev` names below describe LiteLLM runtime profiles. The on-prem
deployment expects a certificate delivery process to place the configured TLS
certificate and private key on the host. The bundled `mkcert` workflow remains
available only for a private workstation or trusted development network.

## Prerequisites

- Docker Engine or Docker Desktop with Docker Compose v2
- Python 3.11 or newer for the local CI validation entrypoint
- An NVIDIA GPU and NVIDIA Container Toolkit for the development profile's
  BGE-M3 vLLM service
- Up to 7 GB of available RAM for the CPU-hosted vLLM Semantic Router
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
does not generate production credentials, provider keys, or TLS material.
Replace placeholders before starting any service.

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

Replace every required placeholder, review the optional operational defaults,
and validate the result before startup:

```sh
docker compose config --quiet
```

See the [environment variable reference](docs/environment-variables.md) for the
complete catalog, accepted formats, defaults, security classification, rotation
guidance, change impact, and operational practices.

Do not commit `.env`, provider keys, database passwords, rendered Compose
configuration, or TLS private keys. The example `.test` names are reserved for
testing. If you choose other names, use the same names in `.env`, the certificate
command, and host resolution.

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
  llm.example.test llm-dev.example.test llm-router.example.test grafana.example.test search.example.test \
  langfuse.example.test langfuse-s3.example.test
```

If `.env` uses different hostnames, substitute all names in the final command.

`mkcert` creates a locally trusted CA and a private key. Its certificates are
for local/private use only: browsers on other devices will not trust them
unless that device explicitly trusts the local CA. Never publish the generated
private key or the CA root key. Do not use the local fallback for the on-prem
deployment.

## 3. Resolve the hostnames

For a stack running on this machine, add one line to `/etc/hosts`:

```text
127.0.0.1 llm.example.test llm-dev.example.test llm-router.example.test grafana.example.test search.example.test langfuse.example.test langfuse-s3.example.test
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

Synthetic production dashboard data is opt-in. To seed it explicitly, activate
the `demo` profile together with production:

```sh
docker compose --profile prod --profile demo up -d
```

The production and development proxies use separate databases and config files,
while PostgreSQL, Traefik, Langfuse, Grafana, Prometheus, SearXNG, Presidio,
and exporters are shared.

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
curl --fail --show-error --silent \
  'https://langfuse.example.test/api/public/health?failIfDatabaseUnavailable=true'
curl --fail --show-error --silent --output /dev/null https://search.example.test/
```

For the development profile, also verify the internal router readiness and
metrics endpoints without publishing them to the host:

```sh
docker compose exec -T vllm-semantic-router \
  curl --fail --silent http://127.0.0.1:8080/ready
docker compose exec -T vllm-semantic-router \
  curl --fail --silent http://127.0.0.1:9190/metrics >/dev/null
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

## 9. Use the hybrid logging pipeline

LiteLLM requests are traced to Langfuse through LiteLLM's OpenTelemetry v2
`langfuse_otel` integration. Open `https://langfuse.example.test`, sign in with
the initial administrator account, and select the pre-provisioned **LiteLLM**
project. Traces include model, provider, token, cost, timing, and error metadata.
Prompt and response content is deliberately excluded by both
`turn_off_message_logging: true` and
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=no_content`.

Langfuse is an LLM tracing platform, not a general Docker log collector. Alloy
therefore remains in the stack and sends stdout/stderr from LiteLLM and every
other Compose service to Loki. This hybrid split preserves the existing
infrastructure and application log coverage while adding structured LiteLLM
traces. Do not enable the legacy `langfuse` callback alongside
`langfuse_otel`, because doing so duplicates traces.

The callback is global in both LiteLLM profiles, so requests made by every
authenticated LiteLLM user or virtual key are traced to the administrator
project. The trusted pre-call hook replaces any caller-supplied `trace_user_id`
with a stable HMAC pseudonym derived from the authenticated LiteLLM user ID (or
the authenticated key identity when no user owns the key). It also strips
caller-supplied `langfuse_*` headers and pseudonymizes a supplied `user` field.
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

The production LiteLLM proxy does not use Xberg or Langfuse health as a startup
gate. Tracing and extraction are auxiliary capabilities: an outage in either
must not prevent the core authenticated model gateway from starting or being
recreated. Their failures remain visible through service health, request errors,
and the observability checks.

### View container logs in Grafana

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

Grafana provisions five dashboards in the **LiteLLM** folder:

- **LiteLLM Stack (dev/prod)** for request, latency, failure, spend, deployment,
  and guardrail metrics.
- **LiteLLM Cost Attribution** for model, key, token, and request-level cost.
- **LiteLLM Admin Overview** for users, usable keys, teams, budgets, spend by
  user/team, provider errors, and audit changes. Its database selector switches
  between production and development.
- **LiteLLM Team Costing** for per-team spend, requests, tokens, models, member
  attribution, and expensive requests. Select any current team; new teams appear
  automatically.
- **vLLM Semantic Router** for router and Envoy target health, inference
  outcomes, decision rate, model-routing latency, downstream request rate, and
  inference error ratio.

Prometheus scrapes the router directly on internal port `9190` and Envoy through
the metrics-only listener on internal port `9901`. The Envoy listener proxies
only `/stats/prometheus`; all other paths return `404`, and the full Envoy admin
API remains loopback-only. Grafana provisions alerts when the development
LiteLLM proxy is active and either router target is down, when routed inference
errors exceed 5% for 10 minutes, or when p95 model-selection latency exceeds
500 ms for 10 minutes. Envoy also limits each routed ingress instance to a
sustained 1,000 requests per second with a 2,000-request burst before semantic
classification, and Grafana alerts when that protection remains active for five
minutes. Configure a Grafana contact point and notification policy for these
alerts; repository configuration does not contain notification credentials.

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

Grafana sign-up and anonymous access remain disabled. The initial Grafana
administrator can grant narrowly scoped viewer access later, but the admin
dashboard should remain restricted because it exposes operational identities,
budgets, key aliases, and audit activity.

Use each observability store for its intended telemetry:

- **Loki** contains raw container log lines collected by Grafana Alloy.
- **Langfuse** contains content-redacted LiteLLM request traces.
- **Prometheus** contains time-series service and infrastructure metrics.
- **LiteLLM PG (prod)** and **LiteLLM PG (dev)** query structured LiteLLM
  database telemetry, such as spend records; they are not log stores.

## 10. Use SearXNG search through LiteLLM

Both LiteLLM profiles expose the internal SearXNG service as the native
`searxng-search` search tool. SearXNG stays on the internal Compose network for
gateway requests; clients authenticate to LiteLLM rather than calling the
internal service directly.

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

The development profile also runs vLLM Semantic Router on CPU with Envoy as its
inference listener. Requests sent to `VLLM_SR_HOST` pass through Envoy and the
router before reaching the internal LiteLLM proxy. The router selects either
the `semantic-fast` or `semantic-reasoning` LiteLLM alias; both currently map to
the approved `gpt-4o-mini` deployment until additional completion models are
reviewed. BGE-M3 remains a separate pooling service and embeddings do not pass
through the chat router.

Before Envoy buffers a request body or invokes the semantic router, it validates
the bearer credential against LiteLLM's internal `/v1/models` endpoint. Missing
or invalid credentials are rejected without semantic classification, and an
unavailable authentication check fails closed with `503`. LiteLLM remains the
authoritative authentication boundary and validates the credential again when
it receives the routed request.

```sh
docker compose --profile dev up -d
```

Call the routed hostname with the same LiteLLM bearer credential:

```sh
curl --fail --silent https://llm-router.example.test/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "vllm-sr/auto",
    "messages": [{
      "role": "user",
      "content": "Analyze this architecture and compare its tradeoffs."
    }],
    "temperature": 0
  }'
```

The router uses an explicit priority rule: coding, analysis, architecture,
comparison, and multi-question prompts select `semantic-reasoning`; unmatched
requests use `semantic-fast`. Envoy fails closed when its external processor is
unavailable and strips client-supplied internal routing headers before
forwarding. LiteLLM remains the authentication, policy, guardrail, provider,
spend, and observability boundary.

This integration is development-only. The pinned router image is a
post-v0.4.0 build containing the buffered LiteLLM response compatibility fix;
the current upstream integration is not yet a formal LiteLLM product
commitment. Avoid list-valued request metadata until upstream semantic-router
metadata compatibility is resolved.

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
- Loki container logs are retained for 7 days.
- Prometheus metrics are retained for 15 days.
- Traefik omits `ClientHost` from access logs to avoid persisting client IP
  addresses in Loki.
- LiteLLM audit logging is enabled for supported administrative changes.
- Langfuse trace metadata is stored in its dedicated PostgreSQL, ClickHouse,
  Redis, and MinIO volumes. Define and operate an explicit Langfuse retention
  and backup policy before production use; the database-only backup scripts in
  this repository do not constitute a complete Langfuse backup.

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
including PostgreSQL, ClickHouse, Redis, MinIO, Loki, Presidio, and SearXNG
traffic, is not encrypted. Treat the isolated Docker networks as a compensating
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
  procedures across PostgreSQL, Loki, provider systems, and backups;
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

The stack deploys a separate PostgreSQL 16 database with pgvector 0.8.1. Its
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
`bge-m3-vllm`, `vllm-semantic-router`, and
`vllm-semantic-router-envoy` whenever the development profile is active, without
reporting intentionally inactive profile services as unknown. Restrict a manual
scan only when a narrower reviewed scope is required:

```bash
./scripts/maintenance.py security-scan \
  --services litellm-dev,bge-m3-vllm,vllm-semantic-router,vllm-semantic-router-envoy
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

### Prometheus and Grafana alerts

The Compose configuration enables node-exporter's official textfile collector
and mounts `security-metrics/` read-only. Grafana provisions the rules under
`grafana/alerting/`. Recreate these two services after deploying this change:

```bash
docker compose up -d --force-recreate node-exporter grafana
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

The repository deliberately does not commit notification credentials. Configure
an existing Grafana contact point and notification policy in the Grafana UI, or
provision one using an environment-sourced secret. A firing rule without a
routed contact point is visible in Grafana but cannot notify an operator.
Grafana reads file-provisioned alerting resources at startup.

Traefik and Alloy reach Docker metadata through dedicated read-only socket
proxies with Docker API `POST` disabled. They do not mount the Docker socket
directly. Keep those proxies on their isolated internal networks and do not
enable additional API sections unless the consumer demonstrably requires them.

The same maintenance workflow verifies the router after upgrades. With the
development profile running, the semantic-routing and observability checks fail
if BGE-M3, the router, Envoy, either Prometheus target, or the dedicated Grafana
dashboard is missing or unhealthy:

```bash
./scripts/maintenance.py upgrade-verify \
  --only dev-vllm-embedding,semantic-routing,observability
```

### Run the scheduler inside the stack

The optional `security` Compose profile runs the same scanner and policy inside
the stack. It executes once when started and then daily at 02:45 in the host's
timezone. Set the deployment account's Linux UID and GID in `.env` so the
non-root monitor can write the three bind-mounted runtime directories:

```bash
IMAGE_SECURITY_UID=1000
IMAGE_SECURITY_GID=1000
```

Use `id -u` and `id -g` on the Linux deployment host rather than assuming those
example values. Start only the security services:

```bash
docker compose --profile security up -d --build image-security-monitor
docker compose logs -f image-security-monitor
```

Change the daily time or disable the immediate startup scan with
`IMAGE_SECURITY_SCHEDULE_HOUR`, `IMAGE_SECURITY_SCHEDULE_MINUTE`, and
`IMAGE_SECURITY_RUN_ON_START` in `.env`. `COMPOSE_PROJECT_NAME` must match the
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
docker compose --profile security rm -sf image-security-monitor image-security-docker-proxy
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
Grafana, Loki, and Prometheus volume files. Keep secrets in their existing
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
`litellm-prod`, `litellm-dev`, `litellm-pgvector`, Grafana, or the pgvector
exporter is running. It rejects symbolic links, unexpected files, unknown
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
./scripts/game-day-preflight.py --env-file .env
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
Grafana datasources, and Loki log delivery. A
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
PostgreSQL, Grafana, and Prometheus data. That operation is destructive.

## Security notes

- Keep `.env` and everything under `traefik/certs/` private.
- Rotate any credential that is accidentally exposed.
- The Docker socket mounted by Traefik is security-sensitive; restrict host
  access.
- Do not expose the `dev` profile or its logs to untrusted users.
- Keep image digests immutable in deployed revisions and review updates rather
  than returning to floating tags.
