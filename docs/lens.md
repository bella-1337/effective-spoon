# LiteLLM Lens

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

[LiteLLM Lens](https://docs.litellm.ai/docs/proxy/lens) uses AI agents to
review your own agents' traces. You describe the expected behaviour; Lens
investigates failures, groups recurring problems into findings, links each
finding to the original traces, and turns useful runs into datasets. The stack
runs it as the optional `lens` profile and embeds it in both LiteLLM dashboards
under **Lens**.

Enable it when you build or operate agents and want trace review inside the
gateway. It does not replace OpenSearch Trace Analytics (gateway request spans
for every caller) or Langfuse (LLM trace UI, prompts, and evaluations): Lens
stores the traces that *agents* send it, with their full inputs, outputs, and
tool calls.

## Architecture

The [LiteLLM Lens profile diagram](../diagrams/README.md#architecture) shows
these services, their networks, and the trace and gateway paths.

| Service | Role | Networks | Data |
| --- | --- | --- | --- |
| `lens-preflight` | One-shot check that every Lens secret is set, 32-512 characters long, and distinct | none | None |
| `lens-clickhouse` | ClickHouse 26.9 with a single-node Keeper for Lens traces, investigations, datasets, sessions, and coordination state | `lens-storage` (internal) | `lens-clickhouse-data` |
| `lens` | Lens UI, API, OTLP trace ingestion, and investigation workers in one non-root, read-only container | `edge`, `internal`, `lens-storage` | None (tmpfs `/tmp` only) |

- Traefik routes `https://$LENS_HOST` to Lens on port 4318: the standalone UI at
  `/ui/`, the API, and OTLP/HTTP trace uploads at `/v1/traces`.
- Both LiteLLM gateways call Lens privately at `http://lens:4318`. They
  authenticate with `LITELLM_LENS_SERVICE_TOKEN`, sign each dashboard user's
  identity with `LENS_GATEWAY_SECRET`, and export their recorded request cost
  to Lens so traces show what each model call cost.
- ClickHouse is reachable only on the internal `lens-storage` network, which
  only Lens joins. Prometheus, the gateways, and Traefik cannot reach it, and
  it publishes no port.
- Lens publishes no release image yet. The stack builds the upstream
  `deploy/runtime/Dockerfile` from the pinned release-candidate commit
  `b2ac5f8a289c3f77f02b81d67ca1ee075bfead6d` (tag `v1.108.0-rc.1`, the Lens
  release paired with LiteLLM 1.108) as `litellm-lens:1.108.0-rc.1`. Both
  gateway images (`v1.108.2` and `v1.110.0-dev.1`) include the Lens adapter
  from LiteLLM PR #45529.
- The build overrides the Dockerfile's `LENS_RUNTIME_IMAGE` argument with a
  newer `cgr.dev/chainguard/wolfi-base` digest. At the pinned commit the
  runtime stage installs packages without upgrading the base, which left a
  `ca-certificates-bundle` with a fixable Critical CVE that fails the stack's
  image release gate. Upstream fixed this later (`apk add --upgrade`); drop the
  override when the Lens pin moves past that fix.

### How the gateways stay independent of Lens

LiteLLM starts its Lens exporter whenever `LITELLM_LENS_URL` is set. Both
gateways therefore start through `litellm/modules/prepare_runtime_config.py`,
which:

1. enables Lens only while the `lens` service resolves on the Compose network;
2. then fails closed (the gateway does not start) when either connection secret
   is missing, outside 32-512 characters, or equal to the other, or when
   `LENS_HOST` is empty;
3. adds `general_settings.tracing.store.type: lens` to the generated runtime
   config; and
4. otherwise removes the four `LITELLM_LENS_*`/`LENS_GATEWAY_SECRET` variables
   from the gateway process and the tracing setting from its config.

The gateways declare Lens as an optional dependency
(`required: false`), so neither `prod` nor `dev` needs the profile, and a Lens
outage never blocks model requests. The decision is made once at gateway
start: **recreate the gateways after enabling or disabling Lens.** The
maintenance contracts fail when a gateway bypasses this launcher, requires
Lens, or reaches it through anything but the private Compose address.

## Enable Lens

1. Create `.env.optional` if it does not exist, and add `LENS_HOST` (default
   `lens.${STACK_DOMAIN}`) to the TLS certificate and host resolution, as in
   [Getting started](getting-started.md#3-resolve-the-hostnames).
2. Generate the secrets. The command fills only empty values, never rotates or
   prints existing ones, and rewrites both files with mode `0600`:

   ```sh
   ./scripts/lens.py secrets
   ```

   It sets `LITELLM_LENS_SERVICE_TOKEN` and `LENS_GATEWAY_SECRET` in `.env`
   (both gateways and Lens use them) and `LENS_ADMIN_TOKEN` and
   `LENS_CLICKHOUSE_PASSWORD` in `.env.optional`. To set them by hand, use
   `openssl rand -hex 32` for each, with four different values.
3. Validate and build. The first build compiles Lens (Rust, Node.js) from the
   pinned commit and takes about 15 minutes on a 10-core workstation, longer
   on smaller hosts; later starts reuse the image:

   ```sh
   docker compose --env-file .env --env-file .env.optional \
     --profile prod --profile dev --profile lens config --quiet
   docker compose --env-file .env --env-file .env.optional --profile lens build lens
   ```

4. Start Lens together with the gateways (add `--profile dev` for the
   development gateway):

   ```sh
   docker compose --env-file .env --env-file .env.optional \
     --profile prod --profile lens up -d --wait
   ```

   If the gateways already ran without Lens, recreate them so they connect:

   ```sh
   docker compose --env-file .env --env-file .env.optional \
     --profile prod --profile lens up -d --force-recreate --no-deps litellm-prod
   ```

5. Check the profile and both gateway connections, then run the end-to-end
   smoke test:

   ```sh
   ./scripts/lens.py status
   ./scripts/lens.py smoke
   ```

   Each gateway logs `LiteLLM Lens tracing is enabled` at start.

## Use Lens

- **From the gateway:** sign in to the LiteLLM dashboard as an administrator
  and open **Lens** in the sidebar. You use your LiteLLM session; the Lens
  administrator token is not requested. Gateway administrators are Lens
  administrators.
- **Standalone:** open `https://$LENS_HOST/ui/` and sign in with
  `LENS_ADMIN_TOKEN` from `.env.optional`. Keep that token for operators; it
  grants access to every trace.
- **Send traces:** on **Home**, create a tracing key (or use **Set up
  manually**) and configure your agent's OpenTelemetry exporter with
  `https://$LENS_HOST/v1/traces` and `Authorization: Bearer <tracing key>`.
  The address must be reachable from the agent; never use the agent's
  `localhost`. Upstream guides cover
  [first traces](https://docs.litellm.ai/docs/proxy/lens/first-trace),
  frameworks, and
  [Claude Code and Codex sessions](https://docs.litellm.ai/docs/proxy/lens/coding-agents).
  A key created from the gateway's **Lens** page carries that gateway user's
  access scope.
- **Investigations and signals** need an analysis model; see the next section.
  Without one, Lens records and displays traces only.

Lens accepts uploads up to 32 MiB per request. Traefik applies the stack's
[edge rate and concurrency limits](edge-and-abuse-protection.md) to Lens like
every other route; reduce the exporter batch size if uploads are rejected.

## Analysis models through the gateway

Lens can run investigations and signals with models served by a LiteLLM
gateway of this stack. Create a dedicated virtual key that can use only the
models Lens should use and has a budget, then set in `.env.optional`:

```dotenv
LENS_GATEWAY_API_BASE=http://litellm-prod:4000
LENS_GATEWAY_API_KEY=sk-...
```

Recreate `lens` and open **Settings > LiteLLM gateway** to refresh the model
list. Lens lists only chat models whose pricing and output limits are known.
The stack also sets `LENS_GATEWAY_URL` to the same address, so every analysis
call carries a short-lived identity signed with `LENS_GATEWAY_SECRET`. The
gateway then treats the call as Lens analysis: it keeps its content out of the
gateway logs and does not export it back to Lens. Each investigation has its
own monthly spending limit; the virtual key's budget is the hard ceiling.

`lens-preflight` refuses to start when only one of the two values is set.

## Data protection

Review these points with your data-protection officer before enabling the
profile; see also the [EU technical-control baseline](eu-technical-controls.md).

- **Agent traces contain full content.** Lens stores the inputs, outputs, tool
  calls, and attributes that agents upload, without truncation, for
  `LENS_TRACE_RETENTION_DAYS` (default 14). Agents decide what they send;
  instrument them to omit secrets and personal data they do not need.
- **Gateway exports carry no prompts or responses.** The gateways export one
  spend record per model call for cost matching. Both gateways run with
  `turn_off_message_logging: true` and `store_prompts_in_spend_logs: false`, so
  the record's `messages` and `response` fields are empty. It does carry call
  and session identifiers, the user, team, and key alias, the model and
  provider endpoint, token counts, cost, request tags such as the client's
  `User-Agent`, and, for failed calls, the provider's error text. This is the
  same metadata the gateway keeps in its own spend logs.
- **Analysis sends content to a model.** With `LENS_GATEWAY_API_KEY` set,
  investigations and signals send selected trace content to the configured
  models through the gateway. Use only models covered by your provider
  agreements; leave the key empty to disable analysis.
- **Access.** Gateway administrators and holders of `LENS_ADMIN_TOKEN` can read
  every trace. Tracing keys only upload. Other gateway users see traces within
  their tracing key's user or team scope.
- **No telemetry or external calls** are made by Lens without an analysis
  provider. Lens' optional Slack and GitHub integrations are not configured.

## Operate Lens

| Task | Command or location |
| --- | --- |
| Profile and gateway status | `./scripts/lens.py status` (also `./scripts/maintenance.py lens status`) |
| End-to-end ingestion check | `./scripts/lens.py smoke` |
| Post-upgrade regression check | `./scripts/verify-litellm-upgrade.py --only compose-health,lens,observability` |
| Readiness through Traefik | `curl --fail "https://$LENS_HOST/health/ready"` |
| Gateway connection (API) | `GET /lens/service` on a gateway with an administrator key: expect `connected: true`, `status.storage_ready: true`, `status.credentials_ready: true`, `status.public_contract: 1` |
| Logs | OpenSearch Discover `log.attributes.compose_service:"lens"` (or `"lens-clickhouse"`); `docker compose logs --tail=100 lens lens-clickhouse` |
| Monitoring | Prometheus job `lens` probes `/health/ready` through blackbox while the profile runs; alert **LiteLLM Lens is not ready** (warning) fires after five minutes of failure, in OpenSearch and, with the `grafana` profile, Grafana |

The smoke test creates a temporary tracing key, uploads one OTLP trace for the
agent `litellm-stack-smoke`, reads it back with the administrator token, and
revokes the key even on failure. The trace remains until retention removes it.

The `lens` Prometheus target exists only while the `lens` service resolves, so
stopping the profile does not raise alerts. Host disk alerts cover the
ClickHouse volume; ClickHouse system logs are kept for one day.

### Back up and restore

`scripts/backup-databases.py` does not include Lens. Lens state is the
`lens-clickhouse-data` volume (traces, investigations, datasets, sessions, and
Keeper coordination state) plus `LENS_ADMIN_TOKEN` and
`LENS_CLICKHOUSE_PASSWORD`. For a consistent copy, stop both services and
archive the volume:

```sh
docker compose --env-file .env --env-file .env.optional --profile lens stop lens lens-clickhouse
docker run --rm -v "${COMPOSE_PROJECT_NAME:-litellm-stack}_lens-clickhouse-data:/data:ro" \
  -v "$PWD/backups:/backup" alpine:3 \
  tar -czf "/backup/lens-clickhouse-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" -C /data .
docker compose --env-file .env --env-file .env.optional --profile lens up -d --wait lens
```

Keep the archive with the secrets in your secret manager, store it off-host,
and rehearse a restore into a separate project. To restore, stop both
services, replace the volume's contents, and start Lens again; `-p` keeps the
ClickHouse file ownership:

```sh
docker compose --env-file .env --env-file .env.optional --profile lens stop lens lens-clickhouse
docker run --rm -v "${COMPOSE_PROJECT_NAME:-litellm-stack}_lens-clickhouse-data:/data" \
  -v "$PWD/backups:/backup:ro" alpine:3 \
  sh -c 'find /data -mindepth 1 -delete && tar -xzpf /backup/lens-clickhouse-YYYYMMDDTHHMMSSZ.tar.gz -C /data'
docker compose --env-file .env --env-file .env.optional --profile lens up -d --wait lens
./scripts/lens.py smoke
```

Use the same `LENS_ADMIN_TOKEN` and `LENS_CLICKHOUSE_PASSWORD` as the backed-up
deployment. The upstream
[backup guide](https://github.com/BerriAI/lens/blob/main/docs/backup.md)
describes the same data set.

### Upgrade Lens

Lens and the gateway's embedded Lens UI are released as pairs with the same
version. To move to a new pin:

1. Read the Lens release notes, the
   [upgrade and migration checks](https://docs.litellm.ai/docs/proxy/lens/deployment/upgrades),
   and the paired LiteLLM release notes. Prefer a published, signed image
   digest when Lens publishes one.
2. Back up the volume as above.
3. In `compose.optional.yml`, change the build-context commit, the
   `LENS_VERSION` build argument, the image tag, and the `version` and
   `revision` labels together. Remove the `LENS_RUNTIME_IMAGE` override once
   the new commit upgrades its base packages, or move it to a current Wolfi
   digest. When upstream changes its ClickHouse pin or
   `deploy/clickhouse/*.xml`, update `lens-clickhouse` and
   `lens/clickhouse/config.d/` in the same change.
4. Update the rows in [Component versions](component-versions.md) and the
   [licence register](third-party-licences.md); `./scripts/maintenance.py doctor`
   fails until the image tag, revision label, build commit, and register agree.
5. Build, recreate `lens` (and the gateways when the paired LiteLLM version
   changes), and run `./scripts/lens.py smoke`. The supply-chain CI job
   (`scripts/ci/images.py build` and `scan`) builds the new image, checks its
   labels, and applies the KEV and fixable-Critical release gate, as for every
   locally built image; the runtime `security-scan` covers only registry
   images pinned by digest.

### Disable or remove Lens

Stop the profile, then recreate the gateways so they run without Lens:

```sh
docker compose --env-file .env --env-file .env.optional --profile lens stop lens lens-clickhouse
docker compose --profile prod up -d --force-recreate --no-deps litellm-prod
./scripts/lens.py status
```

The data stays in `lens-clickhouse-data`. Delete that volume only to erase all
Lens traces, investigations, and datasets.

## Troubleshooting

| Symptom | Check and action |
| --- | --- |
| `lens-preflight` exits with code 64 | Its log names the missing or invalid secret. Run `./scripts/lens.py secrets` and start again with both env files. |
| A gateway exits with `Lens is running but LiteLLM is missing required environment variables` | The connection secrets are not in `.env`, or `LENS_HOST` is empty because `.env.optional` was not loaded. Start the gateway with `--env-file .env --env-file .env.optional`. |
| The gateway's **Lens** page shows installation instructions | The gateway started before Lens. Recreate it with the `lens` profile; `./scripts/lens.py status` reports this case. |
| **Lens** is configured but unavailable | Check `./scripts/lens.py status`, `lens` logs, and that Lens and the gateways use the same two secrets (recreate all three after changing them). |
| Agents cannot upload traces | Use `https://$LENS_HOST/v1/traces` with a tracing key, not either connection secret or the admin token. Check that the agent trusts the stack's certificate and resolves `LENS_HOST`. |
| `lens-clickhouse` is unhealthy | `docker compose logs --tail=100 lens-clickhouse`; check disk space and the mounted Keeper configuration. |
| Investigations do not start | Configure an analysis model (`LENS_GATEWAY_API_*`) and check **Settings > Analysis**. Calculation steps need Linux Landlock ABI 3 and seccomp, provided by current Docker Engine hosts. |
| The image build fails or is slow | The build fetches `github.com/BerriAI/lens` and the npm, crates.io, and Wolfi registries; allow these during the build, or build on a connected host and load the image. |
