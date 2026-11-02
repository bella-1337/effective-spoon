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

The development gateway uses `BAAI/bge-m3` in the internal `bge-m3-vllm`
container to semantically select up to three relevant MCP tools with a minimum
similarity score of `0.3`.
This affects MCP tools offered automatically through Chat Completions and the
Responses API; it does not change MCP permissions or direct
`/mcp-rest/tools/call` requests. Production keeps semantic filtering disabled
because the local model containers are intentionally development-only.

Both LiteLLM profiles register a remote `vllm-sr/d3-mini` deployment as a native
Decisions model. The `coding-router` Auto Router sends only the classification
request to D3, then selects `gpt-4o-mini` for SIMPLE/MEDIUM work and `gpt-4o` for
COMPLEX/REASONING work. If the classifier is unavailable or times out after 15
seconds, routing fails safe to `gpt-4o`. BGE-M3 remains an independent local
pooling service; embeddings never pass through D3.

Deploy D3-mini on the separate NVIDIA GPU machine before starting this stack.
The script requires Docker Compose, one CUDA-capable GPU, a DNS name, an
operator-provided TLS certificate/key, and a random API key of at least 32
characters:

```sh
export D3_SERVER_NAME="d3-mini.${STACK_DOMAIN:?set to the STACK_DOMAIN value from .env}"
export D3_API_KEY="$(openssl rand -hex 32)"
export D3_TLS_CERT_FILE=/secure/path/d3-mini.crt
export D3_TLS_KEY_FILE=/secure/path/d3-mini.key
export D3_CA_CERT_FILE=/secure/path/issuing-ca.crt
./scripts/deploy-d3-mini.sh
```

The deployment uses immutable CUDA runtime and unprivileged Nginx image digests,
pins the built-in D3-mini model revision, persists model downloads, exposes only
the three Decisions routes over TLS, and checks a real four-tier decision before
reporting success. It is sized for a single 16 GB GPU; use `d3-nano` instead if
the actual GPU cannot provide sufficient runtime headroom. Restrict the remote
port at the firewall to the LiteLLM host. Copy the endpoint and the same secret
to `D3_DECISION_API_BASE` and `D3_DECISION_API_KEY` in this stack's `.env`.
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

## Development semantic response cache

The development profile uses an authenticated, internal Qdrant service for an
opt-in semantic response cache. LiteLLM embeds request messages with the local
`bge-m3` vLLM model and reuses a previous complete response only when cosine
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

After BGE-M3 vLLM and vLLM Semantic Router are healthy, run the end-to-end verification:

```sh
./scripts/verify-semantic-cache.py
```

The check authenticates to Qdrant, sends two different but semantically similar
single-turn prompts, and requires the second request to replay the first
response without creating another cached point. Semantic-cache hits also carry
the `x-litellm-semantic-similarity` response header for clients that inspect
response headers.
