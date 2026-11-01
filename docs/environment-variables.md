# Environment variable reference

This document is the operator reference for configuration accepted by the
LiteLLM stack. Copy `.env.example` to `.env` for the core stack. When enabling
Grafana, Langfuse, or the image-security monitor, also copy
`.env.optional.example` to `.env.optional`. Replace every required placeholder
and review every default before deployment.

## Conventions

| Term | Meaning |
| --- | --- |
| Required | Compose rejects the configuration when the value is absent. |
| Optional | The stack uses the documented default when the value is absent. |
| Secret | Confidential value. Store it in a secrets manager for production and never commit it. |
| Sensitive | Operationally sensitive value that is not an authentication secret. Restrict write access. |
| Recreate | Run `docker compose up -d --force-recreate` for affected services after changing the value. |

Values in `.env` and `.env.optional` are strings. Boolean values use `true` or
`false` unless a component explicitly requires the documented spelling. Byte
sizes use Docker/Prometheus units such as `512m`, `2g`, or `10GB`. Durations use
units such as `30s`, `15m`, or `15d`.

## Stack identity, routing, and shared defaults

| Variable | Requirement / default | Format | Description and guidance |
| --- | --- | --- | --- |
| `COMPOSE_PROJECT_NAME` | Optional: `litellm-stack` | Docker Compose project name | Prefixes containers, networks, and volumes and is used by image-security inventory. Changing it creates a logically separate stack and new named volumes. Use lowercase letters, digits, hyphens, and underscores. |
| `STACK_TIMEZONE` | Optional: `Europe/Berlin` | IANA timezone | Central European local time with automatic CET/CEST daylight-saving transitions. Used by the image-security scheduler. Keep an IANA region name rather than fixed `CET`, which cannot represent all civil-time transitions safely. Langfuse infrastructure remains on its required UTC default. |
| `LOG_MAX_SIZE` | Optional: `10m` | Docker size | Maximum size of one container JSON log file before rotation. Lower values reduce disk spikes; higher values retain more local troubleshooting context. |
| `LOG_MAX_FILE` | Optional: `3` | Positive integer | Number of rotated JSON log files retained per container. Total upper bound is approximately `LOG_MAX_SIZE * LOG_MAX_FILE` per container. |
| `DOCKER_SOCKET_PROXY_LOG_LEVEL` | Optional: `warning` | `debug`, `info`, `warning`, `error`, or `critical` | Shared log level for the restricted Docker socket proxies. Avoid `debug` in routine production because it increases noise and may reveal operational metadata. |
| `TRAEFIK_TLS_CERT_FILE` | Optional: `./traefik/certs/litellm-stack.pem` | Host file path | Certificate chain delivered to the on-prem host. The file is bind-mounted read-only as `/certs/tls.crt`. Use an absolute path when the certificate delivery agent writes outside the repository. Include intermediates in server-first order when required by the issuing CA. |
| `TRAEFIK_TLS_KEY_FILE` | Optional: `./traefik/certs/litellm-stack-key.pem` | Host file path; secret | Private key matching the delivered certificate. Restrict host permissions to the deployment account and Docker daemon. Never commit or copy it into images. The file is bind-mounted read-only as `/certs/tls.key`. |
| `STACK_DOMAIN` | Required; example: `example.test` | DNS domain | Base domain of the deployment. Every public `*_HOST` example value is `<service>.${STACK_DOMAIN}`, so a new environment normally changes only this value. Docker Compose and the maintenance scripts expand the reference identically; Compose refuses to render without it. Use a domain you control and can issue certificates for; `.test` is reserved for local testing. Print every resolved hostname with `python3 scripts/maintenance.py stack-env`. |
| `LITELLM_PROD_HOST` | Required; example: `llm.${STACK_DOMAIN}` | DNS hostname or `${STACK_DOMAIN}` reference | Public Traefik hostname and advertised base URL for the production-profile LiteLLM proxy. It must be covered by the TLS certificate. Do not include a scheme or path. |
| `LITELLM_DEV_HOST` | Required; example: `llm-dev.${STACK_DOMAIN}` | DNS hostname or `${STACK_DOMAIN}` reference | Public Traefik hostname and advertised base URL for the development-profile LiteLLM proxy. It must be covered by the TLS certificate. |
| `OPENSEARCH_DASHBOARDS_HOST` | Required; example: `opensearch.${STACK_DOMAIN}` | DNS hostname or `${STACK_DOMAIN}` reference | Public Traefik hostname of OpenSearch Dashboards, the default multi-user observability UI. It must be covered by the TLS certificate. Changing it invalidates browser sessions. |
| `GRAFANA_HOST` | Optional: `grafana.${STACK_DOMAIN}` | DNS hostname or `${STACK_DOMAIN}` reference | Used only by the optional `grafana` profile: public Traefik hostname and Grafana root URL. Changing it can invalidate generated links and browser sessions. |
| `LANGFUSE_WEB_HOST` | Optional: `langfuse.${STACK_DOMAIN}` | DNS hostname or `${STACK_DOMAIN}` reference | Used only by the optional `langfuse` profile: public Traefik hostname for Langfuse web/API routing and `NEXTAUTH_URL`. |
| `LANGFUSE_S3_HOST` | Optional: `langfuse-s3.${STACK_DOMAIN}` | DNS hostname or `${STACK_DOMAIN}` reference | Used only by the optional `langfuse` profile for signed media upload URLs. It must resolve to this stack and be covered by the TLS certificate. |

