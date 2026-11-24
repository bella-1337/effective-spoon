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
| LiteLLM (production, LTS) | `v1.108.9` | `ghcr.io/berriai/litellm` | `litellm-prod` (prod) |
| LiteLLM (development, nightly) | `v1.111.0-dev.4` | `ghcr.io/berriai/litellm` | `litellm-dev` (dev) |
| Traefik | `v3.7.19` | `traefik` | `traefik` (core) |
| Docker socket proxy | `v0.5.0` | `ghcr.io/tecnativa/docker-socket-proxy` | `traefik-docker-proxy`, `alloy-docker-proxy` (core); `image-security-docker-proxy` (security) |

## Guardrails, search, and extraction

| Component | Version | Image | Used by (profile) |
| --- | --- | --- | --- |
| Presidio analyzer, English, German, and French (local build) | `2.2.362-1` | `litellm-presidio-analyzer` | `presidio-analyzer` (core) |
| Presidio anonymizer | `2.2.362` | `mcr.microsoft.com/presidio-anonymizer` | `presidio-anonymizer` (core) |
| SearXNG | `2026.11.15-2d500cb6b` | `searxng/searxng` | `searxng` (core) |
| Xberg | `1.4.2-core` | `ghcr.io/xberg-io/xberg` | `xberg` (prod, dev) |
| BIND 9 (Xberg DNS) | `9.20-26.04_stable` | `ubuntu/bind9` | `xberg-dns` (prod, dev) |
| Squid (Xberg egress proxy) | `7.2-26.04_edge` | `ubuntu/squid` | `xberg-egress-proxy` (core) |

## Data stores

| Component | Version | Image | Used by (profile) |
| --- | --- | --- | --- |
| PostgreSQL 16 | `16.16-alpine3.24` | `postgres` | `postgres`, `postgres-init`, `pgvector-access-init`, `pgvector-migrate` (core); `litellm-demo-data-dev` (dev); `litellm-demo-data-prod` (demo) |
| PostgreSQL 16 + pgvector | `0.8.7-pg16` | `pgvector/pgvector` | `vector-postgres` (core) |
| LiteLLM pgvector connector (local build) | `5bd8f3ab1fa9129e758a2c4b17ba1c6f3047ba24` | `litellm-pgvector` | `litellm-pgvector` (dev) |
| Qdrant | `v1.20.1` | `qdrant/qdrant` | `qdrant` (dev) |

## Observability

| Component | Version | Image | Used by (profile) |
| --- | --- | --- | --- |
| OpenSearch | `3.9.0` | `opensearchproject/opensearch` | `opensearch` (core) |
| OpenSearch Dashboards | `3.9.0` | `opensearchproject/opensearch-dashboards` | `opensearch-dashboards` (core) |
| Data Prepper | `2.17.0` | `opensearchproject/data-prepper` | `data-prepper` (core) |
| OpenSearch tools (local build) | `0.1.0` | `litellm-opensearch-tools` | `opensearch-certs`, `opensearch-setup`, `opensearch-dashboards-setup`, `opensearch-bridge` (core) |
| Prometheus | `v3.16.0` | `prom/prometheus` | `prometheus`, `prometheus-secrets-init` (core) |
| Grafana Alloy | `v1.22.0` | `grafana/alloy` | `alloy` (core); `alloy-loki` (grafana) |
| Blackbox exporter | `v0.29.1` | `prom/blackbox-exporter` | `blackbox` (core) |
| Node exporter | `v1.12.1` | `prom/node-exporter` | `node-exporter` (core) |
| cAdvisor | `v0.55.1` | `gcr.io/cadvisor/cadvisor` | `cadvisor` (core) |
| PostgreSQL exporter | `v0.20.1` | `prometheuscommunity/postgres-exporter` | `postgres-exporter`, `pgvector-exporter` (core) |
| Grafana | `13.3.1` | `grafana/grafana` | `grafana` (grafana) |
| Loki | `3.7.10` | `grafana/loki` | `loki` (grafana) |

## Langfuse

| Component | Version | Image | Used by (profile) |
| --- | --- | --- | --- |
| Langfuse web | `4.80.0` | `docker.langfuse.com/langfuse/langfuse` | `langfuse-web` (langfuse) |
| Langfuse worker | `4.80.0` | `docker.langfuse.com/langfuse/langfuse-worker` | `langfuse-worker` (langfuse) |
| PostgreSQL 17 | `17.12` | `postgres` | `langfuse-postgres`, `langfuse-preflight` (langfuse); `grafana-db-init` (grafana); `lens-preflight` (lens); `d3-mini-preflight` (d3) |
| ClickHouse | `26.3.64.3` | `clickhouse/clickhouse-server` | `langfuse-clickhouse`, `langfuse-clickhouse-observability-setup` (langfuse) |
| Redis | `7.4.12` | `redis` | `langfuse-redis` (langfuse) |
| MinIO (Chainguard) | `latest` | `cgr.dev/chainguard/minio` | `langfuse-minio` (langfuse) |

## LiteLLM Lens

