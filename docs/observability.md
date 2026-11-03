# Observability

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

Commands that use hostname variables such as `$LITELLM_PROD_HOST` expect them
to be loaded first with `eval "$(python3 scripts/maintenance.py stack-env)"`;
see [Public hostnames and `STACK_DOMAIN`](getting-started.md#public-hostnames-and-stack_domain).

OpenSearch is the default trace and log backend. LiteLLM sends standards-based
OpenTelemetry spans to Data Prepper, and Alloy sends stdout/stderr from every
Compose service through the same default platform. The optional `grafana`
profile also sends logs to Loki.

## Optional Langfuse profile

With the optional `langfuse` profile, LiteLLM additionally loads its
`langfuse_otel` integration. Open `https://$LANGFUSE_WEB_HOST`, sign in with
the initial administrator account, and select the pre-provisioned **LiteLLM**
project. Public sign-up is disabled (`AUTH_DISABLE_SIGNUP=true`), so only the
initial administrator account can sign in. Keep its credentials in the secrets
manager, and follow the
[Langfuse operator account runbook](environment-variables.md#langfuse-operator-accounts-and-lockout-recovery)
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

## OpenSearch: the default multi-user observability platform

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

`opensearch-setup`, `opensearch-dashboards-setup`, and `opensearch-bridge` log
one JSON object per line (`level`, `logger`, `message`, `time`, and `exc_info`
for tracebacks) through python-json-logger with orjson. A traceback therefore
stays in the same log entry as its error, and the level comes first so the
collectors' severity detection reads it. To change verbosity, set
`OPENSEARCH_TOOLS_LOG_LEVEL` (default `INFO`) in the service's environment, for
example in a `docker-compose.override.yml`.

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

### Users, roles, and tenants

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

### Alerting

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

### Rotation and recovery

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

## Optional Grafana profile

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

### Management views across the stack

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

## Demonstration data and telemetry stores

D3 classifier and dev BGE-M3 embedding calls flow through LiteLLM and therefore
use the normal LiteLLM request, failure, latency, spend-log, and trace surfaces.
The external GPU host is intentionally not scraped across the trust boundary by
this Compose stack; its owner must monitor GPU memory, utilization, temperature,
process readiness, and D3's TLS certificate expiry.

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