Public hostnames have no hard-coded fallback in the Compose files or scripts.
Keep the `${STACK_DOMAIN}` reference to follow the naming convention, or replace
an individual value with a full hostname (no scheme, port, or path) when a
service needs a name outside that domain. The optional-profile hostnames are
read from `.env.optional` and default to the `.env.optional.example` values, so
they also follow `STACK_DOMAIN` unless overridden. `game-day-preflight` fails
when a rendered Traefik `Host()` rule is not a valid DNS hostname, for example
`llm.` from an empty `STACK_DOMAIN`.

Changing a hostname requires recreation of every service that consumes that
hostname, plus coordinated updates to DNS or local host resolution, TLS
certificates, firewall policy, and clients. Traefik watches Docker labels and
discovers recreated routed services automatically.

## Corporate branding

These are LiteLLM's own branding settings, passed through unchanged; the stack
adds no UI customisation of its own. See [`branding/README.md`](../branding/README.md)
for the asset contract. Recreate `litellm-prod` and `litellm-dev` after a change.

| Variable | Requirement / default | Format | Description and guidance |
| --- | --- | --- | --- |
| `BRAND_COMPANY_NAME` | Optional: `Foo-Bar` in the example | Text | Referenced only inside `.env` to compose the text values below; it is not passed to any container. |
| `BRAND_ASSETS_DIR` | Optional: `./branding/foo-bar` | Host directory path | Mounted read-only at `/app/branding` in both LiteLLM containers. Keep only public brand images here. |
| `UI_LOGO_PATH` | Optional: `/app/branding/logo.png` | File under `/app/branding/` or `https://` URL | Admin UI sidebar logo. LiteLLM serves local PNG, JPEG, GIF, WEBP, or ICO files and redirects browsers to URLs; use a URL for SVG. Unsupported files fall back to the LiteLLM logo. |
| `UI_LOGO_PATH_DARK` | Optional: `/app/branding/logo-dark.png` | Same as `UI_LOGO_PATH` | Admin UI logo in dark mode. LiteLLM falls back to `UI_LOGO_PATH`. |
| `LITELLM_FAVICON_URL` | Optional: `/app/branding/favicon.png` | Same as `UI_LOGO_PATH` | Admin UI browser-tab icon. |
| `DOCS_TITLE`, `DOCS_DESCRIPTION` | Optional; LiteLLM Enterprise | Text | Title and Markdown description of the Swagger API docs. Ignored without an Enterprise licence. |
| `EMAIL_LOGO_URL` | Optional; LiteLLM Enterprise | `https://` URL | Logo in invitation and key emails. Use a PNG or JPEG that recipients' mail clients can fetch. |
| `EMAIL_SUPPORT_CONTACT` | Optional; LiteLLM Enterprise | Email address | Support contact shown in emails. |
| `EMAIL_SIGNATURE` | Optional; LiteLLM Enterprise | HTML | Email footer. Unset keeps LiteLLM's footer. |
| `EMAIL_SUBJECT_INVITATION`, `EMAIL_SUBJECT_KEY_CREATED`, `EMAIL_SUBJECT_KEY_ROTATED` | Optional; LiteLLM Enterprise | Text with `{event_message}` | Email subject templates. |

Compose passes the Enterprise variables only when they are present in `.env`.
Delete a line rather than leaving it empty: LiteLLM treats an empty value as
configured and would, for example, show an empty API docs title. The page title,
login heading, colours, and fonts have no LiteLLM setting and stay unchanged.
`game-day-preflight` checks that the logo and favicon values are `https://` URLs
or files in the read-only `/app/branding` mount, and that the example files are
image formats LiteLLM can serve.

## Credentials and cryptographic material

All secret values in this section are required except the Grafana and Langfuse
values, which are required only when their optional profiles are started.
Langfuse infrastructure values live in `.env.optional`; the public key, secret
key, and user-ID salt remain in `.env` because core services consume them.
Preflight checks refuse to start an optional profile while a required value is
empty. `GRAFANA_DATABASE_USER` has a fixed compatibility default. Recreate every
consumer after a rotation. For production, inject values from a secrets manager
rather than persisting them in developer-managed environment files. Write that
output to `.env` and `.env.optional` as private regular files, for example
`install -m 600 /run/secrets/litellm.env .env`. Do not symlink them: the
maintenance tooling (`scripts/maintenance.py`, backup, verify, upgrade, and
rollback) refuses symlinked or non-regular environment files. This prevents a
replaced link from redirecting which secrets are loaded and prevents silently
validating placeholder values.

