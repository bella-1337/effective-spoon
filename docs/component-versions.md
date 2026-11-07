# Component versions

[← Back to the README](../README.md#reference)

Every image the stack runs or builds, every model it loads, and the host
tools it needs, with the version in use, in one place. Every image is
pinned by digest in its Compose file or Dockerfile. This page lists the
readable tag; check the digest in the source file.

`./scripts/maintenance.py doctor` and `scripts/ci/validate.sh` fail when this
table is missing an image the stack uses, or still lists one it no longer uses.
Update the row in the same change that moves a tag. A digest-only update needs
no edit. Licences of the same components are in the
[third-party licence register](third-party-licences.md).

Profile `core` services run without a profile. Other profiles are described in
[Runtime profiles](../ARCHITECTURE.md#runtime-profiles).

## Edge and gateways

| Component | Version | Image | Used by (profile) |
| --- | --- | --- | --- |
| LiteLLM (production, LTS) | `v1.108.2` | `ghcr.io/berriai/litellm` | `litellm-prod` (prod) |
| LiteLLM (development, nightly) | `v1.110.0-dev.1` | `ghcr.io/berriai/litellm` | `litellm-dev` (dev) |
| Traefik | `v3.7.17` | `traefik` | `traefik` (core) |
| Docker socket proxy | `v0.5.0` | `ghcr.io/tecnativa/docker-socket-proxy` | `traefik-docker-proxy`, `alloy-docker-proxy` (core); `image-security-docker-proxy` (security) |

## Guardrails, search, and extraction

| Component | Version | Image | Used by (profile) |
| --- | --- | --- | --- |
| Presidio analyzer | `2.2.362` | `mcr.microsoft.com/presidio-analyzer` | `presidio-analyzer` (core) |
| Presidio anonymizer | `2.2.362` | `mcr.microsoft.com/presidio-anonymizer` | `presidio-anonymizer` (core) |
| SearXNG | `2026.10.30-ffa88e3ba` | `searxng/searxng` | `searxng` (core) |
| Xberg | `1.3.10-core` | `ghcr.io/xberg-io/xberg` | `xberg` (prod, dev) |
| BIND 9 (Xberg DNS) | `9.20-26.04_stable` | `ubuntu/bind9` | `xberg-dns` (prod, dev) |
| Squid (Xberg egress proxy) | `6.13-25.04_edge` | `ubuntu/squid` | `xberg-egress-proxy` (core) |

## Data stores

| Component | Version | Image | Used by (profile) |
| --- | --- | --- | --- |
| PostgreSQL 16 | `16.15-alpine3.24` | `postgres` | `postgres`, `postgres-init`, `pgvector-access-init`, `pgvector-migrate` (core); `litellm-demo-data-dev` (dev); `litellm-demo-data-prod` (demo) |
| PostgreSQL 16 + pgvector | `0.8.7-pg16` | `pgvector/pgvector` | `vector-postgres` (core) |
| LiteLLM pgvector connector (local build) | `5bd8f3ab1fa9129e758a2c4b17ba1c6f3047ba24` | `litellm-pgvector` | `litellm-pgvector` (dev) |
| Qdrant | `v1.20.0` | `qdrant/qdrant` | `qdrant` (dev) |

## Observability

| Component | Version | Image | Used by (profile) |
| --- | --- | --- | --- |
| OpenSearch | `3.9.0` | `opensearchproject/opensearch` | `opensearch` (core) |
| OpenSearch Dashboards | `3.9.0` | `opensearchproject/opensearch-dashboards` | `opensearch-dashboards` (core) |
| Data Prepper | `2.16.0` | `opensearchproject/data-prepper` | `data-prepper` (core) |
| OpenSearch tools (local build) | `0.1.0` | `litellm-opensearch-tools` | `opensearch-certs`, `opensearch-setup`, `opensearch-dashboards-setup`, `opensearch-bridge` (core) |
| Prometheus | `v3.15.1` | `prom/prometheus` | `prometheus`, `prometheus-secrets-init` (core) |
| Grafana Alloy | `v1.21.3` | `grafana/alloy` | `alloy` (core); `alloy-loki` (grafana) |
| Blackbox exporter | `v0.29.1` | `prom/blackbox-exporter` | `blackbox` (core) |
| Node exporter | `v1.12.1` | `prom/node-exporter` | `node-exporter` (core) |
| cAdvisor | `v0.55.1` | `gcr.io/cadvisor/cadvisor` | `cadvisor` (core) |
| PostgreSQL exporter | `v0.20.1` | `prometheuscommunity/postgres-exporter` | `postgres-exporter`, `pgvector-exporter` (core) |
| Grafana | `13.3.0` | `grafana/grafana` | `grafana` (grafana) |
| Loki | `3.7.9` | `grafana/loki` | `loki` (grafana) |

## Langfuse

| Component | Version | Image | Used by (profile) |
| --- | --- | --- | --- |
| Langfuse web | `4.72.0` | `docker.langfuse.com/langfuse/langfuse` | `langfuse-web` (langfuse) |
| Langfuse worker | `4.72.0` | `docker.langfuse.com/langfuse/langfuse-worker` | `langfuse-worker` (langfuse) |
| PostgreSQL 17 | `17.11` | `postgres` | `langfuse-postgres`, `langfuse-preflight` (langfuse); `grafana-db-init` (grafana) |
| ClickHouse | `26.3.62.3` | `clickhouse/clickhouse-server` | `langfuse-clickhouse`, `langfuse-clickhouse-observability-setup` (langfuse) |
| Redis | `7.4.12` | `redis` | `langfuse-redis` (langfuse) |
| MinIO (Chainguard) | `latest` | `cgr.dev/chainguard/minio` | `langfuse-minio` (langfuse) |

## Image security

| Component | Version | Image | Used by (profile) |
| --- | --- | --- | --- |
| Image-security monitor (local build) | `0.1.0` | `litellm-image-security-monitor` | `image-security-monitor` (security) |
| Trivy | `0.76.1` | `aquasec/trivy` | Copied into the monitor image; host-mode scans |
| Docker CLI and buildx | `29.9.2-cli` | `docker` | Copied into the monitor image |

## Build base images

| Component | Version | Image | Used by (profile) |
| --- | --- | --- | --- |
| Python | `3.11-slim-bookworm` | `python` | Base of `pgvector/Dockerfile` |
| uv with Python 3.13 | `0.9.30-python3.13-bookworm-slim` | `ghcr.io/astral-sh/uv` | Base of `opensearch/tools/Dockerfile` and `security/image-monitor.Dockerfile` |

## Remote GPU host

Deployed separately with [`gpu-host/`](../gpu-host/README.md).

| Component | Version | Image | Used by (profile) |
| --- | --- | --- | --- |
| vLLM Semantic Router (CUDA) | `latest` | `ghcr.io/vllm-project/semantic-router/vllm-sr-cuda` | `d3-mini` |
| Text Embeddings Inference (CUDA) | `cuda-1.9.4` | `ghcr.io/huggingface/text-embeddings-inference` | `bge-m3` |
| nginx (unprivileged) | `1.29.5-alpine` | `docker.io/nginxinc/nginx-unprivileged` | `d3-tls` |

## Models

| Model | Revision | Loaded by |
| --- | --- | --- |
| `vllm-sr/d3-mini` | `a475367de0d8133a3c1a2a0dc12303d89cfa2ab4` (tag `v3.1.0`) | `d3-mini` on the remote GPU host |
| `BAAI/bge-m3` | `5617a9f61b028005a4858fdac845db406aefb181` | `bge-m3` on the remote GPU host |
| `spacy/en_core_web_lg` | Bundled in Presidio analyzer `2.2.362` | `presidio-analyzer` |

## Host tools

| Tool | Version | Needed for |
| --- | --- | --- |
| Docker Engine or Docker Desktop with Compose v2 | Compose `v2.20.0` or newer | Running the stack |
| Python | `3.11` or newer | `scripts/ci/validate.sh` and the maintenance scripts |
| uv | See [Maintenance runtime](maintenance-runtime.md) | Maintenance scripts with pinned dependencies |
| mkcert | Any current release | Local TLS certificates only |
