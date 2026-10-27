# Environment variable reference

This document is the operator reference for configuration accepted by the
LiteLLM stack. Copy `.env.example` to `.env`, replace every required placeholder,
and review every optional default before deployment.

## Conventions

| Term | Meaning |
| --- | --- |
| Required | Compose rejects the configuration when the value is absent. |
| Optional | The stack uses the documented default when the value is absent. |
| Secret | Confidential value. Store it in a secrets manager for production and never commit it. |
| Sensitive | Operationally sensitive value that is not an authentication secret. Restrict write access. |
| Recreate | Run `docker compose up -d --force-recreate` for affected services after changing the value. |

Values in `.env` are strings. Boolean values use `true` or `false` unless a
component explicitly requires the documented spelling. Byte sizes use
Docker/Prometheus units such as `512m`, `2g`, or `10GB`. Durations use units such
as `30s`, `15m`, or `15d`.

## Stack identity, routing, and shared defaults

| Variable | Requirement / default | Format | Description and guidance |
| --- | --- | --- | --- |
| `COMPOSE_PROJECT_NAME` | Optional: `litellm-stack` | Docker Compose project name | Prefixes containers, networks, and volumes and is used by image-security inventory. Changing it creates a logically separate stack and new named volumes. Use lowercase letters, digits, hyphens, and underscores. |
| `STACK_TIMEZONE` | Optional: `Europe/Berlin` | IANA timezone | Central European local time with automatic CET/CEST daylight-saving transitions. Used by Langfuse and the image-security scheduler. Keep an IANA region name rather than fixed `CET`, which cannot represent all civil-time transitions safely. |
| `LOG_MAX_SIZE` | Optional: `10m` | Docker size | Maximum size of one container JSON log file before rotation. Lower values reduce disk spikes; higher values retain more local troubleshooting context. |
| `LOG_MAX_FILE` | Optional: `3` | Positive integer | Number of rotated JSON log files retained per container. Total upper bound is approximately `LOG_MAX_SIZE * LOG_MAX_FILE` per container. |
| `DOCKER_SOCKET_PROXY_LOG_LEVEL` | Optional: `warning` | `debug`, `info`, `warning`, `error`, or `critical` | Shared log level for the restricted Docker socket proxies. Avoid `debug` in routine production because it increases noise and may reveal operational metadata. |
| `TRAEFIK_TLS_CERT_FILE` | Optional: `./traefik/certs/litellm-stack.pem` | Host file path | Certificate chain delivered to the on-prem host. The file is bind-mounted read-only as `/certs/tls.crt`. Use an absolute path when the certificate delivery agent writes outside the repository. Include intermediates in server-first order when required by the issuing CA. |
| `TRAEFIK_TLS_KEY_FILE` | Optional: `./traefik/certs/litellm-stack-key.pem` | Host file path; secret | Private key matching the delivered certificate. Restrict host permissions to the deployment account and Docker daemon. Never commit or copy it into images. The file is bind-mounted read-only as `/certs/tls.key`. |
| `LITELLM_PROD_HOST` | Optional: `llm.example.test` | DNS hostname | Public Traefik hostname and advertised base URL for the production-profile LiteLLM proxy. It must be covered by the TLS certificate. Do not include a scheme or path. |
| `LITELLM_DEV_HOST` | Optional: `llm-dev.example.test` | DNS hostname | Public Traefik hostname and advertised base URL for the development-profile LiteLLM proxy. It must be covered by the TLS certificate. |
| `VLLM_SR_HOST` | Optional: `llm-router.example.test` | DNS hostname | Public Traefik hostname for the development vLLM Semantic Router Envoy endpoint. |
| `GRAFANA_HOST` | Optional: `grafana.example.test` | DNS hostname | Public Traefik hostname and Grafana root URL. Changing it can invalidate generated links and browser sessions. |
| `SEARXNG_HOST` | Optional: `search.example.test` | DNS hostname | Public Traefik hostname used to construct the SearXNG base URL. |
| `LANGFUSE_WEB_HOST` | Optional: `langfuse.example.test` | DNS hostname | Public Traefik hostname used by Langfuse web/API routing and `NEXTAUTH_URL`. |
| `LANGFUSE_S3_HOST` | Optional: `langfuse-s3.example.test` | DNS hostname | Public Traefik hostname used for Langfuse signed media upload URLs. It must resolve to this stack and be covered by the TLS certificate. |

