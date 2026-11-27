<!-- human-version-of: docs/observability.md sha256: 3ee229538603891d2130591d123704c576a46ae9a884b584aa55f7f7c8170976 -->

# Observability

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

This page explains where the stack's logs, metrics, and traces go, which UI
shows them, and who may see what. Read it when you look for telemetry, give
someone access to dashboards, set up alert notifications, or enable the
optional Langfuse, Lens, or Grafana profiles.

Commands that use hostname variables such as `$LITELLM_PROD_HOST` expect them
to be loaded first with `eval "$(python3 scripts/maintenance.py stack-env)"`;
see [Public hostnames and `STACK_DOMAIN`](getting-started.md#public-hostnames-and-stack_domain).

At a glance:

- OpenSearch is the default trace and log backend.
- LiteLLM sends standards-based OpenTelemetry spans to Data Prepper.
- Alloy sends stdout/stderr from every Compose service through the same
  default platform.
- The optional `grafana` profile also sends logs to Loki.

## Optional Langfuse profile

With the optional `langfuse` profile, LiteLLM also loads its
`langfuse_otel` integration.

### Sign in

1. Open `https://$LANGFUSE_WEB_HOST`.
2. Sign in with the initial administrator account.
3. Select the pre-provisioned **LiteLLM** project.

Public sign-up is disabled (`AUTH_DISABLE_SIGNUP=true`), so only the initial
administrator account can sign in. Keep its credentials in the secrets manager.
To add operators or recover access, follow the
[Langfuse operator account runbook](environment-variables.md#langfuse-operator-accounts-and-lockout-recovery).

### What traces contain

Traces include model, provider, token, cost, timing, and error metadata.
Prompt and response content is deliberately excluded by both
`turn_off_message_logging: true` and
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=no_content`.

!!! warning
    Do not enable the legacy `langfuse` callback alongside `langfuse_otel`:
    doing so duplicates traces.

### How users and sessions are pseudonymized

When enabled, the callback is global in both LiteLLM profiles. Requests made by
every authenticated LiteLLM user or virtual key are traced to the administrator
project.

The trusted pre-call hook:

- replaces any caller-supplied `trace_user_id` with a stable HMAC pseudonym
  derived from the authenticated LiteLLM user ID (or the authenticated key
  identity when no user owns the key);
- strips caller-supplied `langfuse_*` headers;
- pseudonymizes a supplied `user` field; and
- maps the common agent identifiers `session_id`, `conversation_id`,
  `thread_id`, and `run_id` from request metadata to Langfuse `session.id`.

LiteLLM's standard `x-litellm-session-id`, `x-session-id`, and
`x-<toolkit>-session-id` headers are also supported.

Session identifiers are validated, removed from exported metadata, and replaced
with stable principal-scoped HMAC identifiers. That stops two authenticated
users or keys from merging traces by supplying the same identifier.

### Group multi-turn conversations

For reliable multi-turn grouping, send one stable identifier on every request
in the logical conversation. The portable OpenAI-compatible form is:

```python
client.chat.completions.create(
    model="your-model",
    messages=[{"role": "user", "content": "Hello"}],
    extra_body={"metadata": {"session_id": conversation_id}},
)
```

When a request has no session identifier:

1. If a toolkit supplies none, the hook uses its propagated OTel trace ID.
2. If neither exists, the hook creates a request-scoped fallback, so the trace
   is still visible in Langfuse Sessions.

A request-scoped fallback cannot infer or merge later turns. Clients that need
multi-turn sessions must send a stable identifier, or propagate one OTel trace
across those calls.

### What stays out of Langfuse

- The OTel v2 baggage allowlists are empty, so raw user IDs, key identities,
  and client IP metadata are not copied to child spans.
- FastAPI server spans are excluded, so request URLs, query strings, user
  agents, or peer IPs do not reach Langfuse.

Team ID and alias remain on administrator-only generation traces for team
analytics.

Rotate `LANGFUSE_USER_ID_SALT` only when you intend to break historical user
correlation.

### No per-user access in Langfuse

Per-LiteLLM-user Langfuse UI access is **not available in this OSS
deployment**:

- Langfuse does not treat `trace_user_id` as an authorization boundary, so a
  project viewer can remove a user filter and see every trace in that project.
- LiteLLM and Langfuse accounts are not synchronized.
- Project-level membership is not available on the installed plan.
- Supported automated provisioning through Langfuse organization/SCIM APIs
  requires Enterprise.

Keep the shared Langfuse project restricted to administrators and operators. Do
not present a saved user filter as access isolation.

### Startup and outages

The production LiteLLM proxy does not require Xberg or an inactive Langfuse
profile to start. When Langfuse is explicitly enabled, LiteLLM waits for its
web service before it keeps the optional callbacks in the runtime config.

Later Langfuse outages do not block model requests. Their failures stay visible
through service health, callback logs, and the default OpenSearch observability
checks.

## Optional LiteLLM Lens profile

The optional `lens` profile adds [LiteLLM Lens](lens.md), which reviews the
traces that your *agents* send it and groups recurring failures into findings.

Lens is not part of the gateway telemetry pipeline:

- Agents upload their own OTLP traces to `https://$LENS_HOST/v1/traces`.
- The gateways only export content-free spend records for cost matching.

Administrators open it from the LiteLLM dashboard under **Lens**. It is
monitored like the other services:

- Lens logs reach OpenSearch like every other service
  (`log.attributes.compose_service:"lens"`).
- Prometheus probes its readiness as job `lens`.
- The **LiteLLM Lens is not ready** alert fires when a running Lens stays
  unready for five minutes.

## Coding router and on-host D3-mini

`coding-router` decisions reach OpenSearch on the gateways' request spans as
`litellm.routing.*` attributes:

- the tier;
- the deciding mechanism (`cause`);
- the classifier model;
- its confidence;
- any failure reason; and
- the routed model.

With the optional [`d3` profile](d3-mini-on-host.md#observe):

- Prometheus also scrapes D3-mini's vLLM metrics (job `d3-mini`);
- vLLM sends one span per decision to Data Prepper; and
- the **On-host D3-mini is unavailable** alert watches the scrape.

The **Coding Router and D3-mini** dashboard combines them.

## OpenSearch: the default multi-user observability platform

OpenSearch replaces Grafana as the default UI, because Grafana OSS cannot
restrict data sources or dashboards per user.

The OpenSearch security plugin provides, without a commercial license:

- internal users;
- role-based index and document/field-level security;
- read-only users;
- Global/Private tenants; and
- an audit log.

Every OpenSearch transport and HTTP connection uses TLS with a private CA
created by `opensearch-certs`. Only Traefik exposes OpenSearch Dashboards.

### Components

| Component | Role |
| --- | --- |
| `opensearch` | Single-node store with security, alerting, ISM, SQL/PPL, observability, and notifications plugins |
| `opensearch-dashboards` | Multi-user UI at `https://${OPENSEARCH_DASHBOARDS_HOST}` |
| `alloy` → `data-prepper` | Container stdout/stderr over OTLP into `litellm-logs-*`, with multi-line entries joined, a normalized severity, ANSI colours stripped, search terms redacted, and Traefik access logs parsed into `http.*` fields |
| LiteLLM → `data-prepper` | Content-free OpenTelemetry request, guardrail, model, and database spans into Trace Analytics (`otel-v1-apm-span-*`, `otel-v1-apm-service-map`) |
| `opensearch-bridge` | Prometheus expressions and raw series into `litellm-metrics-*`, alert-rule state into `litellm-alert-state-*`, non-content LiteLLM spend, provider-error, audit, user, team, and key data into `litellm-spend-*`, `litellm-provider-errors-*`, `litellm-audit-*`, and `litellm-entities` |
| `opensearch-setup` | Idempotent provisioning of users, roles, templates, lifecycle policies, the Prometheus direct-query data source, and alert monitors |
| `opensearch-dashboards-setup` | Imports index patterns, visualizations, saved searches, dashboards, and troubleshooting notebooks into the Global tenant |

??? note "Log format of the OpenSearch tools"
    `opensearch-setup`, `opensearch-dashboards-setup`, and `opensearch-bridge`
    log one JSON object per line (`level`, `logger`, `message`, `time`, and
    `exc_info` for tracebacks) through python-json-logger with orjson. A
    traceback therefore stays in the same log entry as its error, and the level
    comes first so the collectors' severity detection reads it.

    To change verbosity, set `OPENSEARCH_TOOLS_LOG_LEVEL` (default `INFO`) in
    the service's environment, for example in a
    `docker-compose.override.yml`.

### Dashboards

Dashboards in the Global tenant (start at **LiteLLM Overview**):

| Dashboard | What it shows |
| --- | --- |
| **LiteLLM Management Summary** | Executive usage, model spend, reliability, user/team/key adoption, budget coverage, unassigned spend, administrative changes, provider errors, and a directional ROI indication. Request-level, distinct-session, realization, hourly-cost, and currency assumptions come from `.env`; raw session identifiers are pseudonymized before OpenSearch. |
| **LiteLLM Stack (dev/prod)** | Every panel of the Grafana stack dashboard plus tokens, provider latency, time to first token, proxy overhead, proxy status codes and exceptions, deployment state, guardrail errors, cache hit ratio, and in-flight requests from the upstream LiteLLM dashboard. |
| **LiteLLM Cost Attribution**, **LiteLLM Admin Overview**, and **LiteLLM Team Costing** | Every panel of the matching Grafana SQL dashboards, with environment, model, and team filters. |
| **Container Logs** | Log volume by service and severity, error, warning, and stderr lines per Compose service, plus searchable log tables. |
| **Traefik Access Logs** | Every HTTPS request with router, status, method, path, and latency percentiles; error responses and slowest requests. |
| **LiteLLM Request Traces** (operators) | Requests by route, span latency by operation, model calls, guardrail outcomes, database latency by table, error spans, and the slowest spans. **Observability → Trace Analytics** adds trace waterfalls and the service map. |
| **Coding Router and D3-mini** (operators) | `coding-router` decisions by tier and deciding mechanism, D3-mini answers versus heuristic fallbacks and their reasons, the models chosen, and, with the `d3` profile, D3-mini's request rate by HTTP status, latency, queue, load, and span timings. |
| **Infrastructure: Host & Containers**, **Infrastructure: PostgreSQL**, and **Infrastructure: Traefik, Probes & Targets** | The core Node Exporter Full, cAdvisor, PostgreSQL, Traefik, blackbox, TLS-expiry, and scrape-target panels. |
| **Security, Backups & Alerts** | Image-security metrics, backup age, and the latest state of every alert rule. |
| **Metric Explorer** | Any mirrored Prometheus metric by name and series. |
| **OpenSearch Security Audit** | Failed logins, privilege denials, and security configuration changes. |

### Troubleshooting notebooks

Troubleshooting notebooks under **Observability → Notebooks** turn the
repository's tested verification and recovery paths into a top-to-bottom,
user-symptom workflow:

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

Each notebook:

1. starts with the user-visible symptom;
2. narrows the failing layer with live PPL; and
3. ends with the smallest matching `verify-litellm-upgrade.py` or recovery
   command.

PPL cells use OpenSearch's native `QUERY` paragraph schema. They execute with
the signed-in user's read permissions when the notebook opens, so running them
does not require granting shared-dashboard write access.

Index authorization still applies, although the shared notebook itself is
visible in the Global tenant:

- the first-response notebook needs `litellm_ops_viewer` or broader;
- cost/recovery checks need `litellm_viewer` or `litellm_operator`; and
- trace and security-audit queries remain operator-only.

### Other OpenSearch Dashboards tools

Beyond the dashboards, OpenSearch Dashboards provides:

- **Discover** over every index pattern;
- **Observability → Logs** (PPL, for example
  ``source = litellm-logs-* | where `log.attributes.stream` = 'stderr'``);
- **Observability → Metrics** and PPL over every Prometheus series through the
  registered `prometheus` data source (for example
  `source = prometheus.query_range('rate(litellm_requests_metric_total[5m])', now() - 1h, now(), '1m')`);
- **Query Workbench**;
- **Notebooks**;
- **Reporting**;
- **Anomaly Detection**; and
- **Alerting**.

The PromQL expressions and raw-series selectors stored in OpenSearch are
defined in `opensearch/tools/metrics.yml`.

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

Every human user also needs the built-in `kibana_user` role. For alert
visibility or management, add the built-in `alerting_read_access` or
`alerting_full_access` role.

For example, a read-only team lead:

```sh
curl --cacert ca.pem -u "admin:${OPENSEARCH_ADMIN_PASSWORD}" -X PUT \
  https://opensearch:9200/_plugins/_security/api/internalusers/alice \
  -H 'Content-Type: application/json' \
  -d '{"password":"<16+ chars>","opendistro_security_roles":["kibana_user","litellm_viewer","litellm_dashboards_read_only"]}'
```

The same call is easier through **Security → Internal users** in the UI.

Further access rules:

- To limit someone to one team's costs, create a role with document-level
  security, for example index pattern `litellm-spend-*` with the DLS query
  `{"term": {"team_id": "demo-team-atlas"}}`.
- The Admin Overview dashboard exposes identities, budgets, key aliases, and
  audit activity. Grant it only to operators.
- Private tenants let each user build personal dashboards without affecting
  the shared Global tenant.

### Alerting

`opensearch-setup` translates every rule in `grafana/alerting/` into an
OpenSearch Alerting monitor named `[LiteLLM] <rule title>`, so the Grafana
files remain the single source of truth.

- A monitor fires only after its rule's condition held for the full Grafana
  `for` duration, honoring the rule's `noDataState` and `execErrState`.
- Four extra monitors fire when alert-rule evaluation, Prometheus metrics,
  container logs, or LiteLLM trace spans stop reaching OpenSearch.

#### Send alert notifications

The repository contains no notification credentials. To get notified:

1. Create a channel under **Notifications → Channels** (Slack, Microsoft Teams,
   Chime, email, SNS, or a webhook).
2. Copy its ID into `OPENSEARCH_ALERT_CHANNEL_ID`.
3. Re-run `docker compose up -d opensearch-setup`. This attaches the channel to
   every monitor with a one-hour throttle.

Without a channel, alerts are visible under **Alerting** but notify no one.

### Rotation and recovery

`opensearch-setup` re-applies the service passwords from `.env` on every run,
using the private admin certificate. To rotate a service password, update
`.env` and run:

```sh
docker compose up -d --force-recreate opensearch-setup
docker compose up -d --force-recreate opensearch-dashboards data-prepper opensearch-bridge
```

Users created in the UI are not overwritten.

`opensearch-certs` renews node and admin certificates 30 days before expiry
whenever it runs.

OpenSearch volumes are not part of the database backup. If the observability
history itself must be recoverable, use OpenSearch snapshots. The LiteLLM usage
indices can always be rebuilt from PostgreSQL: delete `litellm-bridge-state`
and restart `opensearch-bridge`.

## Optional Grafana profile

With `--profile grafana`, the `alloy-loki` collector also sends container logs
to Loki, and Grafana provisions its original dashboards, data sources, and
alert rules.

### View logs in Grafana

1. Open Grafana and select **Explore**.
2. Select the **Loki** data source.
3. Open the label browser, choose a label and value to identify a container,
   and run the query. You can add more labels to narrow the stream.

Grafana Alloy provides `compose_project`, `compose_service`, `container`, and
`stream` (`stdout` or `stderr`) labels, plus `job="docker"`. The OpenSearch log
documents carry the same values under `log.attributes.*`.

For example, run either of these Loki label selectors to show all discovered
container streams or one Compose service:

```logql
{compose_service=~".+"}
{compose_service="litellm-prod"}
```

Append a line filter when useful, for example
`{compose_service="litellm-prod"} |= "error"`.

Label values reflect the currently running profile and services. Use the label
browser rather than assuming that every example value is present.

### Grafana dashboards

Grafana provisions five dashboards in the **LiteLLM** folder:

| Dashboard | What it is for |
| --- | --- |
| **LiteLLM Management Summary** | Executive usage, cost, reliability, governance, and directional ROI. It displays both successful-request and distinct-successful-session contributions plus the active `.env` assumptions. |
| **LiteLLM Stack (dev/prod)** | Request, latency, failure, spend, deployment, and guardrail metrics. |
| **LiteLLM Cost Attribution** | Model, key, token, and request-level cost. |
| **LiteLLM Admin Overview** | Users, usable keys, teams, budgets, spend by user/team, provider errors, and audit changes. Its database selector switches between production and development. |
| **LiteLLM Team Costing** | Per-team spend, requests, tokens, models, member attribution, and expensive requests. Select any current team; new teams appear automatically. |

!!! warning "Keep Grafana for operators"
    Grafana sign-up and anonymous access remain disabled. Grafana OSS cannot
    restrict data sources per user, so keep Grafana access limited to
    operators and use OpenSearch Dashboards for everyone else.

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
| LiteLLM Lens (`lens` profile) | Agent traces, investigations, findings, and datasets in the LiteLLM dashboard | Agent-quality review for the teams that instrument their agents; it sees only the traces agents upload, not every gateway request |
| Prometheus | Expression browser and source metrics for usage, reliability, and infrastructure | Diagnostic source of truth rather than a curated management dashboard; use Grafana or OpenSearch for reviewed summaries |
| Loki | Explore container logs through Grafana | Operational drill-down only; it has no independent management dashboard UI |

#### Configure the ROI estimate

The ROI panels are **indications, not booked savings**. Configure these in
`.env`:

- `ROI_MINUTES_SAVED_PER_REQUEST`
- `ROI_MINUTES_SAVED_PER_SESSION`
- `ROI_VALUE_REALIZATION_FACTOR`
- `ROI_LOADED_HOURLY_COST`
- `ROI_CURRENCY`

How the estimate works:

- The formula adds the successful-request and distinct-successful-session
  contributions. Set either minutes value to zero when both represent the same
  saved work, or the estimate will double count it.
- The default session value is zero until a local session-level measurement
  exists.
- The loaded hourly cost and LiteLLM model spend must be expressed in the same
  configured currency.
- The calculation subtracts direct model spend, but does not include platform
  labor, hardware, licensing, change-management, review time, or the cost of
  incorrect output.

Validate both minutes values and realization through sampled local workflows
(particularly for regional labor costs). Compare the result with
finance-approved costs before you use it in a business case.

Changes take effect after you:

1. rerun `postgres-init`;
2. recreate `opensearch-bridge`; and
3. rerun `opensearch-dashboards-setup`.

Grafana reads the applied values from the read-only
`LiteLLM_ManagementROIConfig` table.

## Demonstration data and telemetry stores

D3 classifier and dev BGE-M3 embedding calls flow through LiteLLM. They
therefore use the normal LiteLLM request, failure, latency, spend-log, and
trace surfaces.

!!! note "The external GPU host is not monitored here"
    The external GPU host is intentionally not scraped across the trust
    boundary by this Compose stack. Its owner must monitor GPU memory,
    utilization, temperature, process readiness, and D3's TLS certificate
    expiry.

### Demonstration teams and users

The development profile automatically seeds two persistent demonstration teams
and four users after its database schema is ready. Production seeds the same
data only when the explicit `demo` profile is enabled:

- **Demo Team Atlas**: Demo Alice and Demo Bob
- **Demo Team Orion**: Demo Carol and Demo Dave

The seed also adds blocked, non-authenticating placeholder key records and
twelve content-free spend rows. The admin and team dashboards then render
useful test data without exposing a usable credential.

The seed is idempotent:

- Development refreshes it when that profile starts.
- Production refreshes it only when the `demo` profile is explicitly started.
- Existing seeded rows remain in the persistent database until they are
  intentionally removed or expire.

### Which store holds what

Use each observability store for its intended telemetry:

| Store | Contents |
| --- | --- |
| **OpenSearch** | Container logs, mirrored metrics, alert-rule state, non-content copies of LiteLLM usage data, and the OpenSearch security audit log. |
| **Langfuse** | Content-redacted LiteLLM request traces. |
| **LiteLLM Lens** (optional `lens` profile) | The agent traces that agents upload, with their full content, for `LENS_TRACE_RETENTION_DAYS`. |
| **Prometheus** | Every scraped time series; it remains the metrics source of truth. |
| **LiteLLM PostgreSQL** | It remains the system of record for spend and identity data; OpenSearch holds read-only copies. |
| **Loki** (optional `grafana` profile) | Raw container log lines. |
