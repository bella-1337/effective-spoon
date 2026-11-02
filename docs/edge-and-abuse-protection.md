# Edge and abuse protection

This guide describes the controls that protect the stack against denial of
service, traffic interception, and prompt injection, how to tune them, and the
risks they do not cover. The values are sized for a company-internal
deployment of about 200 users, not for a public endpoint: they stop runaway
scripts and accidents without getting in the way of normal use, including
coding agents and dashboards. The
[2026-11-01 probe report](security-probe-2026-11-01.md) contains the evidence
behind each control.

## Edge: Traefik

`traefik/dynamic.yml` defines the controls, and the `websecure` entrypoint in
`docker-compose.yml` attaches the edge middlewares to every route, including
the optional Grafana and Langfuse routes.

| Control | Setting | Effect |
| --- | --- | --- |
| TLS policy | `minVersion: VersionTLS12`, AEAD-only TLS 1.2 suites (Mozilla "intermediate") | TLS 1.0/1.1 and CBC/SHA-1 suites are refused; all current corporate clients connect |
| `sniStrict` | `false` | Monitors and clients that connect by IP address still complete the handshake; they receive Traefik's default certificate and a `404` |
| `edge-security-headers` | HSTS for one day, `nosniff`, referrer policy | Daily users' browsers refuse to downgrade to HTTP; an expired or replaced private certificate locks browsers out for at most a day |
| `edge-rate-limit` | 500 requests/s per client, burst 2,000 | Runaway scripts receive cheap `429` responses from Traefik instead of reaching LiteLLM |
| `edge-in-flight` | 1,024 concurrent requests per client | Caps connections held by one runaway client while allowing many long streaming requests |

The client is the TCP peer. Traefik discards client-supplied `X-Forwarded-For`
headers because no trusted forwarders are configured. Everyone behind one VPN,
NAT, or corporate load balancer (and every client on Docker Desktop) therefore
shares a single budget, which is why the limits are generous. In the
2026-11-02 check, 200 concurrent clients at about 300 requests/s received no
`429` responses, while an unthrottled flood was still cut to its budget.

Raise a limit, rather than removing it, if your gateway aggregates more
traffic.

### No request-body size limit (accepted)

Request bodies are not size-limited at the edge, and LiteLLM's own
`max_request_size_mb` is an Enterprise-only setting. LiteLLM reads a whole body
before it checks the API key, so anyone who can reach Traefik can make it hold
large requests in memory. In the 2026-11-01 probe, four concurrent 150 MB
requests with an invalid key raised `litellm-prod` from 1.04 to 2.6 GiB until
they completed.

This risk is accepted for a company-internal deployment of about 200 users:
the endpoint is not reachable from the Internet, and the rate and in-flight
limits still bound how many such requests one source can hold. A body cap was
evaluated and removed. Traefik's `buffering` middleware also buffers responses,
so it needed a second router per LiteLLM service to keep streaming, which added
more configuration than the risk justifies.

Revisit this decision if the stack becomes reachable from outside the company
network, if untrusted users or partners receive access, or if the host has
little memory headroom. Options then are a `buffering` middleware on a
dedicated large-body router, a memory limit on the LiteLLM services (an
out-of-memory restart drops every open request), or an upstream proxy that
enforces a body limit.

### Port 80

Port 80 only redirects to HTTPS. A client configured with an `http://` base
URL still sends its first request, including the API key, in plaintext before
the redirect. Configure every client with `https://`. HSTS protects browsers
after their first HTTPS visit; API SDKs do not honour HSTS.

## LiteLLM

- `default_key_generate_params` in both profile configurations gives keys
  created without explicit limits `max_parallel_requests: 32` and
  `rpm_limit: 1200`, well above a busy coding agent. They only stop runaway
  scripts. No default token limit is set, because spend is governed by
  budgets. Give shared service or team keys explicit, higher values. Keys
  created before this change keep their stored limits.
