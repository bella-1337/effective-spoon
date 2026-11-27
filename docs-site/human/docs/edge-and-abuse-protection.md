<!-- human-version-of: docs/edge-and-abuse-protection.md sha256: 90472e6aa2dea531996eff74f5a547437ab68fe053357fc791bb920b5703e0cf -->

# Edge and abuse protection

This page lists the controls that protect the stack against denial of
service, traffic interception, and prompt injection. Read it when you tune
one of these controls, change where Xberg may connect, or need to know which
risks the controls do not cover.

The values are sized for a company-internal deployment of about 200 users,
not for a public endpoint. They stop runaway scripts and accidents without
getting in the way of normal use, including coding agents and dashboards. The
[2026-11-01 probe report](security-probe-2026-11-01.md) contains the evidence
behind each control.

## Edge: Traefik

`traefik/dynamic.yml` defines the controls. The `websecure` entrypoint in
`docker-compose.yml` attaches the edge middlewares to every route, including
the optional Grafana, Langfuse, and Lens routes. Lens trace uploads share the
uploading client's budget; agents that batch their exports stay far below it.

| Control | Setting | Effect |
| --- | --- | --- |
| TLS policy | `minVersion: VersionTLS12`, AEAD-only TLS 1.2 suites (Mozilla "intermediate") | TLS 1.0/1.1 and CBC/SHA-1 suites are refused; all current corporate clients connect |
| `sniStrict` | `false` | Monitors and clients that connect by IP address still complete the handshake; they receive Traefik's default certificate and a `404` |
| `edge-security-headers` | HSTS for one day, `nosniff`, referrer policy | Daily users' browsers refuse to downgrade to HTTP; an expired or replaced private certificate locks browsers out for at most a day |
| `edge-rate-limit` | 500 requests/s per client, burst 2,000 | Runaway scripts receive cheap `429` responses from Traefik instead of reaching LiteLLM |
| `edge-in-flight` | 1,024 concurrent requests per client | Caps connections held by one runaway client while allowing many long streaming requests |

Traefik counts these limits per client, and the client is the TCP peer:

- Traefik discards client-supplied `X-Forwarded-For` headers, because no
  trusted forwarders are configured.
- So everyone behind one VPN, NAT, or corporate load balancer (and every
  client on Docker Desktop) shares a single budget. That is why the limits are
  generous.

In the 2026-11-02 check, 200 concurrent clients at about 300 requests/s
received no `429` responses, while an unthrottled flood was still cut to its
budget.

If your gateway aggregates more traffic, raise a limit rather than removing
it.

### No request-body size limit (accepted)

The edge does not limit the size of request bodies, and LiteLLM's own
`max_request_size_mb` is an Enterprise-only setting. LiteLLM reads a whole
body before it checks the API key. So anyone who can reach Traefik can make it
hold large requests in memory.

In the 2026-11-01 probe, four concurrent 150 MB requests with an invalid key
raised `litellm-prod` from 1.04 to 2.6 GiB until they completed.

This risk is accepted for a company-internal deployment of about 200 users:

- the endpoint is not reachable from the Internet;
- the rate and in-flight limits still bound how many such requests one source
  can hold.

A body cap was evaluated and removed. Traefik's `buffering` middleware also
buffers responses, so it needed a second router per LiteLLM service to keep
streaming. That added more configuration than the risk justifies.

!!! warning "When to revisit this decision"

    Revisit it if any of these becomes true:

    - the stack becomes reachable from outside the company network;
    - untrusted users or partners receive access;
    - the host has little memory headroom.

    Your options then are:

    - a `buffering` middleware on a dedicated large-body router;
    - a memory limit on the LiteLLM services (an out-of-memory restart drops
      every open request);
    - an upstream proxy that enforces a body limit.

### Port 80

Configure every client with `https://`.

Port 80 only redirects to HTTPS. A client configured with an `http://` base
URL still sends its first request, including the API key, in plaintext before
the redirect. HSTS protects browsers after their first HTTPS visit; API SDKs
do not honour HSTS.

## LiteLLM

- `default_key_generate_params` in both profile configurations gives keys
  created without explicit limits `max_parallel_requests: 32` and
  `rpm_limit: 1200`. That is well above a busy coding agent; the limits only
  stop runaway scripts.
    - No default token limit is set, because spend is governed by budgets.
    - Give shared service or team keys explicit, higher values.
    - Keys created before this change keep their stored limits.
- `presidio_analyze_chunk_size_bytes: 100000` in
  `litellm/modules/guardrails/presidio.yaml` splits large prompts, so a single
  request cannot exhaust the analyzer's memory.
