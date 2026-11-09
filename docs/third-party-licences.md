# Third-party licence register

This register is the authoritative licence record for the LiteLLM stack. It
covers every container image the stack runs or builds, the model weights it
loads, and the Python packages the repository itself installs. Generated SBOMs
(see [Generate SBOMs and licence evidence](image-security.md#generate-sboms-and-licence-evidence)) are
supporting evidence; they cannot replace this register because scanners do not
detect the licence of most single-binary applications (for example Grafana,
Loki, MinIO, Traefik, Qdrant, and ClickHouse).

`./scripts/maintenance.py doctor` and `scripts/ci/validate.sh` fail when a
Compose image, Dockerfile base image, auxiliary deployment image, or pinned
model revision is missing from this register, or when a listed image is no
longer used. Digest-only updates do not require an edit; a tag change does.
The same tag change also updates the
[component versions](component-versions.md) table.

## Usage basis

All statements below assume the current deployment model. Re-review the
register before any of these assumptions changes.

- The stack is self-hosted on the organisation's own Linux hosts with Docker
  Engine and the Docker CLI/Compose plugin.
- It is used only internally by the organisation. No paid or unpaid service is
  offered to external customers or other third parties.
- Container images are run unmodified as published. Only configuration files
  are mounted. The four local images are built from pinned upstream sources and
  are not distributed outside the organisation.
- No software or image from this stack is redistributed. A release bundle
  contains only this repository's own files.

Under these assumptions, no component requires a paid licence, a licence key,
or the display of licence notices in a user interface. Copyleft obligations
(GPL, AGPL) are triggered by distribution or, for AGPL, by modifying the
program and offering it to users over a network; neither applies today.

Licence categories: **permissive** (MIT, Apache-2.0, BSD, PostgreSQL, PSF),
**weak copyleft** (MPL-2.0, LGPL), **strong copyleft** (GPL, AGPL), and
**source-available** (licences that are not OSI open source).

## Runtime images

| Image | Services (profile) | Licence | Category | Obligations and notes |
| --- | --- | --- | --- | --- |
| `ghcr.io/berriai/litellm:v1.108.2` | `litellm-prod` (prod) | MIT; `enterprise/` code under the BerriAI commercial licence | Permissive, with commercial add-ons | No key is configured. SSO beyond 5 users, audit logs, key rotation, team/key policy attachment, secret managers, IP allow-lists, organisations, and `DOCS_*`/`EMAIL_*` branding need a LiteLLM Enterprise licence. `store_audit_logs: true` writes nothing without a licence. |
| `ghcr.io/berriai/litellm:v1.110.0-dev.1` | `litellm-dev` (dev; nightly) | Same as above | Permissive, with commercial add-ons | Same as above. |
| `litellm-pgvector:5bd8f3ab1fa9129e758a2c4b17ba1c6f3047ba24` | `litellm-pgvector` (dev) | MIT (`BerriAI/litellm-pgvector`), built on the Python base below | Permissive | Local build from upstream commit `5bd8f3ab`; the image carries OCI source, revision, and licence labels. |
| `litellm-lens:1.108.0-rc.1` | `lens` (lens) | MIT (`BerriAI/lens`; no `enterprise/` directory exists at the pinned commit), built on `cgr.dev/chainguard/wolfi-base` with Node.js (MIT) and Python 3.13 (PSF-2.0); bundled npm packages and Rust crates under their own licences | Permissive | Local build of upstream release-candidate commit `b2ac5f8a289c3f77f02b81d67ca1ee075bfead6d` (tag `v1.108.0-rc.1`) with the upstream `deploy/runtime/Dockerfile`, because Lens publishes no image yet; the image carries OCI source, revision, and licence labels. Investigations send selected trace content to the configured analysis model; see [LiteLLM Lens](lens.md#data-protection). |
| `litellm-opensearch-tools:0.1.0` | `opensearch-certs`, `opensearch-setup`, `opensearch-dashboards-setup`, `opensearch-bridge` (core) | Repository code under MIT; packages listed under [repository Python dependencies](#repository-python-dependencies) | Mixed; see below | Local build. Includes psycopg (LGPL-3.0); internal use only. |
| `litellm-image-security-monitor:0.1.0` | `image-security-monitor` (security) | Repository code under MIT plus Trivy (Apache-2.0), Docker CLI and buildx (Apache-2.0), uv (MIT OR Apache-2.0) | Permissive | Local build. |
| `docker.langfuse.com/langfuse/langfuse:4.72.0` | `langfuse-web` (langfuse) | MIT; `ee/` code under the Langfuse Enterprise licence | Permissive, with commercial add-ons | Enterprise features (project-level retention, SCIM, organisation APIs, fine-grained project access) need a licence key and are not used. |
| `docker.langfuse.com/langfuse/langfuse-worker:4.72.0` | `langfuse-worker` (langfuse) | Same as above | Permissive, with commercial add-ons | Same as above. |
| `clickhouse/clickhouse-server:26.3.62.3` | `langfuse-clickhouse`, `langfuse-clickhouse-observability-setup` (langfuse) | Apache-2.0 | Permissive | None. |
| `clickhouse/clickhouse-server:26.9.6.6` | `lens-clickhouse` (lens) | Apache-2.0 | Permissive | The version Lens pins at its release-candidate commit; kept separate from the Langfuse ClickHouse so each upgrades with its own application. |
| `cgr.dev/chainguard/minio:latest` | `langfuse-minio` (langfuse) | MinIO: AGPL-3.0; image distributed under Chainguard's free-tier terms | Strong copyleft | Unmodified internal use creates no AGPL obligation. Chainguard's free tier offers only the `latest` tag without support; pin by digest (done) and review the terms if a paid Chainguard or MinIO subscription is introduced. |
| `redis:7.4.12` | `langfuse-redis` (langfuse) | RSALv2 or SSPLv1 (dual) | Source-available | Internal use is permitted. Offering Redis itself as a service to third parties is not. Valkey (BSD-3-Clause) is a drop-in alternative if legal policy rejects source-available licences. |
| `postgres:17.11` | `grafana-db-init` (grafana); `langfuse-postgres`, `langfuse-preflight` (langfuse); `lens-preflight` (lens) | PostgreSQL Licence | Permissive | None. |
| `postgres:16.15-alpine3.24` | `postgres`, `postgres-init`, `pgvector-access-init`, `pgvector-migrate` (core); `litellm-demo-data-dev` (dev); `litellm-demo-data-prod` (demo) | PostgreSQL Licence | Permissive | None. |
| `pgvector/pgvector:0.8.7-pg16` | `vector-postgres` (core) | PostgreSQL Licence | Permissive | None. |
| `opensearchproject/opensearch:3.9.0` | `opensearch` (core) | Apache-2.0 | Permissive | The security plugin is included under Apache-2.0; no commercial licence exists or is needed. |
| `opensearchproject/opensearch-dashboards:3.9.0` | `opensearch-dashboards` (core) | Apache-2.0 | Permissive | None. |
| `opensearchproject/data-prepper:2.16.0` | `data-prepper` (core) | Apache-2.0 | Permissive | None. |
| `grafana/grafana:13.3.0` | `grafana` (grafana) | AGPL-3.0 | Strong copyleft | Unmodified internal use creates no obligation. Do not patch Grafana source and expose it to external users without legal review. |
| `grafana/loki:3.7.9` | `loki` (grafana) | AGPL-3.0 | Strong copyleft | Same as Grafana. |
| `grafana/alloy:v1.21.3` | `alloy` (core); `alloy-loki` (grafana) | Apache-2.0 | Permissive | None. |
| `prom/prometheus:v3.15.1` | `prometheus`, `prometheus-secrets-init` (core) | Apache-2.0 | Permissive | None. |
| `prom/node-exporter:v1.12.1` | `node-exporter` (core) | Apache-2.0 | Permissive | None. |
| `prom/blackbox-exporter:v0.29.1` | `blackbox` (core) | Apache-2.0 | Permissive | None. |
| `prometheuscommunity/postgres-exporter:v0.20.1` | `postgres-exporter`, `pgvector-exporter` (core) | Apache-2.0 | Permissive | None. |
| `gcr.io/cadvisor/cadvisor:v0.55.1` | `cadvisor` (core) | Apache-2.0 | Permissive | None. |
| `ghcr.io/tecnativa/docker-socket-proxy:v0.5.0` | `traefik-docker-proxy`, `alloy-docker-proxy` (core); `image-security-docker-proxy` (security) | Apache-2.0; bundles HAProxy (GPL-2.0-or-later) | Permissive, with GPL component | Unmodified internal use creates no GPL obligation. |
| `traefik:v3.7.17` | `traefik` (core) | MIT | Permissive | None. |
| `mcr.microsoft.com/presidio-analyzer:2.2.362` | `presidio-analyzer` (core) | MIT; includes the spaCy `en_core_web_lg` model (see [models](#models)) | Permissive | None. |
| `mcr.microsoft.com/presidio-anonymizer:2.2.362` | `presidio-anonymizer` (core) | MIT | Permissive | None. |
| `searxng/searxng:2026.10.30-ffa88e3ba` | `searxng` (core) | AGPL-3.0 | Strong copyleft | Unmodified internal use creates no AGPL obligation. The licence grants no right to use the queried search engines; see [external services](#external-services-and-terms) and [Search-engine terms of use](search-extraction-and-caching.md#search-engine-terms-of-use). |
| `ghcr.io/xberg-io/xberg:1.3.10-core` | `xberg` (prod, dev) | MIT | Permissive | None. |
| `ubuntu/bind9:9.20-26.04_stable` | `xberg-dns` (prod, dev) | BIND 9: MPL-2.0; Ubuntu base packages under their own licences | Weak copyleft | Unmodified internal use creates no MPL obligation. |
| `ubuntu/squid:6.13-25.04_edge` | `xberg-egress-proxy` (core) | Squid: GPL-2.0-or-later; Ubuntu base packages under their own licences | Strong copyleft | Unmodified internal use creates no GPL obligation. The `edge` track is not a long-term-support channel; this is a support concern, not a licence one. |
| `qdrant/qdrant:v1.20.0` | `qdrant` (dev) | Apache-2.0 | Permissive | None. |

## Build and auxiliary images

These images are not Compose services but are executed or embedded when the
stack is built, scanned, or extended.

| Image | Used by | Licence | Category | Obligations and notes |
| --- | --- | --- | --- | --- |
| `python:3.11-slim-bookworm` | Base of `pgvector/Dockerfile` | Python: PSF-2.0; Debian packages under their own licences | Permissive base, mixed OS packages | None for internal use. |
| `ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim` | Base of `opensearch/tools/Dockerfile` and `security/image-monitor.Dockerfile` | uv: MIT OR Apache-2.0; Python: PSF-2.0; Debian packages under their own licences | Permissive base, mixed OS packages | None for internal use. |
| `docker:29.9.2-cli` | Docker CLI and buildx copied into `security/image-monitor.Dockerfile` | Apache-2.0 | Permissive | None. |
| `aquasec/trivy:0.76.1` | Trivy copied into the monitor image; also run directly by host-mode scans | Apache-2.0 | Permissive | Trivy vulnerability and Java databases are downloaded at scan time under Aqua's database terms; internal use is permitted. |
| `ghcr.io/huggingface/text-embeddings-inference:cuda-1.9.4` | `gpu-host/compose.yml` (`bge-m3`, remote GPU host) | Text Embeddings Inference: Apache-2.0; bundles NVIDIA CUDA runtime libraries under the NVIDIA CUDA EULA | Permissive, with proprietary redistributable runtime | Using the bundled CUDA libraries is permitted without a fee. Re-review before redistributing the image. |
| `ghcr.io/vllm-project/semantic-router/vllm-sr-cuda:latest` | `gpu-host/compose.yml` (`d3-mini`, remote GPU host) | Apache-2.0; bundles NVIDIA CUDA runtime libraries under the NVIDIA CUDA EULA | Permissive, with proprietary redistributable runtime | Same CUDA note as Text Embeddings Inference. Pinned by digest despite the `latest` tag. |
| `cgr.dev/chainguard/wolfi-base` and `node:24` (build digests pinned upstream; runtime base digest overridden in `compose.optional.yml`) | Build and runtime bases of the upstream Lens `deploy/runtime/Dockerfile` at the pinned commit | Wolfi packages and Node.js under their own licences (Apache-2.0, MIT, and others) | Permissive bases, mixed OS packages | None for internal use. Not listed as rows the contracts check, because the Dockerfile is fetched from the pinned Lens commit rather than kept in this repository. |
| `docker.io/nginxinc/nginx-unprivileged:1.29.5-alpine` | `gpu-host/compose.yml` (`d3-tls`, D3 TLS terminator) | nginx: BSD-2-Clause; Alpine packages under their own licences | Permissive | None. |

## Models

Model weights are downloaded at runtime and are not part of any image SBOM.
Each model is pinned to an immutable revision.

| Model | Revision | Used by | Licence | Notes |
| --- | --- | --- | --- | --- |
| `BAAI/bge-m3` | `5617a9f61b028005a4858fdac845db406aefb181` | `gpu-host/compose.yml` (`bge-m3`) | MIT | This revision ships `pytorch_model.bin` (pickle format) rather than safetensors; load it only from the pinned revision. Text Embeddings Inference reads it with its own pickle parser, without executing Python code. |
| `vllm-sr/d3-mini` | `a475367de0d8133a3c1a2a0dc12303d89cfa2ab4` (tag `v3.1.0`) | `gpu-host/deploy.py` | Apache-2.0 | Fine-tuned from Qwen3.5-4B (Apache-2.0). Newer revisions exist; re-review the model card when updating the pin. |
| `spacy/en_core_web_lg` | Bundled in `mcr.microsoft.com/presidio-analyzer:2.2.362` at build time | `presidio-analyzer` | MIT | Trained partly on OntoNotes 5, which Explosion licenses commercially; the released model package is MIT. |

## Repository Python dependencies

| Package | Version | Used by | Licence | Notes |
| --- | --- | --- | --- | --- |
| `orjson` | 3.13.0 | `litellm-opensearch-tools` image (JSON log serialiser) | MPL-2.0 AND (Apache-2.0 OR MIT) | Used unmodified as a library; MPL-2.0 file-level obligations apply only on distribution of modified files. |
| `psycopg` | 3.3.6 | `litellm-opensearch-tools` image | LGPL-3.0-only | Used unmodified as a library; LGPL obligations apply only on distribution. |
| `psycopg-binary` | 3.3.6 | `litellm-opensearch-tools` image | LGPL-3.0-only; bundles libpq (PostgreSQL Licence) and OpenSSL (Apache-2.0) | Same as `psycopg`. |
| `python-json-logger` | 4.2.0 | `litellm-opensearch-tools` image (JSON log formatter) | BSD-2-Clause | None. |
| `pyyaml` | 6.0.3 | `litellm-opensearch-tools` image | MIT | None. |
| `tzdata` | 2026.6 | Windows only; never installed on Linux | Apache-2.0 | Not deployed. |
| `truststore` | 0.10.5 | `scripts/verify-litellm-upgrade.py`, `scripts/verify-pgvector.py` (host tooling) | MIT | Not deployed in any image. |

All other repository scripts use only the Python standard library.

## Documentation toolchain

`scripts/build-docs.py` installs these packages from the hash-locked
`docs-site/requirements.txt` into a local virtual environment to build the
[documentation site](documentation-site.md) with Zensical. They run only at
build time, on a workstation or CI runner, and are neither deployed with the
stack nor redistributed. The published site contains Zensical's theme
JavaScript and stylesheets (MIT) and only Material Design Icons (Apache-2.0)
plus the theme's own interface icons. It uses no Font Awesome icons or Twemoji
emoji, whose CC BY 4.0 licence would require attribution.

| Package | Version | Licence | Notes |
| --- | --- | --- | --- |
| `zensical` | 0.0.74 | MIT | Site generator; pre-1.0, pinned exactly (see [Maintenance](documentation-site.md#maintenance)). |
| `zensical-ui` | 0.0.41 | MIT | Theme templates and assets; bundles icon sets under their own licences (Material Design Icons: Apache-2.0; Lucide: ISC; Octicons: MIT; Simple Icons: CC0-1.0; Font Awesome: CC BY 4.0 and MIT). Only Material Design Icons are used. |
| `pymdown-extensions` | 12.3 | MIT | None. |
| `markdown` | 3.11.2 | BSD-3-Clause | None. |
| `jinja2` | 3.1.6 | BSD-3-Clause | None. |
| `markupsafe` | 3.0.4 | BSD-3-Clause | None. |
| `pygments` | 2.21.0 | BSD-2-Clause | None. |
| `click` | 8.5.0 | BSD-3-Clause | None. |
| `deepmerge` | 4.0.0 | MIT | None. |
| `pathspec` | 1.1.1 | MPL-2.0 | Used unmodified; MPL-2.0 file-level obligations apply only on distribution of modified files. |
| `pyyaml` | 6.0.3 | MIT | None. |
| `tomli` | 2.5.0 | MIT | None. |

The build also renders the diagrams with these tools, which are likewise
build-time only. The published site contains their output, not their code.

| Tool | Version | Licence | Notes |
| --- | --- | --- | --- |
| dashmotion `layout.py` and `check_diagram.py` | 2.2.4, commit `ae2fe4f` | MIT | Downloaded by `scripts/render-diagrams.py` and checked against pinned SHA-256 digests; not redistributed. |
| Graphviz (`dot`) | Debian bookworm package in CI | EPL-1.0 | Installed by `scripts/ci/docs.sh` in the CI image; a workstation uses its own. |

The CI jobs build the site in `ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm`
(uv: MIT OR Apache-2.0; Python: PSF-2.0; Debian packages under their own
licences), a CI tool image like the linters, not part of the stack.

## External services and terms

These are contractual terms rather than software licences, but they are
required for compliant operation.

| Service | Terms | Required action |
| --- | --- | --- |
| OpenAI API (`gpt-4o`, `gpt-4o-mini`) | OpenAI commercial API terms | An organisation API agreement and a data processing agreement for personal data. |
| Lens analysis models (optional) | The terms of the provider behind the configured LiteLLM gateway model | Only when `LENS_GATEWAY_API_KEY` is set: investigations send selected trace content to that model. Cover it with the same agreements as gateway traffic. |
| Search engines queried by SearXNG | Each engine's terms of service | Restrict engines, use contracted APIs, or formally accept the risk. See [Search-engine terms of use](search-extraction-and-caching.md#search-engine-terms-of-use). |
| Hugging Face Hub | Hugging Face terms of service and each model's licence | Download only the pinned revisions listed under [models](#models). |
| Docker Hub, GHCR, MCR, Chainguard, Langfuse registry, GitHub (Lens source) | Each registry's terms of service | Anonymous pulls are rate-limited on Docker Hub; authenticate pulls if limits are reached. |

## Review procedure

1. When Dependabot or an operator changes an image tag, model revision, or
   Python dependency, update the matching row in the same change.
2. Confirm the licence from the upstream `LICENSE` file or model card, and check
   the SBOM licence evidence under `security-reports/sbom/` for unexpected
   `forbidden`, `restricted`, `reciprocal`, or `unknown` findings. The generator
   prints these as `REVIEW:` lines.
3. Re-review the whole register when the [usage basis](#usage-basis) changes,
   when a LiteLLM or Langfuse licence key is introduced, and at least yearly.

| Date | Reviewer | Scope |
| --- | --- | --- |
| 2026-11-01 | Prepared by the stack maintainers; awaiting sign-off by the compliance owner | Initial register for repository baseline `cb9576b` |