| Variable | Classification / format | Description and rotation guidance |
| --- | --- | --- |
| `PG_USER` | Sensitive; optional default `litellm`; PostgreSQL identifier | Owner used by both LiteLLM databases. The initialization SQL and existing volumes assume the configured identity. Do not change it after first initialization without a database migration. |
| `PG_PASSWORD` | Secret; URL-safe random string | Password for `PG_USER`. It is embedded in PostgreSQL URLs, so use an unreserved value such as 32 or more random hexadecimal bytes. Rotate in PostgreSQL and all LiteLLM consumers together. |
| `OPENSEARCH_DATABASE_PASSWORD` | Secret; URL-safe random string | Password for the read-only `opensearch_ro` LiteLLM database role used by `opensearch-bridge`. Rotate through `postgres-init` and recreate `opensearch-bridge`. |
| `GRAFANA_DATABASE_USER` | Sensitive; default `grafana_ro`; `grafana` profile only | Read-only role consumed by Grafana datasource provisioning. Keep `grafana_ro`; the initialization SQL creates that fixed least-privilege role. |
| `GRAFANA_DATABASE_PASSWORD` | Secret; URL-safe random string; `grafana` profile only | Password for Grafana's read-only LiteLLM database role. `grafana-db-init` creates or updates `grafana_ro`. Re-run that job and recreate Grafana when rotating the value. |
| `PG_EXPORTER_PASSWORD` | Secret; URL-safe random string | Password for the `litellm_exporter` monitoring role. Rotate through `postgres-init` and recreate `postgres-exporter`. |
| `PG_VECTOR_PASSWORD` | Secret; URL-safe random string | Password for the isolated pgvector owner. Used by migrations, LiteLLM development, and the pgvector connector. Coordinate database-side and consumer changes. |
| `PG_VECTOR_EXPORTER_PASSWORD` | Secret; URL-safe random string | Password for the read-only `vector_exporter` monitoring role. Rotate through `pgvector-access-init` and recreate `pgvector-exporter`. |
| `PG_VECTOR_API_KEY` | Secret; at least 32 random bytes | Shared authentication key between LiteLLM development and the internal pgvector service. Rotate both services together. |
| `QDRANT_API_KEY` | Secret; at least 32 random bytes | API key protecting the development Qdrant service. Rotate Qdrant and LiteLLM development together. |
| `LITELLM_MASTER_KEY` | Secret; `sk-` prefix recommended | Administrative and API authentication key for both LiteLLM profiles. It is also used by the internal pgvector connector. Rotate during a maintenance window and update clients. |
| `OPENAI_API_KEY` | Secret; provider-issued token | Provider credential used by configured models. Scope it to the minimum required project and permissions, set provider-side spend limits, and rotate according to provider policy. |
| `OPENSEARCH_ADMIN_PASSWORD` | Secret; 16+ URL-safe characters | Password of the OpenSearch `admin` user (security all-access) used to sign in to OpenSearch Dashboards and to import saved objects. `opensearch-setup` re-applies it on every start through the admin certificate, so rotating `.env` and re-running `docker compose up -d opensearch-setup opensearch-dashboards-setup` rotates it. |
| `OPENSEARCH_DASHBOARDS_SERVER_PASSWORD` | Secret; 16+ URL-safe characters | Password of the internal `kibanaserver` identity that OpenSearch Dashboards uses for its own system calls. Rotate by re-running `opensearch-setup`, then recreate `opensearch-dashboards`. |
| `OPENSEARCH_DASHBOARDS_COOKIE_PASSWORD` | Secret; 32+ characters | Encrypts OpenSearch Dashboards session cookies. Rotation signs every user out. |
| `OPENSEARCH_INGEST_PASSWORD` | Secret; 16+ characters from `[A-Za-z0-9._~-]` | Password of the `data_prepper` user, which may only write `litellm-logs-*`. Data Prepper rejects other characters. Re-run `opensearch-setup` and recreate `data-prepper` together. |
| `OPENSEARCH_BRIDGE_PASSWORD` | Secret; 16+ URL-safe characters | Password of the `litellm_bridge` user, which may only write the bridge's `litellm-*` indices. Re-run `opensearch-setup` and recreate `opensearch-bridge` together. |
| `OPENSEARCH_DATASOURCES_MASTER_KEY` | Secret; exactly 32 characters | Encrypts credentials of OpenSearch SQL/PPL direct-query data sources (the registered Prometheus source). Keep stable; changing it makes stored data-source credentials unreadable. Generate with `openssl rand -hex 16`. |
| `OPENSEARCH_SESSION_ID_SALT` | Secret; 32+ random bytes | HMAC salt used to pseudonymize session identifiers stored by the default OpenSearch usage bridge. Keep stable to preserve longitudinal grouping; rotation changes all derived identifiers. During migration only, the stack falls back to `LANGFUSE_USER_ID_SALT` when this value is absent. |
| `GRAFANA_ADMIN_PASSWORD` | Secret; strong random password; `grafana` profile only | Initial Grafana administrator password. Grafana persists credentials in its volume, so changing this value does not reset an already initialized administrator; use Grafana's supported reset workflow. |
| `GRAFANA_SECURITY_SECRET_KEY` | Secret; stable 32+ byte value; `grafana` profile only | Encrypts Grafana datasource and alerting credentials. Back it up securely. Changing it can make existing encrypted values unreadable. |
| `SEARXNG_SECRET` | Secret; 32+ random bytes | SearXNG signing secret. Rotation invalidates signed state and existing sessions. |
| `LANGFUSE_POSTGRES_PASSWORD` | Secret; URL-safe random string; `langfuse` profile only | Password for Langfuse PostgreSQL. Rotate the database role and Langfuse web/worker configuration together. |
| `LANGFUSE_CLICKHOUSE_PASSWORD` | Secret; URL-safe random string; `langfuse` profile only | Password for Langfuse ClickHouse trace storage. Rotate ClickHouse and both Langfuse application services together. |
| `LANGFUSE_REDIS_AUTH` | Secret; strong random string; `langfuse` profile only | Password for the Langfuse Redis queue. Rotate Redis and both Langfuse application services together. |
| `LANGFUSE_MINIO_PASSWORD` | Secret; strong random string; `langfuse` profile only | MinIO root password and Langfuse S3 secret access key. Rotate MinIO and both Langfuse application services together. |
| `LANGFUSE_NEXTAUTH_SECRET` | Secret; 32+ random bytes; `langfuse` profile only | Protects Langfuse web sessions. Rotation intentionally invalidates active sessions. |
| `LANGFUSE_SALT` | Secret; stable 32+ byte value; `langfuse` profile only | Salt used by Langfuse credential encryption. Treat it as persistent key material; rotation may require an application-supported migration. |
| `LANGFUSE_ENCRYPTION_KEY` | Secret; exactly 64 hexadecimal characters; `langfuse` profile only | 32-byte key used by Langfuse encryption. Generate with `openssl rand -hex 32`. Back it up securely; uncoordinated replacement can make encrypted data unreadable. |
| `LANGFUSE_PUBLIC_KEY` | Sensitive; `pk-lf-...`; `.env`, required for `langfuse` profile | Public identifier for the pre-provisioned Langfuse project. It remains in the core environment because both LiteLLM services consume it. Keep it paired with `LANGFUSE_SECRET_KEY`. |
| `LANGFUSE_SECRET_KEY` | Secret; `sk-lf-...`; `.env`, required for `langfuse` profile | Secret key for the pre-provisioned Langfuse project and LiteLLM callbacks. It remains in the core environment so ordinary LiteLLM lifecycle commands preserve the integration. Rotate in Langfuse and both LiteLLM profiles together. |
| `LANGFUSE_USER_ID_SALT` | Secret; 32+ random bytes; `.env`, required for `langfuse` profile | HMAC salt used by core LiteLLM attribution and as a migration fallback for the OpenSearch bridge. Keep stable to preserve longitudinal attribution; rotation changes all derived identifiers. |
| `LANGFUSE_INIT_USER_EMAIL` | Sensitive; email address; `langfuse` profile only | Bootstrap Langfuse administrator email. Used during first initialization; later changes do not rename an existing account. |
| `LANGFUSE_INIT_USER_NAME` | Sensitive; display name; `langfuse` profile only | Bootstrap Langfuse administrator display name. Used during first initialization. |
| `LANGFUSE_INIT_USER_PASSWORD` | Secret; strong random password; `langfuse` profile only | Bootstrap Langfuse administrator password. Keep it in the secrets manager for the life of the deployment. Langfuse only uses it to create a missing user and never resets an existing user's password. Public sign-up is disabled and no SMTP is configured, so this stored credential is the recovery path. If you change the password in Langfuse, store the new password in the secrets manager immediately. |