Changing a hostname requires recreation of every service that consumes that
hostname, plus coordinated updates to DNS or local host resolution, TLS
certificates, firewall policy, and clients. Traefik watches Docker labels and
discovers recreated routed services automatically.

## Credentials and cryptographic material

All secret values in this section are required. `GRAFANA_DATABASE_USER` has a
fixed compatibility default. Recreate every consumer after a rotation. For
production, inject values from a secrets manager rather than persisting them in a
developer-managed `.env` file.

| Variable | Classification / format | Description and rotation guidance |
| --- | --- | --- |
| `PG_USER` | Sensitive; optional default `litellm`; PostgreSQL identifier | Owner used by both LiteLLM databases. The initialization SQL and existing volumes assume the configured identity. Do not change it after first initialization without a database migration. |
| `PG_PASSWORD` | Secret; URL-safe random string | Password for `PG_USER`. It is embedded in PostgreSQL URLs, so use an unreserved value such as 32 or more random hexadecimal bytes. Rotate in PostgreSQL and all LiteLLM consumers together. |
| `GRAFANA_DATABASE_USER` | Sensitive; default `grafana_ro` | Read-only role consumed by Grafana datasource provisioning. Keep `grafana_ro`; the initialization SQL creates that fixed least-privilege role. |
| `GRAFANA_DATABASE_PASSWORD` | Secret; URL-safe random string | Password for Grafana's read-only LiteLLM database role. Rotate through `postgres-init` and recreate Grafana. |
| `PG_EXPORTER_PASSWORD` | Secret; URL-safe random string | Password for the `litellm_exporter` monitoring role. Rotate through `postgres-init` and recreate `postgres-exporter`. |
| `PG_VECTOR_PASSWORD` | Secret; URL-safe random string | Password for the isolated pgvector owner. Used by migrations, LiteLLM development, and the pgvector connector. Coordinate database-side and consumer changes. |
| `PG_VECTOR_EXPORTER_PASSWORD` | Secret; URL-safe random string | Password for the read-only `vector_exporter` monitoring role. Rotate through `pgvector-access-init` and recreate `pgvector-exporter`. |
| `PG_VECTOR_API_KEY` | Secret; at least 32 random bytes | Shared authentication key between LiteLLM development and the internal pgvector service. Rotate both services together. |
| `QDRANT_API_KEY` | Secret; at least 32 random bytes | API key protecting the development Qdrant service. Rotate Qdrant and LiteLLM development together. |
| `LITELLM_MASTER_KEY` | Secret; `sk-` prefix recommended | Administrative and API authentication key for both LiteLLM profiles. It is also used by the internal pgvector connector. Rotate during a maintenance window and update clients. |
| `OPENAI_API_KEY` | Secret; provider-issued token | Provider credential used by configured models. Scope it to the minimum required project and permissions, set provider-side spend limits, and rotate according to provider policy. |
| `GRAFANA_ADMIN_PASSWORD` | Secret; strong random password | Initial Grafana administrator password. Grafana persists credentials in its volume, so changing this value does not reset an already initialized administrator; use Grafana's supported reset workflow. |
| `GRAFANA_SECURITY_SECRET_KEY` | Secret; stable 32+ byte value | Encrypts Grafana datasource and alerting credentials. Back it up securely. Changing it can make existing encrypted values unreadable. |
| `SEARXNG_SECRET` | Secret; 32+ random bytes | SearXNG signing secret. Rotation invalidates signed state and existing sessions. |
| `LANGFUSE_POSTGRES_PASSWORD` | Secret; URL-safe random string | Password for Langfuse PostgreSQL. Rotate the database role and Langfuse web/worker configuration together. |
| `LANGFUSE_CLICKHOUSE_PASSWORD` | Secret; URL-safe random string | Password for Langfuse ClickHouse trace storage. Rotate ClickHouse and both Langfuse application services together. |
| `LANGFUSE_REDIS_AUTH` | Secret; strong random string | Password for the Langfuse Redis queue. Rotate Redis and both Langfuse application services together. |
| `LANGFUSE_MINIO_PASSWORD` | Secret; strong random string | MinIO root password and Langfuse S3 secret access key. Rotate MinIO and both Langfuse application services together. |
| `LANGFUSE_NEXTAUTH_SECRET` | Secret; 32+ random bytes | Protects Langfuse web sessions. Rotation intentionally invalidates active sessions. |
| `LANGFUSE_SALT` | Secret; stable 32+ byte value | Salt used by Langfuse credential encryption. Treat it as persistent key material; rotation may require an application-supported migration. |
| `LANGFUSE_ENCRYPTION_KEY` | Secret; exactly 64 hexadecimal characters | 32-byte key used by Langfuse encryption. Generate with `openssl rand -hex 32`. Back it up securely; uncoordinated replacement can make encrypted data unreadable. |
| `LANGFUSE_PUBLIC_KEY` | Sensitive; `pk-lf-...` | Public identifier for the pre-provisioned Langfuse project. Keep it paired with `LANGFUSE_SECRET_KEY`. |
| `LANGFUSE_SECRET_KEY` | Secret; `sk-lf-...` | Secret key for the pre-provisioned Langfuse project and LiteLLM callbacks. Rotate in Langfuse and both LiteLLM profiles together. |
| `LANGFUSE_USER_ID_SALT` | Secret; 32+ random bytes | HMAC salt used to pseudonymize LiteLLM principal identifiers before Langfuse attribution. Keep stable to preserve longitudinal attribution; rotation changes all derived identifiers. |
| `LANGFUSE_INIT_USER_EMAIL` | Sensitive; email address | Bootstrap Langfuse administrator email. Used during first initialization; later changes do not rename an existing account. |
| `LANGFUSE_INIT_USER_NAME` | Sensitive; display name | Bootstrap Langfuse administrator display name. Used during first initialization. |
| `LANGFUSE_INIT_USER_PASSWORD` | Secret; strong random password | Bootstrap Langfuse administrator password. Change it through Langfuse after first login and retain this value only if repeatable bootstrap is required. |

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
| `GRAFANA_ALLOW_SIGN_UP` | `false`; boolean | Enables self-service Grafana registration. Keep disabled; provision users through administrator-controlled workflows. |
| `PROMETHEUS_RETENTION_TIME` | `15d`; Prometheus duration | Maximum age of Prometheus samples. Size retention can evict data earlier. Plan storage before increasing it. |
| `PROMETHEUS_RETENTION_SIZE` | `10GB`; Prometheus size | Maximum Prometheus TSDB size. Keep headroom on the Docker volume filesystem; Prometheus treats this as an upper retention target, not a disk quota. |

