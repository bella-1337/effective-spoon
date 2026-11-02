# EU technical-control baseline

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

Commands that use hostname variables such as `$LITELLM_PROD_HOST` expect them
to be loaded first with `eval "$(python3 scripts/maintenance.py stack-env)"`;
see [Public hostnames and `STACK_DOMAIN`](getting-started.md#public-hostnames-and-stack_domain).

The stack includes a repository-owned technical baseline informed by:

- [GDPR Articles 5, 25, 30, 32, 33, 35, and 44](https://eur-lex.europa.eu/eli/reg/2016/679/oj)
- [EU AI Act Articles 4, 12, 14, 26, and 50](https://eur-lex.europa.eu/eli/reg/2024/1689/oj)
- [EDPB Opinion 28/2024 on AI models](https://www.edpb.europa.eu/documents/opinion-of-the-board-art-64/opinion-282024-on-certain-data-protection-aspects-related-to_en)
- [ENISA's multilayer framework for AI cybersecurity](https://www.enisa.europa.eu/publications/multilayer-framework-for-good-cybersecurity-practices-for-ai)

This is a technical-control baseline, not legal advice, certification, or a claim
that a deployment complies with the GDPR or EU AI Act.

## Modular configuration

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
curl --fail --silent "https://$LITELLM_PROD_HOST/policies/list" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY"

curl --fail --silent "https://$LITELLM_PROD_HOST/policies/resolve" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini"}'
```

The resolved policy must include `presidio-pii`. Repeat the check on the
development hostname after policy changes.

## Data minimisation and retention

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

## Integrity and transport

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

Two links leave the stack host for the remote GPU host. D3-mini uses verified
TLS. The development-only BGE-M3 endpoint uses plain HTTP with an API key by
operator decision, so dev request text sent for embedding crosses that network
unencrypted; see
[Remote BGE-M3 embeddings](search-extraction-and-caching.md#plain-http-accepted-risk)
for the compensating controls and the requirement to switch to TLS before
production use.

## Required deployment decisions outside this repository

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
