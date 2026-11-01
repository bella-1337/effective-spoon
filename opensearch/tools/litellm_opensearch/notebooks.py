"""Provisioned OpenSearch troubleshooting notebooks for the LiteLLM stack."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

NOTEBOOK_TYPE = "observability-notebook"
CREATED_AT = "2026-10-28T00:00:00.000Z"
NOTEBOOK_IDS = {
    "first-response": "edf4ccfe-75a8-5dc6-820b-ed87152f2ebb",
    "requests": "31e86378-fcc0-5fba-b14c-f2478eb35a5d",
    "observability": "2d3564e3-b6b1-5ef0-94e8-793961f64045",
    "data-recovery": "ebcadb4e-f576-5f7b-aaa1-517504bc1bed",
    "access": "b39a2a84-0126-5e13-bf70-989111996e12",
}
LEGACY_NOTEBOOK_IDS = (
    "litellm-nb-first-response",
    "litellm-nb-requests",
    "litellm-nb-observability",
    "litellm-nb-data-recovery",
    "litellm-nb-access",
)


@dataclass(frozen=True)
class NotebookSpec:
    object_id: str
    title: str
    paragraphs: tuple[tuple[str, str], ...]


def md(text: str) -> tuple[str, str]:
    return "markdown", text.strip()


def ppl(query: str) -> tuple[str, str]:
    return "ppl", query.strip()


NOTEBOOKS = (
    NotebookSpec(
        NOTEBOOK_IDS["first-response"],
        "01 - Stack First Response",
        (
            md(
                """
# Stack first response

Use this notebook when a user says the UI is unavailable, a request times out, or
the stack generally "does not work." Work from the edge inward and stop at the
first failed layer:

1. Record the affected hostname, approximate UTC time, environment, route, and
   HTTP status. Do not copy prompts, API keys, or authorization headers.
2. Check Traefik access results.
3. Check scrape-target and container health signals.
4. Check normalized service errors and restart signals.
5. Run the repository verifier for the failing layer.

Role: `litellm_ops_viewer`, `litellm_viewer`, or `litellm_operator`.

