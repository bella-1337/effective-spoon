# Search, document extraction, and caching

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

Commands that use hostname variables such as `$LITELLM_PROD_HOST` expect them
to be loaded first with `eval "$(python3 scripts/maintenance.py stack-env)"`;
see [Public hostnames and `STACK_DOMAIN`](getting-started.md#public-hostnames-and-stack_domain).

## Use SearXNG search through LiteLLM

Both LiteLLM profiles expose the internal SearXNG service as the native
`searxng-search` search tool. SearXNG is internal-only in every profile: it is
attached only to the internal Compose network, has no Traefik route, and
publishes no host port. Clients authenticate to LiteLLM, which is the only way
to reach it; there is no public search UI.

Test production search with:

```sh
curl --fail --show-error --silent \
  "https://$LITELLM_PROD_HOST/v1/search/searxng-search" \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"query":"LiteLLM SearXNG integration","max_results":5}'
```

Use `https://$LITELLM_DEV_HOST` to test the development profile. SearXNG
returns a fixed page of results, so `max_results` may not reduce the number of
results returned by the upstream service.

### Search-engine terms of use

SearXNG itself is AGPL-3.0 and free to run internally unmodified, but it grants
no right to use the search engines it queries. [`searxng/settings.yml`](../searxng/settings.yml)
sets `use_default_settings: true`, so the image's default engine set is active
(for example DuckDuckGo, Brave, Wikipedia, and Bing and Google news/media
engines). SearXNG obtains those results by scraping the public web pages, not
through a licensed API. Most commercial engines' terms of service prohibit
automated querying and reuse of results, and this deployment additionally
sends traffic from a single egress IP on behalf of every LiteLLM user and
feeds results to LLMs. Consequences:

- Using the default engines is not covered by any agreement; treat it as a
  legal and compliance risk to be accepted or mitigated, not as licensed.
- Engines can rate-limit, CAPTCHA, or block the egress IP without notice, so
  search results can degrade or disappear silently.
- Search terms leave the organisation and are sent to each queried engine.

Before production use, have the decision recorded by the responsible owner and
choose one of these options:

1. Restrict SearXNG to sources whose terms allow automated use (for example
   Wikipedia/Wikidata and other open-data or internal sources) by replacing
   `use_default_settings: true` with an explicit `use_default_settings.engines.keep_only`
   list.
2. Use engines through their contracted APIs (for example a paid Brave Search
   or Google Programmable Search API key) under the provider's commercial terms.
3. Formally accept the residual risk of scraping the default engines for
   internal use.

Review this decision whenever the SearXNG image or its default engine list
changes.

The beta LiteLLM Chat UI is enabled only for the development profile at
`https://$LITELLM_DEV_HOST/ui/chat`. Production keeps the Chat UI disabled.
Users must sign in and can only select models allowed by their LiteLLM access
policy. The native `searxng-search` endpoint is available through both gateways;
it is separate from the Chat UI's MCP tool picker.

## Use Xberg MCP document extraction

Both LiteLLM profiles register the internal Xberg Streamable HTTP MCP server as
`xberg`. The container has no published host port and LiteLLM is the
authentication boundary. Only the `extract`, `detect_mime_type`, `get_version`,
and `list_formats` tools are exposed; cache mutation and warming tools are
intentionally excluded.

List the available tools:

```sh
curl --fail --show-error --silent \
  "https://$LITELLM_PROD_HOST/mcp-rest/tools/list?server_id=xberg" \
  -H "x-litellm-api-key: ******"
```

Extract the official Xberg Docker guide through LiteLLM:

```sh
curl --fail --show-error --silent \
  "https://$LITELLM_PROD_HOST/mcp-rest/tools/call" \
  -H "x-litellm-api-key: ******" \
  -H "Content-Type: application/json" \
  -d '{
    "server_id": "xberg",
    "name": "extract",
    "arguments": {
      "input": {
        "kind": "uri",
        "uri": "https://docs.xberg.io/guides/docker/"
      }
    }
  }'
```

Use `https://$LITELLM_DEV_HOST` for the development profile. Xberg accepts
remote URLs and therefore acts as a server-side fetcher. Grant MCP access only
to trusted keys and teams. Xberg has no direct route to the Internet or the
stack's `internal` network:

- outbound traffic must be an HTTPS `CONNECT` tunnel through the Squid proxy,
  which denies plaintext HTTP and private, loopback, link-local, and
  non-allowlisted destinations before resolving them;
- the `xberg-dns` resolver answers `NXDOMAIN` for every name that is not
  explicitly forwarded, so a URL cannot leak data through DNS lookups;
- local paths and `file://` URIs are disabled in `security/xberg-config.toml`;
  send local documents as `kind: bytes` instead;
- a default-on `prompt-injection-tool-output` guardrail redacts explicit
  injection phrases, such as "ignore all previous instructions", from tool
  results.

The secure default permits only the documented Xberg documentation host. To add
a destination, update all of `security/xberg-egress-allowlist.txt`,
`security/xberg-config.toml`, the forward zones in
`security/xberg-dns-zones.conf`, and the delegations in
`security/xberg-dns-deny-root.zone`, including every CNAME target (check with
`dig +noall +answer <host>`). `python3 scripts/maintenance.py doctor` rejects
lists that are out of step. See
[Edge and abuse protection](edge-and-abuse-protection.md).

The development gateway uses `BAAI/bge-m3` on the remote GPU host (see
[Remote BGE-M3 embeddings](#remote-bge-m3-embeddings)) to semantically select up
to three relevant MCP tools with a minimum similarity score of `0.3`.
This affects MCP tools offered automatically through Chat Completions and the
Responses API; it does not change MCP permissions or direct
`/mcp-rest/tools/call` requests. Production keeps semantic filtering disabled
because the embedding model is intentionally development-only.

Both LiteLLM profiles register a remote `vllm-sr/d3-mini` deployment as a native
Decisions model. The `coding-router` Auto Router sends only the classification
request to D3, then selects `gpt-4o-mini` for SIMPLE/MEDIUM work and `gpt-4o` for
COMPLEX/REASONING work. If the classifier is unavailable or times out after 15
seconds, routing fails safe to `gpt-4o`. BGE-M3 is a separate remote pooling
service; embeddings never pass through D3.

Deploy D3-mini on the separate NVIDIA GPU host before starting this stack, with
the [`gpu-host/`](../gpu-host/README.md) Compose project, which also serves
BGE-M3. It needs Docker Compose, one CUDA-capable GPU, a DNS name, an
operator-provided TLS certificate and key, and a random API key of at least 32
characters:

```sh
cd gpu-host
install -m 600 .env.example .env   # set D3_SERVER_NAME, TLS files, and keys
./deploy.py
```

D3-mini runs on the vLLM Semantic Router model runtime behind an unprivileged
Nginx TLS proxy, both pinned by digest. The model revision is pinned, model
downloads persist, only the three Decisions routes are published, and the
deployment checks a real four-tier decision before reporting success. Restrict
the port at the firewall to the LiteLLM hosts. Copy the endpoint and the same
secret to `D3_DECISION_API_BASE` and `D3_DECISION_API_KEY` in this stack's
`.env`.
Create `D3_DECISION_CA_BUNDLE` as a combined PEM containing the normal public
root bundle plus the CA that issued D3's certificate. LiteLLM uses that combined
bundle for both D3 and completion-provider TLS verification.

```sh
docker compose --profile dev up -d
```

Call the router through the normal LiteLLM hostname and credential:

```sh
curl --fail --silent "https://$LITELLM_DEV_HOST/v1/chat/completions" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "coding-router",
    "messages": [{
      "role": "user",
      "content": "Analyze this architecture and compare its tradeoffs."
    }],
    "temperature": 0
  }'
```

LiteLLM remains the only client-facing authentication, policy, guardrail,
provider, spend, and observability boundary. The remote listener uses a separate
bearer credential, TLS 1.2+, and a narrow route allowlist. Neither endpoint logs
prompt content by default. Validate D3's coding-tier accuracy on representative
internal prompts before relying on cost savings.

## Remote BGE-M3 embeddings

The development profile's embeddings (MCP tool filtering, the semantic cache,
the `sme-knowledge` store, and Xberg auto-indexing) come from `BAAI/bge-m3` on
the remote GPU host. Production does not use BGE-M3. The
[`gpu-host/`](../gpu-host/README.md) stack serves it with Hugging Face Text
Embeddings Inference next to D3-mini on the same GPU; that README explains the
engine choice and the 48 GB memory budget.

TEI's image and the model revision are pinned, the container runs unprivileged
on a read-only root filesystem, and its API key guards every compute route,
including `/v1/embeddings` and `/invocations`. Only `/health`, `/ping`,
`/metrics`, and `/docs` answer without it. Inputs over 8,192 tokens are
rejected rather than truncated, so a long prompt cannot match a cached answer
for a shared prefix. The deployment reports success only after an
unauthenticated request is refused and a real embedding has 1,024 dimensions.

Set `BGE_M3_API_BASE` (for example `http://bge-m3.${STACK_DOMAIN}:8080/v1`) and
the same `BGE_M3_API_KEY` in the dev host's `.env`. LiteLLM's `hosted_vllm`
provider is its generic OpenAI-compatible adapter and works with TEI.

### Plain HTTP: accepted risk

By operator decision the endpoint uses plain HTTP, unlike D3, which keeps
verified TLS. Anyone who can observe or intercept traffic between the dev host
and the GPU host can read the API key and the embedded text, and can alter
embeddings. The embedded text includes the user's request text for MCP tool
filtering, prompts that opt in to the semantic cache, and Xberg chunks, which
Presidio anonymizes before indexing. Compensating controls:

- only the dev profile calls BGE-M3, and production traffic never does;
- keep both hosts on the same private network segment, never route the port
  over the Internet, and firewall it to the LiteLLM dev host;
- TEI enforces the API key on every compute route;
- rotate `BGE_M3_API_KEY` if the network is shared or the key may have been
  observed.

Switch to TLS before promoting BGE-M3 to production
([D2P-008](dev-to-prod-promotion-register.md#d2p-008---bge-m3-embedding-service))
or before the network between the hosts becomes untrusted. Only
`BGE_M3_API_BASE` changes on the LiteLLM side.

## Development semantic response cache

The development profile uses an authenticated, internal Qdrant service for an
opt-in semantic response cache. LiteLLM embeds request messages with the remote
`bge-m3` model and reuses a previous complete response only when cosine
similarity is at least `0.92`. A cache hit does not call the configured
generation model.

The cache is intentionally:

- development-only and disabled by default for each request;
- limited to asynchronous Chat Completions calls;
- scoped by the authenticated end-user identity in addition to the LiteLLM
  virtual key, team, and organization;
- stored on a bounded 1 GB `tmpfs`, so stopping or recreating Qdrant deletes all
  cached prompts and responses.

Set `QDRANT_API_KEY` to a separate random secret. Opt in only for single-turn,
non-sensitive requests and send a stable authenticated end-user identifier:

```sh
curl --fail --silent "https://$LITELLM_DEV_HOST/v1/chat/completions" \
  -H "Authorization: ******" \
  -H "x-litellm-customer-id: example-user-123" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "What is LiteLLM?"}],
    "cache": {"use-cache": true}
  }'
```

Do not opt in multi-turn conversations, tool-calling or agent loops,
permission-sensitive RAG, personalized responses, or time-sensitive queries.
Semantically similar text can have materially different answers, and a false
match replays the earlier answer without calling the model.

After the remote BGE-M3 and D3-mini endpoints are healthy, run the end-to-end verification:

```sh
./scripts/verify-semantic-cache.py
```

The check authenticates to Qdrant, sends two different but semantically similar
single-turn prompts, and requires the second request to replay the first
response without creating another cached point. Semantic-cache hits also carry
the `x-litellm-semantic-similarity` response header for clients that inspect
response headers.