- `presidio_analyze_chunk_size_bytes: 100000` in
  `litellm/modules/guardrails/presidio.yaml` splits large prompts so a single
  request cannot exhaust the analyzer's memory.
- Presidio Analyzer and Anonymizer each run two synchronous workers
  (`PRESIDIO_ANALYZER_WORKERS`, `PRESIDIO_ANONYMIZER_WORKERS`). Raise them on
  busy hosts; add about 0.5 GiB to `PRESIDIO_ANALYZER_MEMORY_LIMIT` for each
  additional analyzer worker.

## Prompt injection

`litellm/modules/guardrails/prompt-injection.yaml` defines the default-on
`prompt-injection-tool-output` guardrail. It applies LiteLLM's bundled
`prompt_injection_system_prompt` and `prompt_injection_jailbreak` keyword
categories to MCP tool results (`post_mcp_call`) with the `MASK` action:

- explicit attack phrases, such as "ignore all previous instructions", are
  replaced with `[KEYWORD_REDACTED]` and the rest of the document is returned;
- broader word pairs, such as "show" with "system prompt", also appear in
  ordinary AI documentation, so they are only logged as warnings.

Change both categories to `action: BLOCK` to reject such results instead; that
also blocks legitimate documents about prompts and LLMs. Only admin-controlled
key or team metadata can opt out of a default-on guardrail; request bodies
cannot.

User prompts are not screened by default, because coding requests routinely
discuss system prompts and jailbreak techniques. To screen them as well, add
`pre_call` to the guardrail's `mode` and test representative prompts for false
positives first. The keyword categories catch common phrasing only; treat them
as one layer, not as a guarantee.

The Presidio guardrail masks tool results when an agent sends them back to a
model (`pre_call`). It does not mask the response of `/mcp-rest/tools/call`
itself: Presidio cannot rewrite Xberg's structured output and would block every
document containing a name. Grant MCP access only to trusted keys and teams.

## Xberg sandbox

| File | Control |
| --- | --- |
| `security/xberg-config.toml` | Local paths and `file://` URIs are disabled; only uploaded bytes and allowlisted URLs are extracted |
| `security/xberg-egress-squid.conf` | Only HTTPS `CONNECT` tunnels; unlisted names are denied before any DNS lookup; the ICMP pinger is off |
| `security/xberg-dns-zones.conf` and `security/xberg-dns-deny-root.zone` | An empty authoritative root answers `NXDOMAIN` locally; only delegated, forwarded names are resolved upstream |
| `security/xberg-egress-allowlist.txt` | Destination allowlist for Squid |

When you add a destination, update all four files and include every CNAME
target of the new name. `python3 scripts/maintenance.py doctor` fails when the
lists diverge.

## Internal network

Traefik, LiteLLM, the blackbox exporter, and the Xberg egress proxy drop
`NET_RAW`, so a compromise of one of them cannot ARP-spoof plaintext
service-to-service traffic. cAdvisor remains privileged by design.

## Residual risks

- **Unbounded request bodies.** See
  [No request-body size limit](#no-request-body-size-limit-accepted).
- **Shared Presidio capacity.** LiteLLM's per-key parallel-request limit is
  enforced after guardrails run, so a key can still queue large prompts that
  delay masking for other callers. Monitor guardrail latency and add Presidio
  workers as load grows.
- **Masked, not blocked, injection.** Reworded injections that avoid the
  bundled phrases reach the model. Treat tool output as untrusted in agents.
- **Forwarded-header trust.** `LITELLM_FORWARDED_ALLOW_IPS=*` lets any
  container that reaches LiteLLM directly, including Xberg, choose the client
  address. Do not rely on per-key `allowed_ips` until it is narrowed; see
  [Environment variables](environment-variables.md).
- **Plaintext internal links.** PostgreSQL, Presidio, and Data Prepper traffic
  on the `internal` network is not encrypted. OpenSearch uses TLS.
- **Upstream error detail.** Provider authentication errors are returned to
  callers and include a masked fragment of the provider API key.