Recommended generation commands:

```sh
# URL-safe passwords and generic 256-bit secrets
openssl rand -hex 32

# Langfuse encryption key: exactly 64 hexadecimal characters
openssl rand -hex 32

# Pre-provisioned Langfuse project keys
printf 'pk-lf-%s\n' "$(openssl rand -hex 16)"
printf 'sk-lf-%s\n' "$(openssl rand -hex 16)"
```

## LiteLLM and observability

| Variable | Default / format | Description and guidance |
| --- | --- | --- |
| `LITELLM_STORE_MODEL_IN_DB` | `True`; LiteLLM boolean | Enables database-backed model storage for both profiles. Keep enabled when models are managed through the LiteLLM UI/API. |
| `LITELLM_OTEL_V2` | `true`; boolean | Enables LiteLLM OpenTelemetry v2 behavior. Disable only after validating trace compatibility and accepting reduced observability. |
| `LITELLM_FORWARDED_ALLOW_IPS` | `*`; comma-separated IPs or networks | Controls which forwarding proxies LiteLLM trusts. The default is acceptable only while LiteLLM is reachable exclusively through the isolated Compose networks. Set explicit Traefik proxy addresses or networks when deploying into a shared network. Never expose LiteLLM directly with wildcard trust. |
| `OTEL_GENAI_CAPTURE_MESSAGE_CONTENT` | `no_content`; enum supported by the installed OpenTelemetry instrumentation | Controls whether prompt and response content is captured in telemetry. Keep `no_content` to avoid recording credentials, personal data, or proprietary prompts. |
| `OTEL_PYTHON_FASTAPI_EXCLUDED_URLS` | `.*`; regular expression | Excludes matching FastAPI URLs from automatic tracing. The default excludes all auto-instrumented routes because stack-owned callbacks provide controlled telemetry. Test regex changes to avoid sensitive or high-cardinality trace data. |
| `GRAFANA_ALLOW_SIGN_UP` | `false`; boolean; `grafana` profile only | Enables self-service Grafana registration. Keep disabled; provision users through administrator-controlled workflows. |
| `ROI_MINUTES_SAVED_PER_REQUEST` | `15`; 0-1440 | Scenario minutes attributed to each successful request. Replace the default with locally measured evidence; set to `0` when session-level value captures the same work. |
| `ROI_MINUTES_SAVED_PER_SESSION` | `0`; 0-10080 | Scenario minutes attributed once to each distinct successful `session_id`. The zero default prevents unsupported value and double counting until local session evidence exists. |
| `ROI_LOADED_HOURLY_COST` | `75`; 0-10000 | Fully loaded hourly labor cost used by the scenario. Use a finance-approved regional value in the same currency as model spend. |
| `ROI_VALUE_REALIZATION_FACTOR` | `0.25`; 0-1 | Portion of theoretical time savings expected to become usable value. Validate through local workflow sampling. |
| `ROI_CURRENCY` | `USD`; three-letter uppercase ISO 4217 code | Display currency for the scenario. LiteLLM model spend and loaded hourly cost must use this same currency before net value is meaningful. |
| `PROMETHEUS_RETENTION_TIME` | `15d`; Prometheus duration | Maximum age of Prometheus samples. Size retention can evict data earlier. Plan storage before increasing it. |
| `PROMETHEUS_RETENTION_SIZE` | `10GB`; Prometheus size | Maximum Prometheus TSDB size. Keep headroom on the Docker volume filesystem; Prometheus treats this as an upper retention target, not a disk quota. |

All variables in this section require recreation of their consuming service.
ROI changes additionally require rerunning `postgres-init` and
`opensearch-dashboards-setup`; recreate `opensearch-bridge` so new session
pseudonyms are indexed.
Retention changes do not recover data already deleted.

## OpenSearch observability