Dashboards: [Traefik Access Logs](/app/dashboards#/view/litellm-dash-access),
[Host & Containers](/app/dashboards#/view/litellm-dash-host), and
[Container Logs](/app/dashboards#/view/litellm-dash-logs).
"""
            ),
            md("## 1. Did the request reach Traefik?\nRecent non-successful edge requests, newest first:"),
            ppl(
                """
source = litellm-logs-*
| where `http.DownstreamStatus` >= 400
| fields `@timestamp`, `http.RequestHost`, `http.RequestMethod`, `http.RequestPath`,
  `http.DownstreamStatus`, `http.duration_ms`, `http.RouterName`, `http.ServiceName`
| sort - `@timestamp`
| head 100
"""
            ),
            md(
                """
**Interpretation**

- No matching request: verify DNS, TLS trust, the hostname, and the client network.
- 404: inspect the host rule and path.
- 401/403: continue in **05 - Access, Authentication, and Audit**.
- 429: inspect LiteLLM rate limits and budgets.
- 5xx: continue below, then use **02 - LiteLLM Requests and Integrations**.
"""
            ),
            md("## 2. Are Prometheus targets reachable?\nThe newest mirrored target state should be `1`:"),
            ppl(
                """
source = litellm-metrics-*
| where kind = 'expression' and metric = 'target_up'
| sort - `@timestamp`
| dedup metric_series
| fields `@timestamp`, `labels.job`, `labels.instance`, value
| sort `labels.job`, `labels.instance`
"""
            ),
            md("## 3. Which services are producing errors?"),
            ppl(
                """
source = litellm-logs-*
| where severityNumber >= 17
| stats count() as errors by `log.attributes.compose_service`
| sort - errors
"""
            ),
            md("## 4. Did a container restart recently?"),
            ppl(
                """
source = litellm-metrics-*
| where kind = 'expression' and metric = 'container_restarts_1h'
| sort - `@timestamp`
| dedup `labels.service`
| fields `@timestamp`, `labels.service`, value
| sort - value
"""
            ),
            md(
                """
## 5. Confirm from the deployment host

```bash
docker compose ps
./scripts/verify-litellm-upgrade.py --only compose-health,network-exposure,opensearch,opensearch-logs
```

Use `docker compose logs --since 15m <service>` only for the first failing service.
Do not paste rendered Compose configuration or `.env` output into an incident.
"""
            ),
        ),
    ),
    NotebookSpec(
        NOTEBOOK_IDS["requests"],
        "02 - LiteLLM Requests and Integrations",
        (
            md(
                """
# LiteLLM request and integration troubleshooting

Use this notebook when authentication succeeds but a completion, embedding,
semantic route, guardrail, search, Xberg extraction, or pgvector operation fails
or is slow.

Top-down order:

1. Classify the edge status and latency.
2. Compare proxy failures with provider failures.
3. Confirm deployment and guardrail state.
4. Use traces only after narrowing the time and environment.
5. Run the smallest matching verifier category.

Role: `litellm_viewer` for logs, metrics, and request accounting;
`litellm_operator` for the trace paragraph.

Never search for raw prompt or response content. Use request IDs, routes, models,
status classes, and UTC timestamps.
"""
            ),
            md("## 1. Classify failed and slow HTTPS requests"),
            ppl(
                """
source = litellm-logs-*
| where `http.DownstreamStatus` >= 400 or `http.duration_ms` >= 5000
| fields `@timestamp`, `http.RequestHost`, `http.RequestMethod`, `http.RequestPath`,
  `http.DownstreamStatus`, `http.duration_ms`, `http.ServiceName`
| sort - `@timestamp`
| head 100
"""
            ),
            md(
                """
## 2. Check recent request accounting metadata

Provider failures are summarized in the next metric query. The dedicated
`litellm-provider-errors-*` index is created only after the first provider error,
so a healthy deployment may not have that index.
"""
            ),
            ppl(
                """
source = litellm-spend-*
| fields `@timestamp`, environment, request_id, model_name, provider, status
| sort - `@timestamp`
| head 100
"""
            ),
            md("## 3. Check the newest proxy and provider health expressions"),
            ppl(
                """
source = litellm-metrics-*
| where kind = 'expression' and (
    metric = 'litellm_failed_requests_per_second'
    or metric = 'litellm_proxy_failures_per_second'
    or metric = 'litellm_deployment_state'
    or metric = 'litellm_guardrail_errors_per_second'
  )
| sort - `@timestamp`
| dedup metric_series
| fields `@timestamp`, metric, `series`, value
| sort metric, `series`
"""
            ),
            md(
                """
## 4. Inspect request traces (operator role required)

Trace spans can contain cost, team, and pseudonymous user attributes, so only
`litellm_operator` can run this check. A status code of `2` is an OpenTelemetry
error. Pivot on `traceId` in **Observability → Trace Analytics** for the waterfall.
"""
            ),
            ppl(
                """
source = otel-v1-apm-span-*
| where `status.code` = 2 or durationInMillis >= 5000
| fields startTime, serviceName, traceGroup, name, durationInMillis, `status.code`,
  `status.message`, traceId
| sort - startTime
| head 100
"""
            ),
            md(
                """
## 5. Run the matching end-to-end check

```bash
# Core request/authentication or prod/dev separation
./scripts/verify-litellm-upgrade.py --only prod-auth,dev-auth,isolation

# Local embeddings and semantic routing
./scripts/verify-litellm-upgrade.py --only dev-vllm-embedding,coding-routing

# Data and guardrail paths
./scripts/verify-litellm-upgrade.py --only pgvector,presidio

# Search and extraction integrations
./scripts/verify-litellm-upgrade.py --only searxng,xberg-mcp

# Administrator-only tracing UI
./scripts/verify-litellm-upgrade.py --only langfuse
```

If Presidio fails, indexing is intentionally fail-closed. If Xberg background
indexing fails, the original extraction may still succeed; repeat `extract` after
the embedding or vector service recovers.
"""
            ),
        ),
    ),
    NotebookSpec(
        NOTEBOOK_IDS["observability"],
        "03 - Missing Logs, Metrics, Traces, or Alerts",
        (
            md(
                """
# Observability pipeline troubleshooting

Use this notebook when a dashboard is empty, logs are missing, traces disappear,
or an alert is stale. Check the pipeline from producer to storage:

1. Confirm each OpenSearch index family has fresh documents.
2. Confirm every running Compose service has logs.
3. Confirm normalized severity and structured Traefik fields exist.
4. Confirm mirrored metrics and alert evaluation continue.
5. Confirm LiteLLM spans reach Trace Analytics.

An empty error panel is healthy when there were no errors. An entirely stale
index family is an ingestion problem.

Role: `litellm_ops_viewer` for logs, metrics, and alert state;
`litellm_operator` for the trace paragraph.
"""
            ),
            md("## 1. Last document received by each pipeline"),
            ppl(
                """
source = litellm-logs-*
| stats max(`@timestamp`) as last_log
"""
            ),
            ppl(
                """
source = litellm-metrics-*
| stats max(`@timestamp`) as last_metric
"""
            ),
            ppl(
                """
source = litellm-alert-state-*
| stats max(`@timestamp`) as last_alert_evaluation
"""
            ),
            ppl(
                """
source = otel-v1-apm-span-*
| stats max(startTime) as last_trace
"""
            ),
            md("## 2. Which Compose services have log documents?"),
            ppl(
                """
source = litellm-logs-*
| stats count() as events, max(`@timestamp`) as last_seen by `log.attributes.compose_service`
| sort `log.attributes.compose_service`
"""
            ),
            md("## 3. Are severity and Traefik parsing active?"),
            ppl(
                """
source = litellm-logs-*
| eval parsed_access = if(isnull(`http.DownstreamStatus`), 0, 1)
| stats count() as events, sum(parsed_access) as access_events by severityText
| sort - events
"""
            ),
            md("## 4. Are alert rules evaluating or reporting errors?"),
            ppl(
                """
source = litellm-alert-state-*
| sort - `@timestamp`
| dedup rule_uid
| fields `@timestamp`, rule_uid, title, severity, state, condition_met, active_series_count, error
| sort severity, title
"""
            ),
            md("## 5. Are traces arriving by service?"),
            ppl(
                """
source = otel-v1-apm-span-*
| stats count() as spans, max(startTime) as last_seen by serviceName
| sort serviceName
"""
            ),
            md(
                """
## Known pipeline checks from this repository

```bash
./scripts/verify-litellm-upgrade.py --only observability,opensearch,opensearch-logs
docker compose ps alloy data-prepper opensearch-bridge opensearch opensearch-dashboards
docker compose logs --since 15m alloy data-prepper opensearch-bridge
```

- Logs: Alloy sends OTLP to Data Prepper port `21892`.
- Traces: LiteLLM uses `OTEL_EXPORTER=otlp_grpc` and Data Prepper port `21890`.
- Metrics and alert state: `opensearch-bridge` reads Prometheus every configured
  interval.
- Langfuse receives its own trace copy; its health does not prove OpenSearch trace
  ingestion.
- Data Prepper needs data-stream lookup permission; the bridge needs scroll-clear
  permission. Re-run `opensearch-setup` after role changes.
- If only an old daily log index has unexpected mappings, inspect a newly rotated
  index before changing the template.
"""
            ),
        ),
    ),
    NotebookSpec(
        NOTEBOOK_IDS["data-recovery"],
        "04 - Data, Cost, Backup, and Upgrade Recovery",
        (
            md(
                """
# Data, cost, backup, and upgrade recovery

Use this notebook when spend is absent, PostgreSQL-backed features fail, backups
are stale, or an upgrade needs rollback.

1. Establish database exporter connectivity.
2. Check spend/provider-error freshness without querying content.
3. Check backup and scanner state.
4. Run the game-day preflight before any destructive recovery.
5. Restore or roll back only with repository scripts and their confirmation gates.

Role: `litellm_viewer` or `litellm_operator`.
"""
            ),
            md("## 1. PostgreSQL connectivity and deadlock signals"),
            ppl(
                """
source = litellm-metrics-*
| where kind = 'expression' and (
    metric = 'pg_up'
    or metric = 'pg_connections'
    or metric = 'pg_max_connections'
    or metric = 'pg_deadlocks_1h'
  )
| sort - `@timestamp`
| dedup metric_series
| fields `@timestamp`, metric, `series`, value
| sort metric, `series`
"""
            ),
            md("## 2. Is usage data current by environment?"),
            ppl(
                """
source = litellm-spend-*
| stats count() as requests, max(`@timestamp`) as last_seen, sum(spend) as spend by environment
| sort environment
"""
            ),
            md("## 3. Latest backup and image-security state"),
            ppl(
                """
source = litellm-metrics-*
| where kind = 'expression' and (
    metric = 'backup_age_hours'
    or metric = 'image_security_scan_success'
    or metric = 'image_security_scan_age_hours'
    or metric = 'image_security_run_age_hours'
  )
| sort - `@timestamp`
| dedup metric_series
| fields `@timestamp`, metric, `series`, value
| sort metric, `series`
"""
            ),
            md(
                """
## 4. Preflight and backup

```bash
./scripts/maintenance.py game-day-preflight
./scripts/maintenance.py backup
./scripts/maintenance.py upgrade-verify --only pgvector,langfuse,observability,opensearch
```

Treat a partial backup as failed even if some archives were published. Confirm
the checksum manifest and metadata before choosing a recovery point.
"""
            ),
            md(
                """
## 5. Upgrade recovery

```bash
# Before changing the image
./scripts/maintenance.py pre-upgrade --candidate-image "$CANDIDATE_IMAGE" --pull
./scripts/prepare-litellm-upgrade.py

# After deployment
./scripts/verify-litellm-upgrade.py

# Image-only rollback when the old image supports the current schema
./scripts/rollback-litellm-upgrade.py --confirm-rollback --mode image-only

# Database restore only when the upgrade made the schema incompatible
./scripts/rollback-litellm-upgrade.py --confirm-rollback --mode restore-db
```

Rollback refuses configuration drift by default. Database restore stops
application services first and intentionally leaves them stopped if restoration
fails. Do not bypass these safety gates.
"""
            ),
        ),
    ),
    NotebookSpec(
        NOTEBOOK_IDS["access"],
        "05 - Access, Authentication, and Audit",
        (
            md(
                """
# Access, authentication, and audit troubleshooting

Use this notebook for OpenSearch sign-in failures, missing dashboards, denied
queries, or LiteLLM 401/403 responses.

1. Decide whether the denial came from Traefik/LiteLLM or OpenSearch Dashboards.
2. Confirm the user has `kibana_user` plus one least-privilege LiteLLM role.
3. Inspect failed-login and missing-privilege audit events.
4. Grant trace and audit access only to operators.

Role: `litellm_operator`.

Do not solve a permission problem by granting `all_access`.
"""
            ),
            md("## 1. Recent 401 and 403 edge responses"),
            ppl(
                """
source = litellm-logs-*
| where `http.DownstreamStatus` = 401 or `http.DownstreamStatus` = 403
| fields `@timestamp`, `http.RequestHost`, `http.RequestMethod`, `http.RequestPath`,
  `http.DownstreamStatus`, `http.RouterName`, `http.ServiceName`
| sort - `@timestamp`
| head 100
"""
            ),
            md(
                """
## 2. OpenSearch failed logins (operator role required)

Audit indices expose usernames, remote addresses, and requested privileges.
Restrict this notebook query to incident responders.
"""
            ),
            ppl(
                """
source = security-auditlog-*
| where audit_category = 'FAILED_LOGIN'
| fields `@timestamp`, audit_request_effective_user, audit_request_remote_address,
  audit_rest_request_path
| sort - `@timestamp`
| head 100
"""
            ),
            md("## 3. Missing OpenSearch privileges (operator role required)"),
            ppl(
                """
source = security-auditlog-*
| where audit_category = 'MISSING_PRIVILEGES'
| fields `@timestamp`, audit_request_effective_user, audit_request_privilege,
  audit_rest_request_path
| sort - `@timestamp`
| head 100
"""
            ),
            md(
                """
## 4. Role checklist

- Every human Dashboards user: built-in `kibana_user`.
- Operations without cost/identity data: `litellm_ops_viewer`.
- Cost and entity data only: `litellm_cost_viewer`.
- Logs, metrics, cost, and entities: `litellm_viewer`.
- Traces, security audit, LiteLLM audit, and shared-object editing:
  `litellm_operator`.
- Read-only UI: `litellm_dashboards_read_only`.
- Alert visibility is separate: `alerting_read_access` or
  `alerting_full_access`.

Private tenants hold personal objects; repository notebooks and dashboards are
provisioned into the Global tenant.
"""
            ),
            md(
                """
## 5. Verify authentication boundaries

```bash
./scripts/verify-litellm-upgrade.py --only network-exposure,prod-auth,dev-auth,isolation,opensearch
```

OpenSearch Dashboards must return `401` to anonymous requests. LiteLLM ownership
and environment isolation checks must continue to fail closed when a dependency
is unavailable.
"""
            ),
        ),
    ),
)


def _paragraph(notebook_id: str, number: int, kind: str, text: str) -> dict[str, Any]:
    input_text = f"%md\n{text}" if kind == "markdown" else f"%ppl\n{text}"
    output: dict[str, str] = {
        "result": text,
        "execution_time": "0 ms",
        "outputType": "MARKDOWN" if kind == "markdown" else "QUERY",
    }
    return {
        "id": f"paragraph_{notebook_id}_{number:02d}",
        "dateCreated": CREATED_AT,
        "dateModified": CREATED_AT,
        "input": {"inputText": input_text, "inputType": "MARKDOWN" if kind == "markdown" else "QUERY"},
        "output": [output],
        "dataSourceMDSId": "",
        "dataSourceMDSLabel": "",
    }


def build_all() -> list[dict[str, Any]]:
    objects = []
    for spec in NOTEBOOKS:
        objects.append(
            {
                "type": NOTEBOOK_TYPE,
                "id": spec.object_id,
                "attributes": {
                    "savedNotebook": {
                        "name": spec.title,
                        "path": spec.title,
                        "dateCreated": CREATED_AT,
                        "dateModified": CREATED_AT,
                        "backend": ".kibana_1.0",
                        "paragraphs": [
                            _paragraph(spec.object_id, number, kind, text)
                            for number, (kind, text) in enumerate(spec.paragraphs, 1)
                        ],
                    }
                },
                "references": [],
            }
        )
    return objects