- Presidio Analyzer and Anonymizer each run two synchronous workers
  (`PRESIDIO_ANALYZER_WORKERS`, `PRESIDIO_ANONYMIZER_WORKERS`). Raise them on
  busy hosts. The analyzer preloads its English, German, and French
  pipelines, so its workers share them. Each extra worker adds only about
  0.1 GiB to the roughly 2.7 GiB that `PRESIDIO_ANALYZER_MEMORY_LIMIT` must
  cover.

## Prompt injection

`litellm/modules/guardrails/prompt-injection.yaml` defines the default-on
`prompt-injection-tool-output` guardrail. It applies LiteLLM's bundled
`prompt_injection_system_prompt` and `prompt_injection_jailbreak` keyword
categories to MCP tool results (`post_mcp_call`) with the `MASK` action:

- explicit attack phrases, such as "ignore all previous instructions", are
  replaced with `[KEYWORD_REDACTED]` and the rest of the document is returned;
- broader word pairs, such as "show" with "system prompt", also appear in
  ordinary AI documentation, so they are only logged as warnings.

To reject such results instead, change both categories to `action: BLOCK`.
That also blocks legitimate documents about prompts and LLMs.

Only admin-controlled key or team metadata can opt out of a default-on
guardrail; request bodies cannot.

User prompts are not screened by default, because coding requests routinely
discuss system prompts and jailbreak techniques. To screen them as well, add
`pre_call` to the guardrail's `mode`. Test representative prompts for false
positives first.

!!! warning

    The keyword categories catch common phrasing only. Treat them as one
    layer, not as a guarantee.

The Presidio guardrail masks tool results when an agent sends them back to a
model (`pre_call`). It does not mask the response of `/mcp-rest/tools/call`
itself: Presidio cannot rewrite Xberg's structured output and would block
every document containing a name. Grant MCP access only to trusted keys and
teams.

## Xberg sandbox

| File | Control |
| --- | --- |
| `security/xberg-destinations.toml` | The single source of allowed destinations; the four allowlist files below are generated from it |
| `security/xberg-config.toml` | Local paths and `file://` URIs are disabled; only uploaded bytes and allowlisted URLs are extracted |
| `security/xberg-egress-squid.conf` | Only HTTPS `CONNECT` tunnels; unlisted names are denied before any DNS lookup; the ICMP pinger is off |
| `security/xberg-dns-zones.conf` and `security/xberg-dns-deny-root.zone` | An empty authoritative root answers `NXDOMAIN` locally; only delegated, forwarded names are resolved upstream |
| `security/xberg-egress-allowlist.txt` | Destination allowlist for Squid |

To change destinations:

1. Edit only `security/xberg-destinations.toml`. List every CNAME target of a
   new name in its `dns_aliases`.
2. Run `python3 scripts/maintenance.py xberg-allowlist apply`.

The command regenerates the SSRF policy, both DNS files, and the Squid
allowlist with matching exact-or-subdomain semantics. It also recreates the
running sandbox services, which read their configuration only at startup.

`python3 scripts/maintenance.py doctor` fails when a generated file differs
from the source, or when `XBERG_HEALTHCHECK_DOMAIN` is not an allowed
destination.

## Internal network

Traefik, LiteLLM, the blackbox exporter, and the Xberg egress proxy drop
`NET_RAW`. A compromise of one of them therefore cannot ARP-spoof plaintext
service-to-service traffic. cAdvisor remains privileged by design.

## Residual risks

These risks remain with the controls above in place:

| Risk | Details |
| --- | --- |
| Unbounded request bodies | See [No request-body size limit](#no-request-body-size-limit-accepted). |
| Shared Presidio capacity | LiteLLM's per-key parallel-request limit is enforced after guardrails run, so a key can still queue large prompts that delay masking for other callers. Monitor guardrail latency and add Presidio workers as load grows. |
| Masked, not blocked, injection | Reworded injections that avoid the bundled phrases reach the model. Treat tool output as untrusted in agents. |
| Forwarded-header trust | `LITELLM_FORWARDED_ALLOW_IPS=*` lets any container that reaches LiteLLM directly, including Xberg, choose the client address. Do not rely on per-key `allowed_ips` until it is narrowed; see [Environment variables](environment-variables.md). |
| Plaintext internal links | PostgreSQL, Presidio, and Data Prepper traffic on the `internal` network is not encrypted. OpenSearch uses TLS. |
| Unconstrained GPU host CA | LiteLLM trusts the GPU host's mkcert CA for all outbound TLS, and mkcert CAs have no name constraints, so its private key could impersonate a completion provider. It never leaves the GPU host; see the [GPU host guide](../gpu-host/README.md#network-and-security). |
| Upstream error detail | Provider authentication errors are returned to callers and include a masked fragment of the provider API key. |