All variables in this section require recreation of their consuming service.
Retention changes do not recover data already deleted.

## Langfuse behavior and bootstrap metadata

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
| `LANGFUSE_DISABLE_SIGNUP_ONBOARDING` | `true`; boolean | Disables open signup/onboarding. Keep enabled for a private stack and create accounts administratively. |

Bootstrap variables are not a general migration mechanism. After Langfuse has
initialized its database, make supported identity or tenant changes through
Langfuse and verify whether the bootstrap values should remain unchanged.

## Development model and data services

These variables primarily affect the `dev` profile. Model, dtype, context length,
or embedding dimension changes must be validated together; incompatible values
can cause startup failures or corrupt semantic-cache expectations.

### BGE-M3 vLLM

| Variable | Default / format | Description and guidance |
| --- | --- | --- |
| `BGE_M3_MODEL` | `BAAI/bge-m3`; model identifier or approved local path | Model loaded by vLLM. Review model provenance, license, and remote-code requirements before changing it. |
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
| `VLLM_SR_CPU_LIMIT` | `2`; positive decimal CPUs | CPU limit for the semantic-router service. |
| `VLLM_SR_MEMORY_LIMIT` | `7g`; Docker size | Memory limit for router models. Lower values can cause out-of-memory termination during model loading. |
| `VLLM_SR_HEALTH_START_PERIOD` | `5m`; Docker duration | Health-check grace period while router models initialize. |
| `VLLM_SR_ENVOY_LOG_LEVEL` | `warning`; Envoy log level | Envoy verbosity. Use `info` or `debug` only for time-bounded troubleshooting. |
| `VLLM_SR_ENVOY_CPU_LIMIT` | `1`; positive decimal CPUs | CPU limit for the Envoy front end. |
| `VLLM_SR_ENVOY_MEMORY_LIMIT` | `512m`; Docker size | Memory limit for the Envoy front end. |

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
| `PGVECTOR_EMBEDDING_MODEL` | `openai/bge-m3`; connector model identifier | Model the pgvector connector requests from LiteLLM development. Keep aligned with LiteLLM model configuration. |
| `PGVECTOR_EMBEDDING_DIMENSIONS` | `1024`; positive integer | Stored vector width. It must match model output and the database schema. Changing it requires a planned schema migration and re-index. |

