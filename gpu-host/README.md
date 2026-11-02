# Remote GPU host stack

This folder deploys the two models the LiteLLM stack calls on a separate GPU
host, sharing one GPU. Both are reachable only through the `tls` Nginx proxy,
which presents one certificate issued by this host's dedicated mkcert CA:

| Service | Engine | Model | Endpoint | Used by |
| --- | --- | --- | --- | --- |
| `d3-mini` | vLLM Semantic Router model runtime | `vllm-sr/d3-mini` (coding-tier decisions) | `https://<D3_SERVER_NAME>:8443` | `litellm-prod` and `litellm-dev` (`coding-router`) |
| `bge-m3` | Hugging Face Text Embeddings Inference (TEI) | `BAAI/bge-m3` (1,024-dimension dense embeddings) | `https://<BGE_M3_SERVER_NAME>:9443/v1` | `litellm-dev` only (semantic cache, MCP tool filter, pgvector, Xberg auto-indexing) |

The proxy publishes only the three Decisions routes for D3 and only
`/v1/embeddings` and `/health` for BGE-M3, accepts TLS 1.2 (forward-secret AEAD
suites) and 1.3, and resolves each upstream per request, so one model starting
or failing never blocks the other's endpoint. This is a separate Compose project
from the main stack and never runs on the LiteLLM hosts.

## Engine choice

- **D3-mini on the Semantic Router runtime.** D3 is a decision model with its
  own readout heads; only this runtime serves its Decisions and System One API.
  vLLM, SGLang, TGI, and Ollama cannot. The runtime enforces `D3_API_KEY` on
  every public route.
- **BGE-M3 on TEI.** TEI is built for embedding models: Flash Attention,
  token-budget batching, a small image, and no up-front GPU memory reservation.
  vLLM, the previous engine, reserves a fixed share of the card and leaves its
  tokenizer, pooling, and metrics routes outside the API key. TEI's key guards
  every compute route, including `/invocations`; only `/health`, `/ping`,
  `/metrics`, and `/docs` answer without it, and the proxy does not publish
  those last three. Neither engine terminates TLS itself. TEI serves
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

`deploy.py` needs Python 3.9+, Docker, `curl`, `openssl`, and
[`mkcert`](https://github.com/FiloSottile/mkcert). It:

1. validates `.env` (private file permissions, URL-safe keys of at least 32
   characters, distinct keys, hostnames, ports, sizes);
2. renders `state/` with mode `0700`/`0600`: the D3 runtime configuration with
   its key, TEI's key as an environment file, and the probe headers, so no key
   appears on a command line;
3. issues the TLS certificate for `D3_SERVER_NAME`, `BGE_M3_SERVER_NAME`, and
   any `TLS_EXTRA_NAMES` from a dedicated mkcert CA in `state/mkcert/`. mkcert
   creates that CA on first use and never installs it in this host's trust
   store. The certificate is re-issued only when the names change, it expires
   within `TLS_RENEW_BEFORE_DAYS`, or it no longer verifies against the CA;
4. starts the Compose project (models download into `state/` on first start)
   and reloads the proxy when the certificate changed;
5. succeeds only after both endpoints verify against the mkcert CA, refuse an
   unauthenticated request, and answer a real one: a valid coding tier from D3
   and a 1,024-dimension embedding from BGE-M3.

`./deploy.py --render-only` validates and renders without starting anything.
Run Compose commands with both environment files:

```sh
docker compose --env-file .env --env-file state/compose.env ps
docker compose --env-file .env --env-file state/compose.env logs -f bge-m3
docker compose --env-file .env --env-file state/compose.env down
```

## Trust the CA on the LiteLLM hosts

LiteLLM verifies both endpoints, and every completion provider, against the one
bundle named by `SSL_CERT_FILE` in its containers. On each LiteLLM host:

1. Copy `gpu-host/state/mkcert/rootCA.pem` from the GPU host. It is the public
   CA certificate only. **Never copy `rootCA-key.pem`**; it stays on the GPU
   host.
2. From the repository root, build the bundle of the operating system's public
   roots plus that CA:

   ```sh
   ./scripts/build-gpu-host-ca-bundle.py --root-ca /path/to/rootCA.pem
   ```

   It refuses a file that contains a private key or is not a CA certificate, and
   writes `traefik/certs/gpu-host-ca-bundle.pem` (ignored by Git). Pass
   `--public-roots` if the host has no standard root bundle.
3. Create DNS records pointing `D3_SERVER_NAME` and `BGE_M3_SERVER_NAME` at the
   GPU host's private IP. The LiteLLM containers resolve names through DNS;
   the LiteLLM host's `/etc/hosts` is not visible inside them. Then set:

| Variable | Value | Hosts |
| --- | --- | --- |
| `GPU_HOST_CA_BUNDLE` | `./traefik/certs/gpu-host-ca-bundle.pem` | prod and dev |
| `D3_DECISION_API_BASE` | `https://<D3_SERVER_NAME>:<D3_PUBLIC_PORT>` | prod and dev |
| `D3_DECISION_API_KEY` | `D3_API_KEY` | prod and dev |
| `BGE_M3_API_BASE` | `https://<BGE_M3_SERVER_NAME>:<BGE_M3_PUBLIC_PORT>/v1` | dev |
| `BGE_M3_API_KEY` | `BGE_M3_API_KEY` | dev (any placeholder on prod) |

4. Recreate the proxies: `docker compose --profile prod --profile dev up -d
   --force-recreate litellm-prod litellm-dev`.

Leaf certificate renewals need no change on the LiteLLM hosts. Rebuild the
bundle only when the CA changes.

## Network and security

- Firewall `D3_PUBLIC_PORT` to both LiteLLM hosts and `BGE_M3_PUBLIC_PORT` to
  the LiteLLM dev host. Nothing else should reach either port.
- **The mkcert CA is trusted for every hostname.** The LiteLLM containers use
  the bundle for all outbound TLS, and mkcert CAs carry no name constraints, so
  whoever holds `state/mkcert/rootCA-key.pem` could impersonate any server to
  LiteLLM, including the completion providers. Keep `state/` private to the
  deploying account, include it only in encrypted backups, and never copy the
  key. If it may have leaked, rotate the CA: stop the stack, delete
  `state/mkcert/` and `state/tls/`, run `./deploy.py`, and rebuild the bundle on
  every LiteLLM host.
- mkcert is meant for private networks. Use a certificate from your
  organisation's CA instead if the GPU host must be reachable more widely.
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
- **LiteLLM reports `CERTIFICATE_VERIFY_FAILED`.** The bundle does not contain
  the current mkcert CA, or `GPU_HOST_CA_BUNDLE` points elsewhere. Rebuild it
  and recreate the proxies. A hostname mismatch means the name in
  `D3_DECISION_API_BASE` or `BGE_M3_API_BASE` is missing from the certificate;
  add it to `TLS_EXTRA_NAMES` and run `./deploy.py`.
- **TEI rejects an input over 8,192 tokens with a validation error.**
  Truncation is disabled on purpose so a long prompt cannot match a cached
  answer for a shared prefix.