| Variable | Default / format | Description and guidance |
| --- | --- | --- |
| `OPENSEARCH_JAVA_HEAP` | `1g`; JVM size | Heap for the single OpenSearch node (`-Xms` and `-Xmx`). Keep it at or below half of `OPENSEARCH_MEMORY_LIMIT`. |
| `OPENSEARCH_MEMORY_LIMIT` | `3g`; Docker size | Container memory limit for OpenSearch. |
| `DATA_PREPPER_JAVA_OPTS` | `-Xms256m -Xmx512m`; JVM options | Data Prepper JVM options. |
| `DATA_PREPPER_MEMORY_LIMIT` | `1g`; Docker size | Container memory limit for Data Prepper. |
| `OPENSEARCH_LOG_RETENTION_DAYS` | `7`; days | ISM deletes `litellm-logs-*` daily indices after this age (matches Loki). |
| `OPENSEARCH_METRICS_RETENTION_DAYS` | `15`; days | ISM deletes `litellm-metrics-*` and `litellm-alert-state-*` after this age (matches Prometheus). |
| `OPENSEARCH_USAGE_RETENTION_DAYS` | `180`; days | `opensearch-bridge` deletes spend, provider-error, and audit copies older than this (matches LiteLLM's spend-log cleanup). It is also the initial backfill window. |
| `OPENSEARCH_TRACE_RETENTION_DAYS` | `15`; days | ISM rolls LiteLLM span indices (`otel-v1-apm-span-*`) over daily and deletes them after this age. |
| `LITELLM_OTEL_TRACE_EXPORTER` | `otlp_grpc` | Where LiteLLM sends its OpenTelemetry spans besides Langfuse. `otlp_grpc` delivers them to Data Prepper and OpenSearch Trace Analytics. `console` prints every span as multi-line JSON to stdout (the previous behavior) and should be used only for short debugging sessions. |
| `OPENSEARCH_AUDIT_RETENTION_DAYS` | `90`; days | ISM deletes monthly `security-auditlog-*` indices this long after creation, so up to one extra month is retained. |
| `OPENSEARCH_BRIDGE_INTERVAL_SECONDS` | `60`; 15-600 | Prometheus evaluation and usage-sync interval. Alert monitors assume one evaluation per interval; re-run `opensearch-setup` after changing it. |
| `OPENSEARCH_USAGE_OVERLAP_MINUTES` | `30`; minutes | Window re-read on every sync to pick up spend rows LiteLLM writes after a request finishes. Rows written later than this after their start time are not copied. |
| `OPENSEARCH_CERT_VALIDITY_DAYS` | `397`; days | Validity of node and admin certificates issued by `opensearch-certs`. |
| `OPENSEARCH_CERT_RENEW_BEFORE_DAYS` | `30`; days | `opensearch-certs` reissues leaf certificates inside this window. Restart the stack (or run `docker compose up -d opensearch-certs` and recreate `opensearch`) at least once per window. |
| `OPENSEARCH_ALERT_CHANNEL_ID` | empty | ID of an OpenSearch Notifications channel (Slack, email, webhook, ...). When set, every provisioned monitor notifies it. Create the channel in Dashboards first, then re-run `opensearch-setup`. |

## Optional Langfuse profile behavior and bootstrap metadata

| Variable | Default / format | Description and guidance |
| --- | --- | --- |
| `LANGFUSE_TELEMETRY_ENABLED` | `false`; boolean | Controls Langfuse product telemetry. Keep disabled unless organizational policy explicitly permits outbound telemetry. |
| `LANGFUSE_ENABLE_EXPERIMENTAL_FEATURES` | `false`; boolean | Enables experimental Langfuse features. Keep disabled in production because behavior and data models may change. |
| `LANGFUSE_S3_BATCH_EXPORT_ENABLED` | `false`; boolean | Enables Langfuse S3 batch export. Enabling it can create additional object-storage data and requires a retention and access-control review. |
| `LANGFUSE_REDIS_TLS_ENABLED` | `false`; boolean | Controls TLS for Langfuse-to-Redis traffic. The bundled Redis endpoint is isolated and plaintext; enabling this requires a TLS-enabled Redis deployment and matching trust configuration. |
| `LANGFUSE_INIT_ORG_ID` | `litellm-stack`; stable slug | Identifier for the bootstrap organization. Treat as immutable after initialization. |
| `LANGFUSE_INIT_ORG_NAME` | `LiteLLM Stack`; text | Display name for the bootstrap organization. Primarily used on first initialization. |
| `LANGFUSE_INIT_PROJECT_ID` | `litellm`; stable slug | Identifier for the bootstrap Langfuse project. Keep aligned with the provisioned public and secret keys. |
| `LANGFUSE_INIT_PROJECT_NAME` | `LiteLLM`; text | Display name for the bootstrap project. Primarily used on first initialization. |
| `LANGFUSE_TRUSTED_PROXY_COUNT` | `1`; non-negative integer | Number of reverse proxies trusted when resolving client information. The bundled path has exactly one proxy, Traefik. Incorrectly high values can trust attacker-supplied forwarding headers. |
| `LANGFUSE_DISABLE_SIGNUP_ONBOARDING` | `true`; boolean | Skips the post-signup newsletter step so the browser makes no request to langfuse.com. It does **not** control registration: Compose pins `AUTH_DISABLE_SIGNUP=true` on `langfuse-web`, so public self-registration is always off. Users come from the headless `LANGFUSE_INIT_USER_*` bootstrap; per Langfuse, this also blocks invited users who do not yet have an account. |

Bootstrap variables are not a general migration mechanism. After Langfuse has
initialized its database, make supported identity or tenant changes through
Langfuse and verify whether the bootstrap values should remain unchanged.
Langfuse web, worker, PostgreSQL, and ClickHouse infrastructure remains on UTC
because Langfuse does not support overriding its infrastructure timezone.

### Langfuse operator accounts and lockout recovery

Langfuse has public sign-up disabled (`AUTH_DISABLE_SIGNUP=true`), no SMTP, and
no SSO provider. Self-service sign-up, invitation acceptance for new accounts,
and "forgot password" therefore do not work. On every `langfuse-web` start,
Langfuse creates the `LANGFUSE_INIT_USER_EMAIL` user only if it does not exist.
It then makes that user an `OWNER` of `LANGFUSE_INIT_ORG_ID` and never changes
an existing password. Use that behavior for both cases below:

1. **Add an operator or recover a locked-out administrator.** In
   `.env.optional`, set `LANGFUSE_INIT_USER_EMAIL` to an address that has no
   Langfuse account yet, `LANGFUSE_INIT_USER_NAME` to the person's name, and
   `LANGFUSE_INIT_USER_PASSWORD` to a new value generated with
   `openssl rand -hex 32`. Store all three in the secrets manager, then run
   `docker compose --env-file .env --env-file .env.optional --profile prod --profile langfuse up -d langfuse-web`.
   The new account signs in as an organization owner and can grant project
   access to existing accounts or remove a compromised or unrecoverable one.
2. **Restore the original bootstrap values** afterward if they are still the
   intended break-glass identity. Every account created this way stays an
   owner until an owner removes it in **Organization settings → Members**.

Never edit password hashes in `langfuse-postgres` directly. Never set
`AUTH_DISABLE_SIGNUP=false` to onboard users: the repository contract rejects it,
and it would let anyone who can reach the hostname create an account.

## Development model and data services

These variables primarily affect the `dev` profile. Model, dtype, context length,
or embedding dimension changes must be validated together; incompatible values
can cause startup failures or corrupt semantic-cache expectations.

### BGE-M3 vLLM

| Variable | Default / format | Description and guidance |
| --- | --- | --- |
| `BGE_M3_MODEL` | `BAAI/bge-m3`; model identifier or approved local path | Model loaded by vLLM. Review model provenance, license, and remote-code requirements before changing it. |
| `BGE_M3_REVISION` | `5617a9f61b028005a4858fdac845db406aefb181`; full 40-character Hugging Face commit SHA | Immutable model and tokenizer revision loaded by vLLM, so a cache rebuild cannot silently fetch different weights. Change it only together with `BGE_M3_MODEL` and the model entry in `docs/third-party-licences.md`, after reviewing the licence and file list of the new revision. |
| `BGE_M3_SERVED_MODEL_NAME` | `bge-m3`; model alias | Name exposed by the internal OpenAI-compatible endpoint. Keep aligned with downstream model references. |
| `BGE_M3_DTYPE` | `half`; vLLM dtype | Inference precision. Choose a dtype supported by the GPU and model. Lower precision reduces memory but can affect quality. |
| `BGE_M3_MAX_MODEL_LEN` | `8192`; positive integer | Maximum token length accepted by the embedding server. Larger values increase memory pressure. |
| `BGE_M3_VLLM_GPU_MEMORY_UTILIZATION` | `0.35`; decimal greater than `0` and at most `1` | Fraction of GPU memory available to vLLM. Increase gradually and leave headroom for the driver and other workloads. |
| `BGE_M3_SHM_SIZE` | `2gb`; Docker size | Container shared-memory allocation. Increase only when required by runtime errors and available host memory. |
| `BGE_M3_MEMORY_LIMIT` | `6g`; Docker size | Container memory limit. Set above expected model/runtime use while preserving host headroom. |
| `BGE_M3_HEALTH_START_PERIOD` | `15m`; Docker duration | Grace period before failed health checks count during model download and load. Increase for cold caches or slow storage. |

### Qdrant and semantic router

| Variable | Default / format | Description and guidance |
| --- | --- | --- |
| `QDRANT_STORAGE_SIZE` | `1g`; Docker tmpfs size | Volatile Qdrant storage limit. The bundled development cache is intentionally non-persistent; all contents disappear on recreation. |
| `QDRANT_CPU_LIMIT` | `1`; positive decimal CPUs | CPU limit for Qdrant. |
| `QDRANT_MEMORY_LIMIT` | `2g`; Docker size | Memory limit for Qdrant. Keep enough headroom for indexes and query peaks. |
| `D3_DECISION_API_BASE` | Required; HTTPS base URL; example: `https://d3-mini.${STACK_DOMAIN}:8443` | Operator-controlled remote D3-mini endpoint, without `/v1/systemone`. LiteLLM appends the path. Allowlist this destination in network policy and use a certificate trusted by the LiteLLM host. |
| `D3_DECISION_API_KEY` | Required secret, at least 32 random characters | Bearer credential shared only by LiteLLM and the remote D3 listener. Rotate it independently of LiteLLM client keys. |
| `D3_DECISION_CA_BUNDLE` | Required host path to a PEM bundle | CA bundle mounted into both LiteLLM containers. It must include the CA that issued the D3 certificate **and** the normal public roots needed by completion providers; setting `SSL_CERT_FILE` to a private CA alone would break those providers. |

### Presidio, Xberg, and pgvector connector

| Variable | Default / format | Description and guidance |
| --- | --- | --- |
| `PRESIDIO_NLP_ENGINE` | `spacy`; Presidio engine name | NLP engine used by Presidio Analyzer. A different engine requires compatible analyzer configuration and image support. |
| `PRESIDIO_SUPPORTED_LANGUAGES` | `en`; comma-separated language codes supported by the image | Languages exposed by Presidio. Adding a language also requires compatible NLP models and recognizers. |
| `PRESIDIO_ANALYZER_MEMORY_LIMIT` | `2g`; Docker size | Memory limit for Presidio Analyzer. Increase when adding models or languages. |
| `XBERG_AUTO_INDEX_ENABLED` | `false`; boolean | Explicit opt-in for automatic development extraction indexing. Enabling it sends eligible content through Xberg, Presidio, the embedding service, and pgvector; complete a data-handling review first. |
| `XBERG_INDEX_EMBEDDING_MODEL` | `hosted_vllm/bge-m3`; LiteLLM model alias | Embedding model used by automatic indexing. Keep compatible with the configured vector dimensions. |
| `XBERG_LOG_LEVEL` | `info`; Rust log filter | Xberg logging verbosity. Avoid verbose logging for sensitive document workloads. |
| `XBERG_MAX_REQUEST_BODY_BYTES` | `10485760`; positive integer bytes | Maximum HTTP request body accepted by Xberg; default is 10 MiB. Keep bounded to reduce denial-of-service risk. |
| `XBERG_MAX_MULTIPART_FIELD_BYTES` | `10485760`; positive integer bytes | Maximum multipart field size accepted by Xberg; default is 10 MiB. It should not exceed the request body limit without a documented reason. |
| `XBERG_CPU_LIMIT` | `1`; positive decimal CPUs | CPU limit for Xberg. |
| `XBERG_MEMORY_LIMIT` | `1g`; Docker size | Memory limit for Xberg. Consider request-size limits when tuning it. |
| `XBERG_SANDBOX_SUBNET` | `172.29.0.0/24`; private IPv4 CIDR | Dedicated internal subnet for Xberg and its restricted egress helpers. Change it if the default overlaps another Docker or host network. |
| `XBERG_DNS_IP` | `172.29.0.53`; IPv4 address within `XBERG_SANDBOX_SUBNET` | Fixed address of the DNS-only relay that lets Xberg's SSRF validator resolve public names without giving Xberg direct Internet access. |
| `PGVECTOR_EMBEDDING_MODEL` | `openai/bge-m3`; connector model identifier | Model the pgvector connector requests from LiteLLM development. Keep aligned with LiteLLM model configuration. |
| `PGVECTOR_EMBEDDING_DIMENSIONS` | `1024`; positive integer | Stored vector width. It must match model output and the database schema. Changing it requires a planned schema migration and re-index. |

## Image-security monitor

The Compose scheduler variables in this table live in `.env.optional`.

| Variable | Default / format | Description and guidance |
| --- | --- | --- |
| `IMAGE_SECURITY_UID` | `1000`; Linux UID | Runtime UID for the monitor container. It must own or be able to write `security-state/`, `security-reports/`, and `security-metrics/`. |
| `IMAGE_SECURITY_GID` | `1000`; Linux GID | Runtime GID for the monitor container and writable security directories. |
| `IMAGE_SECURITY_SCHEDULE_HOUR` | `2`; integer `0-23` | Local-time hour for the daily scan in `STACK_TIMEZONE`. |
| `IMAGE_SECURITY_SCHEDULE_MINUTE` | `45`; integer `0-59` | Local-time minute for the daily scan. |
| `IMAGE_SECURITY_RUN_ON_START` | `true`; boolean | Runs one scan when the scheduler starts. Keep enabled to avoid waiting for the first scheduled result. |
| `IMAGE_SECURITY_SBOM_ENABLED` | `true`; boolean | Generates CycloneDX SBOMs for new running image digests after each scheduled scan. Existing digests are not regenerated. |
| `IMAGE_SECURITY_SBOM_LICENCE_SCAN` | `true`; boolean | Adds a one-time Trivy full licence scan per new digest as raw licence evidence. Disable only if the scan time for very large images is unacceptable; the curated register in `docs/third-party-licences.md` remains authoritative. |
| `IMAGE_SECURITY_SBOM_RETENTION_DAYS` | `400`; non-negative integer | Retention for stack SBOM runs and image SBOMs no retained run references. The newest run is always kept; `0` disables pruning. Align with the audit retention period. |
| `IMAGE_SECURITY_CPU_LIMIT` | `2`; positive decimal CPUs | CPU limit for image scanning. Scans are CPU intensive; reduce only if longer scan times are acceptable. |
| `IMAGE_SECURITY_MEMORY_LIMIT` | `2500m`; Docker size | Memory limit for image scanning. Too small a value can terminate scans of large images. |
| `IMAGE_SECURITY_PIDS_LIMIT` | `256`; positive integer | Maximum process count for the monitor container. Keep bounded as a defense-in-depth control. |
| `IMAGE_SECURITY_RESTART_POLICY` | `on-failure:5`; Compose restart policy | Restarts the monitor after failures, up to five times. Avoid `always` when persistent configuration errors should remain visible. |

The following variables configure direct host execution of the image-security
scripts and system service installer. They are not Compose substitutions and are
therefore not included in either environment template. Export them in the
invoking shell or the generated service definition.

| Variable | Default | Description and guidance |
| --- | --- | --- |
| `IMAGE_SECURITY_STATE_DIR` | `./security-state` | Absolute or repository-relative private state directory. Must not be `/` or a symbolic link. |
| `IMAGE_SECURITY_REPORT_DIR` | `./security-reports` | Private report output directory. Reports can disclose image inventory and vulnerability details. |
| `IMAGE_SECURITY_METRICS_DIR` | `./security-metrics` | Directory for node-exporter textfile metrics. Must not be `/` or a symbolic link. |
| `IMAGE_SECURITY_EXCEPTIONS_FILE` | `./security/exceptions.toml` | Reviewed vulnerability exception policy. Restrict write access and require change review. |
| `IMAGE_SECURITY_TRIVY_EXECUTABLE` | Auto-detected | Optional absolute path to an executable Trivy binary. Do not point it to a user-writable or untrusted binary. |
| `IMAGE_SECURITY_COMMAND_TIMEOUT_SECONDS` | `900` | Positive integer command timeout. Keep finite so stalled scans fail visibly. |
| `IMAGE_SECURITY_RETENTION_DAYS` | `30` | Non-negative integer report retention. `0` removes completed reports immediately; ensure this satisfies audit requirements. |
| `IMAGE_SECURITY_SBOM_LICENCE_SCAN`, `IMAGE_SECURITY_SBOM_RETENTION_DAYS` | As above | Also honoured by `./scripts/maintenance.py sbom`. `IMAGE_SECURITY_SBOM_ENABLED` applies only to scheduled runs; the systemd timer accepts `--no-sbom` instead. |
| `STACK_REPOSITORY_REVISION` | Git `HEAD` for SBOMs; `unknown` for image labels | Repository commit recorded in the stack SBOM when the checkout has no Git metadata, and in the `org.opencontainers.image.revision` label of images this repository builds. Export it, for example `export STACK_REPOSITORY_REVISION="$(git rev-parse HEAD)"`, before `docker compose build`. |
| `STACK_REPOSITORY_URL` | Empty | Optional URL of this repository, recorded in the `org.opencontainers.image.source` and `org.opencontainers.image.url` labels of images this repository builds. Export it before `docker compose build`; leave it empty rather than pointing to a URL the organisation does not control. |

## Maintenance scripts

These variables are read by host-side maintenance scripts, not by Compose.
Export them in the shell, scheduler, or service that invokes the script.

| Variable | Default / format | Description and guidance |
| --- | --- | --- |
| `BACKUP_DIR` | `./backups`; private directory path | Root for database backup sets. Prefer an absolute path on encrypted storage outside the repository. The script rejects unsafe roots and symbolic-link traversal. |
| `BACKUP_RETENTION_DAYS` | `14`; non-negative integer | Age after which complete backup sets are removed. Set from recovery objectives and compliance policy; verify off-host copies before reducing retention. |
| `UPGRADE_STATE_DIR` | `./upgrade-state`; private directory path | Stores upgrade preparation and rollback state. Protect it with the same controls as backups because it contains operational metadata and database backup material. |

Standard operating-system variables such as `SUDO_USER`, `CAROOT`,
`LOCALAPPDATA`, and `XDG_DATA_HOME` may be consulted by specific tools for
platform integration. They are not stack configuration and should not be added
to `.env` or `.env.optional`.

## Operating guidelines

### Initial setup

1. Copy the template with `cp .env.example .env`.
2. Replace every `replace_with_...` placeholder and every example identity.
3. Set file permissions with `chmod 600 .env`.
4. Validate interpolation with `docker compose config --quiet`.
5. Review the fully rendered configuration with `docker compose config`; store
   rendered output only in a protected temporary location because it contains
   secrets.
6. Start only the intended profiles.

For an optional profile, additionally run
`cp .env.optional.example .env.optional`, set mode `0600`, populate that
profile's values, and use
`docker compose --env-file .env --env-file .env.optional ...`.

Compose reads environment files for interpolation but does not execute shell
expressions. Generate values first and paste literal output. Do not use command
substitutions such as `$(openssl rand ...)` in either environment file.

### Precedence

Shell environment values take precedence over values loaded from an environment
file. When `--env-file` is supplied, Compose uses that selected file instead of
the project `.env`; otherwise it discovers the default `.env`. Multiple
`--env-file` options are layered in command-line order, with later files
overriding earlier files. Defaults declared in `docker-compose.yml` apply only
when the selected inputs do not set a value. The `-p` command option has
precedence over `COMPOSE_PROJECT_NAME`.

Use one controlled source per environment. The committed optional Compose module
uses `.env.optional.example` only for safe interpolation defaults; explicit
`.env.optional` values take precedence when it is loaded after `.env`. Avoid
casually exporting variables in interactive shells because they can silently
override reviewed values.

### Change management

- Treat `.env.example`, `.env.optional.example`, and this document as the schema.
  Update the applicable template whenever an operator-facing variable is added,
  renamed, or removed.
- Review configuration changes through source control, but keep real secret
  values out of source control, issue trackers, logs, and rendered artifacts.
- Test with `docker compose config --quiet` before every deployment.
- Recreate affected containers after changes; a restart does not replace a
  container's environment.
- Back up stateful services before changing identities, encryption keys, database
  settings, embedding dimensions, or project names.
- Record the owner, reason, approval, deployment date, and rollback value for
  production changes.

### Secret lifecycle

- Generate at least 256 bits of entropy for local random secrets.
- Use separate values for every variable and every environment.
- Store production secrets in a managed secrets service with audit logging and
  least-privilege access.
- Rotate provider tokens and authentication secrets on a schedule and
  immediately after suspected exposure.
- Coordinate producer and consumer updates; do not rotate one side of a shared
  credential independently.
- Never print `docker compose config` into CI logs. It renders secret values.
- If a secret was committed, rotate it immediately and remove it from history
  using the organization's incident process; deleting the current line is not
  sufficient.

### Values intentionally kept out of environment files

Container image references and digests, network topology, internal service names,
mount paths, least-privilege flags, and security options remain in
`docker-compose.yml` or `compose.optional.yml`. They are version-controlled
integrity and architecture controls, not routine operator knobs. Change them
through reviewed Compose changes rather than untracked environment overrides.