## Image-security monitor

| Variable | Default / format | Description and guidance |
| --- | --- | --- |
| `IMAGE_SECURITY_UID` | `1000`; Linux UID | Runtime UID for the monitor container. It must own or be able to write `security-state/`, `security-reports/`, and `security-metrics/`. |
| `IMAGE_SECURITY_GID` | `1000`; Linux GID | Runtime GID for the monitor container and writable security directories. |
| `IMAGE_SECURITY_SCHEDULE_HOUR` | `2`; integer `0-23` | Local-time hour for the daily scan. Container local time follows the host through `/etc/localtime`. |
| `IMAGE_SECURITY_SCHEDULE_MINUTE` | `45`; integer `0-59` | Local-time minute for the daily scan. |
| `IMAGE_SECURITY_RUN_ON_START` | `true`; boolean | Runs one scan when the scheduler starts. Keep enabled to avoid waiting for the first scheduled result. |
| `IMAGE_SECURITY_CPU_LIMIT` | `2`; positive decimal CPUs | CPU limit for image scanning. Scans are CPU intensive; reduce only if longer scan times are acceptable. |
| `IMAGE_SECURITY_MEMORY_LIMIT` | `2500m`; Docker size | Memory limit for image scanning. Too small a value can terminate scans of large images. |
| `IMAGE_SECURITY_PIDS_LIMIT` | `256`; positive integer | Maximum process count for the monitor container. Keep bounded as a defense-in-depth control. |
| `IMAGE_SECURITY_RESTART_POLICY` | `on-failure:5`; Compose restart policy | Restarts the monitor after failures, up to five times. Avoid `always` when persistent configuration errors should remain visible. |

The following variables configure direct host execution of the image-security
scripts and system service installer. They are not Compose substitutions and are
therefore not included in `.env.example`. Export them in the invoking shell or
the generated service definition.

| Variable | Default | Description and guidance |
| --- | --- | --- |
| `IMAGE_SECURITY_STATE_DIR` | `./security-state` | Absolute or repository-relative private state directory. Must not be `/` or a symbolic link. |
| `IMAGE_SECURITY_REPORT_DIR` | `./security-reports` | Private report output directory. Reports can disclose image inventory and vulnerability details. |
| `IMAGE_SECURITY_METRICS_DIR` | `./security-metrics` | Directory for node-exporter textfile metrics. Must not be `/` or a symbolic link. |
| `IMAGE_SECURITY_EXCEPTIONS_FILE` | `./security/exceptions.toml` | Reviewed vulnerability exception policy. Restrict write access and require change review. |
| `IMAGE_SECURITY_TRIVY_EXECUTABLE` | Auto-detected | Optional absolute path to an executable Trivy binary. Do not point it to a user-writable or untrusted binary. |
| `IMAGE_SECURITY_COMMAND_TIMEOUT_SECONDS` | `900` | Positive integer command timeout. Keep finite so stalled scans fail visibly. |
| `IMAGE_SECURITY_RETENTION_DAYS` | `30` | Non-negative integer report retention. `0` removes completed reports immediately; ensure this satisfies audit requirements. |

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
to `.env`.

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

Compose reads `.env` for interpolation but does not execute shell expressions.
Generate values first and paste literal output. Do not use command substitutions
such as `$(openssl rand ...)` in `.env`.

### Precedence

Shell environment values take precedence over values loaded from an environment
file. When `--env-file` is supplied, Compose uses that selected file instead of
the project `.env`; otherwise it discovers the default `.env`. Multiple
`--env-file` options are layered in command-line order, with later files
overriding earlier files. Defaults declared in `docker-compose.yml` apply only
when the selected inputs do not set a value. The `-p` command option has
precedence over `COMPOSE_PROJECT_NAME`.

Use one controlled source per environment. Avoid casually exporting variables in
interactive shells because they can silently override reviewed `.env` values.

### Change management

- Treat `.env.example` and this document as the schema. Update both whenever an
  operator-facing variable is added, renamed, or removed.
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

### Values intentionally kept out of `.env`

Container image references and digests, network topology, internal service names,
mount paths, least-privilege flags, and security options remain in
`docker-compose.yml`. They are version-controlled integrity and architecture
controls, not routine operator knobs. Change them through reviewed Compose
changes rather than untracked environment overrides.
