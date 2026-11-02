# Remote GPU host stack

This folder deploys the two models the LiteLLM stack calls on a separate GPU
host, sharing one GPU:

| Service | Engine | Model | Endpoint | Used by |
| --- | --- | --- | --- | --- |
| `d3-mini` + `d3-tls` | vLLM Semantic Router model runtime behind Nginx | `vllm-sr/d3-mini` (coding-tier decisions) | `https://<D3_SERVER_NAME>:8443`, verified TLS | `litellm-prod` and `litellm-dev` (`coding-router`) |
| `bge-m3` | Hugging Face Text Embeddings Inference (TEI) | `BAAI/bge-m3` (1,024-dimension dense embeddings) | `http://<host>:8080/v1`, plain HTTP | `litellm-dev` only (semantic cache, MCP tool filter, pgvector, Xberg auto-indexing) |

It is a separate Compose project from the main stack and never runs on the
LiteLLM hosts.

## Engine choice

- **D3-mini on the Semantic Router runtime.** D3 is a decision model with its
  own readout heads; only this runtime serves its Decisions and System One API.
  vLLM, SGLang, TGI, and Ollama cannot. The runtime enforces `D3_API_KEY` on
  every public route, and the Nginx proxy publishes only the three Decisions
  routes over TLS.
- **BGE-M3 on TEI.** TEI is built for embedding models: Flash Attention,
  token-budget batching, a small image, and no up-front GPU memory reservation.
  vLLM, the previous engine, reserves a fixed share of the card and leaves its
  tokenizer, pooling, and metrics routes outside the API key. TEI's key guards
  every compute route, including `/invocations`; only `/health`, `/ping`,
  `/metrics`, and `/docs` answer without it, so no proxy is needed. TEI serves
  BGE-M3's dense vectors, which are the only output the stack uses. The
  `cuda-1.9.4` image selects the right kernels for Turing through Blackwell GPUs
  at start-up.

## GPU memory on one 48 GB card

Neither engine reserves memory up front; both grow to their working set.
Figures for D3-mini are the runtime's published measurements.

| Workload | Typical | Worst case |
| --- | --- | --- |
| D3-mini, inputs capped at 8,192 tokens | ~12 GiB | ~18 GiB |
| BGE-M3 on TEI, `BGE_M3_MAX_BATCH_TOKENS=16384` | ~2–3 GiB | ~4 GiB |
| CUDA contexts of both processes | ~1 GiB | ~1 GiB |
| **Total** | **~15 GiB** | **~23 GiB** |

That leaves more than 20 GB of headroom. The larger `d3-flash` model (about
19 GiB at 8.5k tokens) would also fit if it routes better on your prompts; the
full `d3` model (50–62 GiB) does not. Raising `BGE_M3_MAX_BATCH_TOKENS` raises
TEI's peak roughly in proportion.

Host sizing: 16 CPU cores, 64 GB RAM, 1 TB NVMe for model caches, the NVIDIA
driver, Docker Engine with the Compose plugin, and the NVIDIA Container Toolkit.

## Deploy

On the GPU host, from a checkout of this repository:

```sh
cd gpu-host
install -m 600 .env.example .env
# Set D3_SERVER_NAME, the TLS files, and two different keys:
#   D3_API_KEY=$(openssl rand -hex 32)
#   BGE_M3_API_KEY=$(openssl rand -hex 32)
./deploy.py
```

`deploy.py` needs Python 3.9+, Docker, and `curl`. It:

1. validates `.env` (private file permissions, URL-safe keys of at least 32
   characters, distinct keys, hostnames, ports, sizes);
2. renders `state/` with mode `0700`/`0600`: the D3 runtime configuration with
   its key, the TLS files, TEI's key as an environment file, and the probe
   headers, so no key appears on a command line;
3. starts the Compose project; models download into `state/` on first start;
4. succeeds only after D3 refuses an unauthenticated request and returns a
   valid coding tier, and TEI refuses an unauthenticated request and returns a
   1,024-dimension embedding.

`./deploy.py --render-only` validates and renders without starting anything.
Run Compose commands with both environment files:

```sh
docker compose --env-file .env --env-file state/compose.env ps
docker compose --env-file .env --env-file state/compose.env logs -f bge-m3
docker compose --env-file .env --env-file state/compose.env down
```

Then set on the LiteLLM hosts:

| Variable | Value | Hosts |
| --- | --- | --- |
| `D3_DECISION_API_BASE` | `https://<D3_SERVER_NAME>:<D3_PUBLIC_PORT>` | prod and dev |
| `D3_DECISION_API_KEY` | `D3_API_KEY` | prod and dev |
| `D3_DECISION_CA_BUNDLE` | public roots plus the CA that issued D3's certificate | prod and dev |
| `BGE_M3_API_BASE` | `http://<gpu-host>:<BGE_M3_PUBLIC_PORT>/v1` | dev |
| `BGE_M3_API_KEY` | `BGE_M3_API_KEY` | dev (any placeholder on prod) |

## Network and security

- Firewall `D3_PUBLIC_PORT` to both LiteLLM hosts and `BGE_M3_PUBLIC_PORT` to
  the LiteLLM dev host. Nothing else should reach either port.
- **BGE-M3 uses plain HTTP by operator decision.** Its key and the embedded
  text, including dev request text, cross the network unencrypted. Keep the
  hosts on one private segment and rotate `BGE_M3_API_KEY` if the network may be
  shared. Add TLS before production uses BGE-M3. See
  [Plain HTTP: accepted risk](../docs/search-extraction-and-caching.md#plain-http-accepted-risk).
- Every container runs as the deploying user with a read-only root
  filesystem, no capabilities, and `no-new-privileges`.
- Images and model revisions are pinned in `compose.yml` and `deploy.py` and
  recorded in the [licence register](../docs/third-party-licences.md); the
  repository's maintenance contracts fail if they drift apart.
- The main stack's image scanner does not see this host. Scan the pinned
  images in the registry or on this host.
- Monitor GPU memory, utilization, temperature, container health, and D3's
  certificate expiry on this host; the main stack does not scrape it.

## Troubleshooting

- **TEI exits with a write error under `/etc/ld.so.conf.d`.** Some container
  runtimes mount the driver under `/usr/local/nvidia`, and TEI's entrypoint then
  updates the linker cache. Use the standard NVIDIA Container Toolkit, which
  injects the driver libraries without that step.
- **TEI rejects an input over 8,192 tokens with a validation error.**
  Truncation is disabled on purpose so a long prompt cannot match a cached
  answer for a shared prefix.
