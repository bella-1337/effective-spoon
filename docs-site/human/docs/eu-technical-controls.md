<!-- human-version-of: docs/eu-technical-controls.md sha256: a72cb8633ee5f273190571397bed74e55a98a70bdbef13b3e5f998ecdbedc620 -->

# EU technical-control baseline

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

This page lists the technical controls the stack ships for data protection
and AI governance, and the decisions your organisation must still make
outside the repository. Read it when you prepare a deployment for personal
data or review how it handles retention, transport, and model providers.

Commands that use hostname variables such as `$LITELLM_PROD_HOST` expect them
to be loaded first with `eval "$(python3 scripts/maintenance.py stack-env)"`;
see [Public hostnames and `STACK_DOMAIN`](getting-started.md#public-hostnames-and-stack_domain).

The stack includes a repository-owned technical baseline informed by:

- [GDPR Articles 5, 25, 30, 32, 33, 35, and 44](https://eur-lex.europa.eu/eli/reg/2016/679/oj)
- [EU AI Act Articles 4, 12, 14, 26, and 50](https://eur-lex.europa.eu/eli/reg/2024/1689/oj)
- [EDPB Opinion 28/2024 on AI models](https://www.edpb.europa.eu/documents/opinion-of-the-board-art-64/opinion-282024-on-certain-data-protection-aspects-related-to_en)
- [ENISA's multilayer framework for AI cybersecurity](https://www.enisa.europa.eu/publications/multilayer-framework-for-good-cybersecurity-practices-for-ai)

!!! warning

    This is a technical-control baseline, not legal advice, certification, or
    a claim that a deployment complies with the GDPR or EU AI Act.

## Modular configuration

Both profile roots use LiteLLM's native `include` support:

- `litellm/modules/search.yaml` defines the internal SearXNG integration.
- `litellm/modules/guardrails/presidio.yaml` defines bidirectional PII masking.
- `litellm/modules/policies/eu-data-protection.yaml` defines and globally
  attaches the `eu-data-protection` policy.
- `litellm/config.prod.yaml` and `litellm/config.dev.yaml` retain only
  profile-specific models, timeouts, UI behavior, and shared privacy settings.

A policy must be attached to take effect: defining a policy without attaching
it does not apply its guardrails. Check the live configuration with:

```sh
curl --fail --silent "https://$LITELLM_PROD_HOST/policies/list" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY"

curl --fail --silent "https://$LITELLM_PROD_HOST/policies/resolve" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"<configured model>"}'
```

The resolved policy must include `presidio-pii`. After policy changes, repeat
the check on the development hostname.

## EU model providers

Set `DATA_SOVEREIGNTY_REGION=eu` to keep every cloud model inside the EU. The
LiteLLM launcher then wires only the provider models named in `.env`. It
refuses to start if any provider location could process prompts outside EU
member states. The allowed locations are:

- Amazon Bedrock: `eu.` inference profiles, called from a source Region whose
  routing stays inside the EU.
- Azure OpenAI: resources in EU regions, with Data Zone or regional
  deployments.
- Vertex AI: the `eu` multi-region or an EU single region.

No cloud model is configured by default. See
[Data sovereignty and regional models](data-sovereignty.md) for the variables,
allowed locations, and provider-side controls.

## Data minimisation and retention

The stack keeps prompt and response content out of its logs and traces where
it can, and deletes what it stores after fixed periods. The exceptions are
Langfuse application events and LiteLLM Lens traces, described below.

### Retention periods

| Data | Retained for |
| --- | --- |
| LiteLLM spend metadata | 180 days, then deleted by a daily cleanup job |
| LiteLLM health-check rows | 7 days |
| OpenSearch: container logs | 7 days |
| OpenSearch: LiteLLM trace spans, mirrored metrics, and alert-rule state | 15 days |
| OpenSearch: LiteLLM usage copies | 180 days |
| OpenSearch: security audit log | 90 days (monthly indices, so up to one extra month) |
| Loki container logs (optional `grafana` profile) | 7 days |
| Prometheus metrics | 15 days |
| LiteLLM Lens agent traces (optional `lens` profile) | `LENS_TRACE_RETENTION_DAYS` (default 14) |

For the OpenSearch periods, see `OPENSEARCH_*_RETENTION_DAYS`.

The 180 days for LiteLLM spend metadata support the six-month high-risk
deployer logging period in AI Act Article 26(6) when that obligation applies.
This does not by itself establish that a system is high risk or that the
retention period is appropriate for every purpose.

### What the stack logs and traces

- LiteLLM message logging is disabled. Non-content spend metadata remains
  available for accountability and operations.
- Prompt and response bodies are explicitly excluded from spend logs.
- LiteLLM audit logging is enabled for supported administrative changes.
- Langfuse receives LiteLLM OTel traces with prompt and response content
  disabled. Custom request metadata can still contain sensitive values, so
  clients must allowlist or scrub it.
- LiteLLM trace spans carry the same content-free attributes as the Langfuse
  traces (model, route, token counts, cost, guardrail outcome, and the
  pseudonymous user ID). Only `litellm_operator` can read them. Set
  `LITELLM_OTEL_TRACE_EXPORTER=console` only for short debugging sessions: it
  prints spans to stdout and therefore into the container logs.
- Traefik writes JSON access logs without client addresses, ports, user
  names, or request headers, so client IP addresses are not persisted in
  OpenSearch or Loki.
- Alloy redacts `q=`/`query=` URL parameters in every log line, so search
  terms (for example in SearXNG upstream errors) are not persisted.
- `opensearch-bridge` never copies prompts, responses, request metadata,
  client IP addresses, virtual-key hashes, provider exception text, or audit
  before/after values out of PostgreSQL.

### Langfuse and ClickHouse

Langfuse trace metadata is stored in its dedicated PostgreSQL, ClickHouse,
Redis, and MinIO volumes. ClickHouse:

- keeps warning-or-higher server logs continuously, and rotates each file
  channel at 100 MB with three files retained;
- retains query, view, part, scheduler, and structured warning logs for 7
  days, and error logs for 30 days;
- has its high-volume query profiler, internal metric, and internal
  OpenTelemetry tables disabled.

Prometheus scrapes equivalent live capacity and engine counters instead, and
**Infrastructure: Host & Containers** shows them with disk-pressure,
scrape-failure, and log-storm alerts. This preserves always-on incident
evidence without requiring an operator to predict when to enable
diagnostics.

!!! warning "Langfuse application events are kept indefinitely"

    Langfuse application event retention is a separate concern. The
    project-level automatic retention feature is available only in Langfuse
    Enterprise Edition; OSS self-hosted event data is indefinite by default.

    Before production use, define and operate a supported Langfuse
    deletion/export policy and a complete backup policy. The database-only
    backup scripts in this repository do not constitute a complete Langfuse
    backup.

### LiteLLM Lens traces

!!! warning

    LiteLLM Lens (optional `lens` profile) stores the agent traces that agents
    upload, **with their full inputs, outputs, and tool calls**, in its own
    ClickHouse volume for `LENS_TRACE_RETENTION_DAYS` (default 14).

- The gateways export only content-free spend records to it.
- With `LENS_GATEWAY_API_KEY` set, investigations send selected trace content
  to the configured analysis models through the gateway.
- Agent owners must decide what their exporters send.
- Gateway administrators and the Lens administrator token can read every
  trace.

See [LiteLLM Lens data protection](lens.md#data-protection).

### Your retention review

- Record these periods in the deployment's retention schedule and review them
  against its documented purposes.
- Restrict proxy-admin access, because database-backed UI settings can alter
  runtime behavior.
- If another callback, cache, object store, tracing system, or provider
  logging feature is enabled, add that system to the data-flow and retention
  review.

## Integrity and transport

Images are pinned:

- Every registry image is pinned to an immutable digest.
- The development `litellm-pgvector` image is built from the official BerriAI
  repository at a full, signed Git commit.
- The optional `litellm-lens` image is built from the BerriAI Lens repository
  at a full commit of its paired release candidate.

To update one, Renovate or manually review upstream release notes, update the
matching digest or source commit, and re-run the complete validation before
deploying the update.

Transport inside and at the edge of the stack:

- External routes use TLS through Traefik, including Lens trace uploads.
- The LiteLLM and Langfuse containers trust forwarded headers, because they
  are not published directly and only Traefik is expected to reach them on the
  edge network.
- Internal Compose traffic is not encrypted. This covers PostgreSQL,
  ClickHouse, Redis, MinIO, Loki, Presidio, SearXNG, the gateway-to-Lens
  connection (authenticated by `LITELLM_LENS_SERVICE_TOKEN` and signed
  identities), and the Alloy-to-Data Prepper OTLP stream.
- OpenSearch traffic always uses verified TLS.

!!! warning

    Treat the isolated Docker networks as a compensating control for local
    use. Enable authenticated internal TLS before using an untrusted or
    multi-host network.

Two links leave the stack host for the remote GPU host: D3-mini and the
development-only BGE-M3 endpoint. Both use verified TLS with a certificate
from the GPU host's dedicated mkcert CA, which LiteLLM trusts through
`GPU_HOST_CA_BUNDLE`, plus a separate API key each; see the
[GPU host guide](../gpu-host/README.md).

## Required deployment decisions outside this repository

Before operating the stack with personal data, the responsible organisation
must separately:

- [ ] identify controller, processor, provider, and deployer roles;
- [ ] document purposes, lawful basis, data categories, recipients,
      retention, and technical measures in its record of processing;
- [ ] assess whether a DPIA is required and classify every AI Act use case,
      including prohibited and high-risk uses;
- [ ] execute processor agreements and subprocessor controls;
- [ ] establish a valid Chapter V transfer mechanism and transfer impact
      assessment for non-EU recipients, or configure an eligible EU-resident
      provider;
- [ ] implement data-subject access, correction, export, restriction, and
      deletion procedures across PostgreSQL, OpenSearch, Loki, provider
      systems, and backups;
- [ ] define breach detection, documentation, escalation, and the GDPR
      72-hour notification process;
- [ ] appoint trained human oversight with the authority to revoke keys,
      disable models, stop processing, and override model output;
- [ ] inform users when they interact with AI and address provider-side
      marking of AI-generated content where Article 50 applies;
- [ ] test Presidio and policy effectiveness regularly with representative,
      multilingual data. The bundled analyzer supports English only.

With `DATA_SOVEREIGNTY_REGION=eu`, the configured models are restricted to EU
processing (see [EU model providers](#eu-model-providers)). This configuration
alone does not prove EU data residency or sovereignty: AWS, Microsoft, and
Google are US-headquartered processors.

Before sending personal data, for each provider account:

- confirm its region and policy settings;
- confirm its retention, training, and abuse monitoring terms;
- confirm its data processing agreement;
- assess any remaining third-country access risk.