| Component | Version | Image | Used by (profile) |
| --- | --- | --- | --- |
| LiteLLM Lens (local build) | `1.108.0-rc.1` | `litellm-lens` | `lens` (lens) |
| ClickHouse | `26.9.29.5` | `clickhouse/clickhouse-server` | `lens-clickhouse` (lens) |

Lens publishes no release image yet. The stack builds the upstream
`deploy/runtime/Dockerfile` from the pinned release-candidate commit
`b2ac5f8a289c3f77f02b81d67ca1ee075bfead6d` (tag `v1.108.0-rc.1`). Its build
images are pinned by digest upstream; the runtime base is overridden with a
newer `cgr.dev/chainguard/wolfi-base` digest in `compose.optional.yml`. See
[LiteLLM Lens](lens.md#upgrade-lens).

## On-host D3-mini

| Component | Version | Image | Used by (profile) |
| --- | --- | --- | --- |
| vLLM (CUDA 13.0) | `v0.33.0` | `vllm/vllm-openai` | `d3-mini`, `d3-mini-download` (d3) |

The first vLLM release that serves Decision 3.0 models. Its image needs NVIDIA
driver 580 or newer. `d3-mini-preflight` reuses the `postgres:17.12` image
listed under Langfuse. See [On-host D3-mini](d3-mini-on-host.md).

## Image security

| Component | Version | Image | Used by (profile) |
| --- | --- | --- | --- |
| Image-security monitor (local build) | `0.1.0` | `litellm-image-security-monitor` | `image-security-monitor` (security) |
| Trivy | `0.76.2` | `aquasec/trivy` | Copied into the monitor image; host-mode scans |
| Docker CLI and buildx | `29.9.3-cli` | `docker` | Copied into the monitor image |

## Documentation site

| Component | Version | Image | Used by (profile) |
| --- | --- | --- | --- |
| Documentation site (local build, Zensical serve) | `0.1.0` | `litellm-stack-docs` | `docs` (docs), built on the uv image listed under [Build base images](#build-base-images) with the Zensical toolchain pinned in `docs-site/requirements.txt` |

## Build base images

| Component | Version | Image | Used by (profile) |
| --- | --- | --- | --- |
| Python | `3.11-slim-bookworm` | `python` | Base of `pgvector/Dockerfile` |
| Presidio analyzer (upstream) | `2.2.362` | `mcr.microsoft.com/presidio-analyzer` | Base of `presidio/Dockerfile` |
| uv with Python 3.13 | `0.9.30-python3.13-bookworm-slim` | `ghcr.io/astral-sh/uv` | Base of `opensearch/tools/Dockerfile`, `security/image-monitor.Dockerfile`, and `docs-site/Dockerfile` |

## Remote GPU host

Deployed separately with [`gpu-host/`](../gpu-host/README.md).

| Component | Version | Image | Used by (profile) |
| --- | --- | --- | --- |
| vLLM Semantic Router (CUDA) | `latest` | `ghcr.io/vllm-project/semantic-router/vllm-sr-cuda` | `d3-mini` |
| Text Embeddings Inference (CUDA) | `cuda-1.9.4` | `ghcr.io/huggingface/text-embeddings-inference` | `bge-m3` |
| nginx (unprivileged) | `1.31.7-alpine` | `docker.io/nginxinc/nginx-unprivileged` | `d3-tls` |

## Models

| Model | Revision | Loaded by |
| --- | --- | --- |
| `vllm-sr/d3-mini` | `a475367de0d8133a3c1a2a0dc12303d89cfa2ab4` (tag `v3.1.0`) | `d3-mini` on the remote GPU host; `d3-mini` (d3 profile) |
| `BAAI/bge-m3` | `5617a9f61b028005a4858fdac845db406aefb181` | `bge-m3` on the remote GPU host |
| `spacy/en_core_web_lg` | Bundled in Presidio analyzer `2.2.362` | `presidio-analyzer` |
| `spacy/de_core_news_lg` | `3.8.0` (`presidio/spacy-models.txt`) | `presidio-analyzer` |
| `spacy/fr_core_news_lg` | `3.8.0` (`presidio/spacy-models.txt`) | `presidio-analyzer` |
| `pii-assist` model (your vLLM server, outside the stack) | As served under `PII_ASSIST_VLLM_MODEL` (default `gemma4`); the revision is pinned by the operator | `pii-assist` model of both gateways, when `PII_ASSIST_API_BASE` is set |

## Host tools

| Tool | Version | Needed for |
| --- | --- | --- |
| Docker Engine or Docker Desktop with Compose v2 | Compose `v2.20.0` or newer | Running the stack |
| Python | `3.11` or newer | `scripts/ci/validate.sh` and the maintenance scripts |
| uv | See [Maintenance runtime](maintenance-runtime.md) | Maintenance scripts with pinned dependencies |
| mkcert | Any current release | Local TLS certificates only |
| Graphviz | Any current release with `dot -Tjson` | Rendering `diagrams/*.mmd` locally; CI installs it for the documentation build |
