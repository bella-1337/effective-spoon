# Changelog

All notable changes to this stack are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases use
[semantic versioning](https://semver.org/spec/v2.0.0.html) tags
(`vMAJOR.MINOR.PATCH`). Each tag publishes a validated source bundle and its
`SHA256SUMS`; see [Development and delivery workflow](docs/development-workflow.md#continuous-delivery).

## [Unreleased]

### Added

- [Presidio PII masking and guardrails](docs/presidio-guardrails.md#check-what-the-guardrail-masks)
  explains how administrators see what the `presidio-pii` guardrail masks
  without calling a model: `POST /guardrails/apply_guardrail` and the admin
  UI's guardrail test playground, both limited by LiteLLM to proxy
  administrators.
- German and French in the Presidio analyzer, and UK formats in English. See
  [Languages and UK formats](docs/presidio-guardrails.md#languages-and-uk-formats).
  - The new `litellm-presidio-analyzer:2.2.362-1` image (`presidio/Dockerfile`)
    adds the `de_core_news_lg` (MIT) and `fr_core_news_lg` (LGPL-LR) 3.8.0
    pipelines, pinned by hash, to the upstream analyzer. LGPL-LR obligations
    apply only if the image is distributed outside the organisation.
  - `presidio/analyzer-engine.yaml` and `presidio/recognizers.yaml` set the
    languages and switch on UK National Insurance number, passport, and
    postcode recognizers. Credit cards are also detected in German and French.
  - Gunicorn preloads the pipelines, so workers share them: about 2.7 GiB for
    two workers. A contract requires the preload.
  - German and French street-address recognizers mask whole addresses
    (street, house number, postcode, and city) as `LOCATION`. Before, the
    house number and postcode stayed visible ("<LOCATION> 12, 80331
    <LOCATION>"). Their repetitions are bounded so crafted input cannot make
    matching slow; `scripts/tests/test_presidio_recognizers.py` covers matches,
    non-matches such as amounts and years, and 100 KB inputs. The `pii-assist`
    guardrail also folds a postcode or house number left between its own
    placeholders into them.
- Optional local-model assist (`pii-assist` guardrail,
  `litellm/modules/pii_assist.py`). See
  [Local-model assist](docs/presidio-guardrails.md#local-model-assist).
  - Before Presidio, a local model, such as Ollama's `qwen3.5:4b` on the Docker
    host, detects each prompt's language and personal data. The guardrail sets
    the request's Presidio language and masks what the model found, except
    organisation names. Presidio then masks the rest, so it remains the
    baseline; on any model failure the request continues with Presidio alone.
  - Enable it with `PII_ASSIST_API_BASE` (and `PII_ASSIST_OLLAMA_MODEL`) in
    `.env`. The gateways accept only `host.docker.internal`, a Compose service,
    or a private address, because the model reads prompts unmasked, and reach
    the Docker host through `extra_hosts: host.docker.internal:host-gateway`.
    `PII_ASSIST_MAX_CHUNK_CHARS`, `PII_ASSIST_MAX_CALLS`, and
    `PII_ASSIST_TIMEOUT_SECONDS` bound the cost; results are cached per text.
  - LiteLLM (checked up to 1.111.0-dev.4) ignores the per-request Presidio
    language on chat requests. The module installs a narrow compatibility shim
    that restores it and allows placeholders such as `<LOCATION>` through, so
    the German and French models do not re-mask them. It is skipped with a
    warning if LiteLLM changes the patched method; the upgrade check tests
    German and French analysis and the guardrail order.
  - The prompt and answer schema are in
    `litellm/modules/pii_assist_prompt.json`; only items that occur verbatim
    in the prompt are masked.
  - Approach: the BerriAI Inference Stack has no data protection to compare.
    LiteLLM cannot detect a request's language, so a local model through
    LiteLLM was chosen; D3-mini was considered and not used.

### Changed

- The `eu-data-protection` policy now runs `pii-assist`, then `presidio-pii`,
  and is the default attachment. A new `local-pii-assist` policy exempts only
  the local `pii-assist` model, which must see text unmasked. A repository
  contract allows no other policy attachment, because any other would replace
  the default masking for the requests it matches.
- `PRESIDIO_ANALYZER_MEMORY_LIMIT` defaults to `4g` (was `2g`), and
  `PRESIDIO_SUPPORTED_LANGUAGES` is no longer read: the languages come from
  `presidio/analyzer-engine.yaml`.

### Upgrade notes

- Raise `PRESIDIO_ANALYZER_MEMORY_LIMIT` in `.env` to at least `4g` (prod
  hosts: `5g`) before deploying. With `2g` the analyzer is killed when it loads
  the German and French pipelines, and masking stops.
- Build the new analyzer image (`docker compose build presidio-analyzer`) and
  recreate `presidio-analyzer` and both gateways.
- To use the local-model assist, run Ollama on the Docker host, pull the model,
  and set `PII_ASSIST_API_BASE`. Without it, nothing else changes: Presidio
  runs alone, in English, unless a client sets the language.

### Fixed

- [Presidio PII masking and guardrails](docs/presidio-guardrails.md) said the
  gateway masks organisation names with a 0.7 threshold. The analyzer has
  never mapped spaCy's `ORG` label, and the upgrade check asserts that it
  does not, so the guide now says the gateway does not mask organisations.
- The Presidio guardrail no longer masks file names, paths, and Rust or C++
  code as personal data. See
  [Code, file names, and paths](docs/presidio-guardrails.md#code-file-names-and-paths).
  On sample coding prompts, detections fell from 39 to 11; the URLs, IP
  addresses, and other personal data in the samples are still masked.
  - `WebUrlRecognizer` replaces Presidio's `UrlRecognizer`, which matched any
    `name.tld`: `.rs`, `.py`, `.md`, and `.sh` are country domains, so
    `main.rs` and `self.name` were masked as URLs. A URL now needs a scheme or
    `www.`. Bare domains such as `example.com` are no longer masked.
  - `IpAddressRecognizer` replaces `IpRecognizer`, whose IPv6 pattern matched
    the `::` in `std::io`. IPv6 now needs two hex groups and a digit, and
    compressed addresses such as `2001:db8::1` are masked whole.
  - `presidio_score_thresholds` ignores `US_DRIVER_LICENSE` below 0.4. Its
    weak pattern matched Rust types (`i64`, `f32`) and compiler codes
    (`E0382`). With a context word such as "license", a real one still scores
    0.65.
  - spaCy can still mask code identifiers as `PERSON` or `NRP` and paths as
    `LOCATION`; the guide lists what remains.
  - Approach: the BerriAI Inference Stack has no data protection to compare.
    Stricter recognizers and one threshold were chosen over a code-aware
    filter in LiteLLM, which would need custom code checked on every upgrade.
- The guide's `apply_guardrail` example used `jane@example.test`, which
  Presidio does not detect as an email address because `.test` is not a
  public top-level domain. It now uses `example.com` and shows the real
  output.

### Security

- `security/exceptions.toml` records time-limited exceptions for the fixable
  Critical findings that no upstream image fixes yet, reviewed on 2026-11-23:
  - Presidio `2.2.362` analyzer and anonymizer, until 2026-12-31: Brotli
    (CVE-2026-111497, on the CISA KEV list), GnuTLS, OpenSSL, Perl, `anyio`,
    and `certifi`. It is the newest Presidio release and Microsoft has not
    rebuilt it.
  - The `gosu` helper in the PostgreSQL `16.16-alpine3.24` and `17.12` images,
    until 2027-02-28: CVE-2025-68121 in Go's `crypto/tls`. `gosu` only drops
    privileges at container start and makes no TLS connections. The current
    upstream rebuilds of both tags still ship it.

  Raw reports still list every finding; the exceptions only clear the policy
  metrics. Remove each entry once a fixed image is pinned.

## [v0.4.1] - 2026-11-23

### Added

- Optional `docs` profile that serves this documentation from the stack at
  `DOCS_HOST`, by default `gateway-deployment-docs.${STACK_DOMAIN}`. The
  `litellm-stack-docs` image runs `zensical serve` on the checked-out revision,
  and every page's banner names that commit (`STACK_REPOSITORY_REVISION`). It
  serves one version without the version selector; the versioned site stays on
  Pages. The container is non-root and read-only on the `edge` network only;
  Traefik answers only `GET` and `HEAD` and adds a same-origin Content Security
  Policy through a `docs-headers` middleware. There is no sign-in. The BerriAI
  Inference Stack has no documentation site to compare with; building the
  sources into the image was chosen over mounting a host checkout or
  redirecting to Pages, so the docs cannot drift from the running stack, and
  `zensical serve` was chosen over a separate static web server. See
  [Documentation site](docs/documentation-site.md#serve-the-site-from-the-stack).

### Changed

- The **Source** links in the [diagram guide](diagrams/README.md) open a
  viewer page that draws the Mermaid source in the browser, instead of
  downloading the `.mmd` file. `scripts/render-diagrams.py` writes the pages
  as `diagrams/NN-name.mmd.html`. They run Mermaid.js 12.3.0 (MIT), which is
  pinned by SHA-256 and served from the documentation site, so the site still
  makes no third-party requests.
- Every diagram page, animated or Mermaid, has zoom buttons (50% to 400%, also
  Ctrl or ⌘ with the wheel, drag to pan) and downloads of the diagram as SVG
  or PNG and of its `.mmd` source. Only the diagram zooms: the architecture
  diagrams' legend now sits in its own SVG below the zoom area.
- The notes in the Xberg and search tool-call flows no longer contain `;`,
  which Mermaid reads as the end of a statement, and the renderer now rejects
  `;` in sequence diagrams.
- Xberg moves from `1.3.10-core` to `1.4.2-core`, still pinned by digest. The
  version table and third-party licence register now list it too.
- The documentation site builds with Zensical `0.1.1` (was `0.1.0`), and the
  OpenSearch tools use psycopg `3.3.9` (was `3.3.6`).

### Fixed

- The Admin UI tab now shows the `LITELLM_FAVICON_URL` favicon instead of
  LiteLLM's own. LiteLLM's UI links its bundled `/favicon.ico` ahead of
  `/get_favicon`, so Traefik now sends `/favicon.ico` on both gateway hosts to
  `/get_favicon`.

## [v0.4.0] - 2026-11-22

### Added

- [Reference stack: BerriAI Inference Stack](docs/reference-inference-stack.md)
  compares this stack with LiteLLM's reference deployment. Feature requests are
  now checked against it, and the requester chooses the approach before it is
  implemented (SKILL.md guardrail 9).
- Optional `d3` profile: D3-mini on the stack host's own NVIDIA GPU, sized for
  a single 16 GB RTX PRO 2000 Blackwell. See
  [On-host D3-mini](docs/d3-mini-on-host.md).
  - It runs on the official `vllm/vllm-openai:v0.33.0` image, pinned by digest.
    Both gateways reach it only on the new internal `d3-local` network, at
    `D3_DECISION_API_BASE=http://d3-mini:8000`.
  - `d3-mini-download` fetches the pinned model revision once. `d3-mini` then
    runs offline, without root, and is never published.
  - New `.env.optional` settings: `D3_MAX_INPUT_TOKENS` (default 4096, range
    512–8192), `D3_GPU_MEMORY_UTILIZATION` (0.90, range 0.50–0.95),
    `D3_GPU_DEVICE_ID`, `D3_CPU_LIMIT`, `D3_MEMORY_LIMIT`, and `D3_SHM_SIZE`.
    `d3-mini-preflight` refuses to start the profile on a value out of range.
  - The host needs NVIDIA driver 580 or newer and the NVIDIA Container Toolkit.
  - Decision record: the reference stack runs models as plain vLLM containers
    with `--max-model-len` (default 4,096). The profile follows it, because
    vLLM reserves memory and checks the cap at start-up. The Semantic Router
    runtime allocates on demand, so an oversized cap would fail on a request.
    The service is declared in Compose instead of started by a launcher that
    holds the Docker socket. The profile has not run on the target card yet.
- `coding-router` and D3-mini telemetry in OpenSearch:
  - LiteLLM's `litellm.routing.*` span attributes (tier, deciding mechanism,
    classifier model, confidence, failure reason, routed model) are now
    filterable fields.
  - With the `d3` profile, Prometheus scrapes D3-mini's vLLM metrics (job
    `d3-mini`). `opensearch-bridge` mirrors them and adds `d3_*` expressions,
    and vLLM sends one OTLP span per decision to Data Prepper. Prometheus and
    Data Prepper join the internal `d3-local` network for this.
  - New **Coding Router and D3-mini** dashboard and **On-host D3-mini is
    unavailable** alert.
- `scripts/compare-coding-router-classifiers.py` compares LiteLLM's heuristic,
  `heuristic_v2`, and any D3 model (such as D3-mini or d3-nano) on your own
  labelled prompts, using LiteLLM's own router code in the pinned prod image.
  It reports accuracy, routing too low or too high, D3 fallbacks, and latency,
  and never prints or stores prompt text. See
  [On-host D3-mini](docs/d3-mini-on-host.md#compare-classifiers-on-your-prompts).

### Changed

- **Breaking:** The project is now the Sovereign LLM Gateway, a region-locked
  gateway. The new required `DATA_SOVEREIGNTY_REGION` (`eu` or `us`) sets where
  every cloud model must process prompts. Cloud models are no longer listed in
  `litellm/config.*.yaml`. They come only from the optional `BEDROCK_MODELS`,
  `AZURE_OPENAI_DEPLOYMENTS`, and `VERTEX_MODELS` lists in `.env`. With none
  set, the gateways serve only their self-hosted models. At startup,
  `prepare_runtime_config.py` wires the configured models into both gateways
  through `litellm/modules/sovereign_models.py`:
  - Bedrock models get the region's geographic inference profile.
  - Azure resource regions and Vertex locations are checked against the region.
  - The gateway refuses to start when a credential is missing or a location
    could process prompts outside the region.
- **Breaking:** `gpt-4o`, `gpt-4o-mini`, and `OPENAI_API_KEY` are removed. No
  model calls the OpenAI API directly.
- **Breaking:** `coding-router` exists only when `CODING_ROUTER_DEFAULT_MODEL`
  names a configured model. Its tiers come from the `CODING_ROUTER_*_MODEL`
  variables.
- `coding-router` can route more efficiently, all set from `.env`:
  - A tier can list several models.
  - `CODING_ROUTER_ADAPTIVE` learns which model in a tier works best.
  - `CODING_ROUTER_SESSION_AFFINITY` keeps a conversation on one model so
    provider prompt caches are reused.
  - `CODING_ROUTER_*_KEYWORDS` send matching prompts straight to a tier.
  - `CODING_ROUTER_CLASSIFIER=heuristic` classifies with no model call.
  - `CODING_ROUTER_STALL_ESCALATION` moves a stuck agent task up a tier.
  - A prompt too long for its tier, or an image sent to a text-only tier, now
    moves to a model that can take it.
- **Breaking:** The D3-mini classifier timeout drops from 15 to 3 seconds
  (`CODING_ROUTER_CLASSIFIER_TIMEOUT_MS`). A failed or slow classification is
  now scored locally instead of going to `CODING_ROUTER_DEFAULT_MODEL`.
- **Breaking:** The gateways refuse to start unless `D3_DECISION_API_BASE` is an
  HTTPS base URL without a path, credentials, or query, or exactly
  `http://d3-mini:8000` for the `d3` profile.
- Provider key files live in `PROVIDER_SECRETS_DIR` (default
  `./litellm/provider-secrets`, ignored by Git), which is mounted read-only into both
  gateways.
- The development `test-model` now returns a mock response and makes no
  provider call.
- `check-litellm-upgrade-compatibility.py` now fails when a static config
  lists a cloud model or the launcher's region lock is missing. See
  [Data sovereignty and regional models](docs/data-sovereignty.md).

## [v0.3.1] - 2026-11-21

### Changed

- Xberg's outbound destinations now come from one file,
  `security/xberg-destinations.toml`. Previously the same list was kept by hand
  in four files with different matching rules. Adding a name to only some of
  them, especially a DNS forward zone without its root delegation, still
  answered `NXDOMAIN`. The four files are now generated from the source by
  `scripts/maintenance.py xberg-allowlist render`, and `xberg-allowlist apply`
  also recreates the running sandbox services, which a plain
  `docker compose up -d` leaves on their old configuration. `doctor` now
  compares the generated text with the source, not just the names, so a
  mismatch between exact-host and subdomain matching is caught. Squid now
  allows `docs.xberg.io` only, not its subdomains, to match Xberg's
  exact-host policy.
- The Xberg healthcheck resolves `XBERG_HEALTHCHECK_DOMAIN` (default
  `docs.xberg.io`) instead of a fixed name, and `doctor` requires it to be an
  allowed destination. Removing `docs.xberg.io` from the list no longer leaves
  Xberg, and with it `litellm-dev`, permanently unhealthy.
- `xberg-egress-proxy` joins the `prod` and `dev` profiles with Xberg, so its
  Internet route no longer runs when Xberg does not.
- The upgrade compatibility check starts its Xberg validator with the
  stack's `security/xberg-config.toml`, as Compose does, instead of Xberg's
  built-in defaults.
- The upgrade compatibility check now validates the runtime config the
  gateways actually load. It starts the candidate through
  `prepare_runtime_config.py` with Langfuse and Lens enabled, instead of
  validating the raw YAML. Lens tracing, which only the launcher adds, is now
  validated too.
- `scripts/maintenance.py up` runs `doctor` and starts the stack only when it
  passes, so contract failures such as Xberg allowlist drift stop a deployment
  instead of surfacing at runtime. The getting-started guide uses it.
- Production LiteLLM moves from `v1.108.2` to the `v1.108.9` LTS patch
  release, still pinned by digest. The 1.108.x LTS line needs no migration or
  configuration change: production already sets `drop_params: true`, which
  1.108.7 requires for Responses-only fields on bridged providers.
- Development LiteLLM follows the nightly channel from `v1.110.0-dev.1` to
  `v1.111.0-dev.4`, still pinned by digest; it passes the upgrade
  compatibility check. Its new `redact_tokens_in_spend_logs` setting now
  defaults to off; both gateways keep `store_prompts_in_spend_logs: false`, so
  spend logs hold no free-form text to scan. Releases from `v1.111.0-dev.3`
  are signed with Sigstore keyless signing instead of `cosign.pub`.
  The image now runs as the non-root user 65532 and replaces
  `docker/prod_entrypoint.sh` with `docker-entrypoint.sh`. Both gateways now
  start whichever of the two launchers the image provides (`docker-entrypoint.sh
  proxy` first), so an image-only upgrade or rollback across v1.111 keeps
  working without editing the command.
- The upgrade compatibility check also proves that the candidate image
  provides a launcher that the `litellm-prod` and `litellm-dev` start commands
  use. Its config validator runs `litellm` directly, so v1.111.0-dev.4 passed
  the check and then failed to start the gateway.
- The upgrade compatibility check gives its config validator 2 GiB of memory
  instead of 512 MiB. LiteLLM 1.111 peaks near 1 GiB while loading the
  configuration and otherwise never printed its policy-engine summary, which
  failed the check for a compatible image.
- The upgrade regression suite's `opensearch-logs` and `logs` checks no
  longer fail for a running service whose container has written no log line;
  they report it instead. Lens logs nothing in normal operation, so both
  checks failed whenever the `lens` profile ran.
- Prometheus `v3.15.1` to `v3.16.0`, Grafana Alloy `v1.21.3` to `v1.22.0`,
  and Data Prepper `2.16.0` to `2.17.0`. Data Prepper's `otel_traces`
  processor no longer copies `gen_ai.*` attributes and token totals onto
  non-GenAI root spans, so the **Model calls by model** trace panel stops
  counting each LiteLLM request's HTTP root span as a second model call. The
  Alloy breaking changes (`loki.enrich`, `prometheus.enrich`, OTel connectors)
  and the Prometheus TLS external-URL default do not affect this stack.
- Optional `grafana` profile: Grafana `13.3.0` to `13.3.1` (six CVE fixes)
  and Loki `3.7.9` to `3.7.10`.
- Optional `langfuse` profile: Langfuse web and worker `4.72.0` to `4.80.0`
  and ClickHouse `26.3.62.3` to `26.3.64.3` (same 26.3 LTS series), with
  rebuilt Redis `7.4.12` and Chainguard MinIO digests. Langfuse runs its
  PostgreSQL and ClickHouse migrations on start. From 4.77 it no longer
  tokenizes generations itself; LiteLLM already reports token usage on every
  span.
- Optional `lens` profile: its ClickHouse `26.9.6.6` to `26.9.29.5`, the
  latest patch of the 26.9 series Lens pins, and a newer
  `cgr.dev/chainguard/wolfi-base` runtime digest for the Lens build.
- The Xberg egress proxy moves from Squid `6.13-25.04_edge`, whose Ubuntu
  25.04 base no longer receives updates, to `7.2-26.04_edge`. The new Ubuntu
  rock runs Squid as its unprivileged `_daemon_` user (584792) without a
  shell, so the service calls the image's entrypoint directly, owns its
  tmpfs mounts as that user, drops all capabilities, and probes its port with
  Perl instead of Bash.
- Traefik `v3.7.17` to `v3.7.19`, which fixes five security advisories. Its
  migration notes (BasicAuth password length, Ingress-NGINX TLS options,
  dot-segments with path parameters) need no change here.
- SearXNG `2026.10.30-ffa88e3ba` to `2026.11.15-2d500cb6b` (rolling release).
- Qdrant `v1.20.0` to `v1.20.1`, which fixes slow HNSW builds on many-core
  hosts.
- Trivy `0.76.1` to `0.76.2` in the image-security monitor, host-mode scans,
  and both CI systems. The release rebuilds Trivy on Alpine 3.24.3 and fixes
  several dependency CVEs.
- Docker CLI and buildx `29.9.2-cli` to `29.9.3-cli` in the image-security
  monitor, and the GitLab CI Docker CLI and Docker-in-Docker images to
  `29.9.3`.
- CI tooling: Ruff `0.17.4` to `0.17.5` on GitHub Actions and GitLab CI, and
  the GitLab release job's `glab` image `v1.128.0` to `v1.130.0`.
- Remote GPU host: the TLS terminator moves from nginx `1.29.5-alpine` to the
  `1.31.7-alpine` mainline release; its configuration passes `nginx -t`
  unchanged. Redeploy the GPU host with `gpu-host/deploy.py` to apply it.
- PostgreSQL `16.15-alpine3.24` to `16.16-alpine3.24` and `17.11` to `17.12`,
  and a rebuilt `pgvector/pgvector:0.8.7-pg16` digest. These minor releases
  need no dump or restore. Their release notes ask for a `REINDEX` of B-tree
  indexes on unconstrained `bpchar` columns and of `gist_trgm_ops` indexes,
  and of GIN indexes suspected of corruption. None of the stack's databases
  has such a B-tree or trigram index, so no `REINDEX` is needed.

## [v0.3.0] - 2026-11-09

### Added

- Two request-flow diagrams that start from the user's prompt and show who
  decides on and runs each tool call: the model's Xberg `extract` call, which
  LiteLLM runs itself, and the model's search call, which the agent runs
  through the native `searxng-search` endpoint.
- Optional `lens` profile with [LiteLLM Lens](docs/lens.md): AI review of agent
  traces, findings, and datasets, embedded in both LiteLLM dashboards. Lens is
  built from the pinned `BerriAI/lens` release-candidate commit
  `b2ac5f8a289c3f77f02b81d67ca1ee075bfead6d` (`v1.108.0-rc.1`) and stores its
  data in its own ClickHouse on the internal `lens-storage` network. A
  preflight job rejects missing or reused secrets, Traefik routes `LENS_HOST`,
  Prometheus probes its readiness, and the **LiteLLM Lens is not ready** alert
  covers a running but unready Lens. The build uses a newer Wolfi runtime base
  than the pinned commit, whose base fails the image release gate with a
  fixable Critical `ca-certificates-bundle` CVE.
- `scripts/lens.py` (also `maintenance.py lens`) generates missing Lens secrets
  without rotating existing ones, checks Lens and each gateway's connection, and
  runs an end-to-end trace smoke test. The upgrade regression suite gains a
  `lens` check.
- Ruff (`ruff.toml`: syntax, Pyflakes, and bugbear rules) and zizmor
  (`.github/zizmor.yml`: workflow and Dependabot security audit) run in CI on
  GitHub Actions and GitLab CI through `scripts/ci/static-checks.sh`.
- An OpenSSF Scorecard workflow reports to code scanning and publishes the
  score behind a new README badge.
- Dependabot updates the image pins in `docker-compose.yml`,
  `compose.optional.yml`, and `gpu-host/compose.yml`, except LiteLLM and major
  versions, and grouping images that must match. Every Dependabot entry waits
  seven days after an upstream release.
- The `master` branch and `v*` release-tag rulesets are kept as code in
  `.github/rulesets/`. The repository-settings guide applies them together
  with immutable releases, a tag-only `release` environment, SHA-pinned
  actions, fork pull request approval, secret push protection, CodeQL default
  setup, and the labels Dependabot uses.
- A "Planned improvements" list in the development workflow guide: a Compose
  smoke test, a release SBOM attestation, dependency review, and a link check.

### Changed

- Both LiteLLM gateways start through `prepare_runtime_config.py` as a launcher.
  It enables the Lens connection only while the `lens` service runs, removes the
  `LITELLM_LENS_*` variables from the gateway process otherwise, and refuses to
  start a gateway whose Lens secrets are missing or invalid while Lens runs.
- The maintenance contracts accept build contexts fetched from Git only when
  they pin a full commit that the image's revision label and the licence
  register record.
- A manual Release run must be started on the release tag itself; the
  `release_tag` input is removed, and a run on a branch fails before any
  check.

### Fixed

- The OpenSearch bridge binds each cycle's evaluation time to its metric and
  usage tasks explicitly instead of through closures over the loop variable.
- Unused imports in three maintenance scripts.

## [v0.2.1] - 2026-11-08

### Changed

- Architecture diagrams are laid out by Graphviz `dot` instead of dashmotion's
  own engine. Connectors are curved and routed between boxes, so they no longer
  trace the dashed boundary rectangles, and each boundary keeps its members
  together. Rendering now needs Graphviz.
- The rendered diagram HTML is no longer committed. The Mermaid sources are the
  only form in the repository; the documentation build in CI renders them,
  installing Graphviz in its image, and fails when one does not render.
  `./scripts/render-diagrams.py` downloads the dashmotion renderer at a pinned
  commit and checks its SHA-256 digests, so the dashmotion skill is no longer
  needed. The `--check` option is removed.
- The Mermaid edges alone decide the rows, as in any Mermaid viewer. The
  `%% @tier` directive is removed from the diagram format and from every
  source.

## [v0.2.0] - 2026-11-07

### Added

- A [component versions](docs/component-versions.md) table lists the version
  of every image, model, and host tool in one place. `doctor` and
  `scripts/ci/validate.sh` fail when it misses an image the stack uses or lists
  one it no longer uses.
- `LITELLM_USE_V2_MIGRATION_RESOLVER` (default `false`) selects LiteLLM's
  database migration resolver for both proxies. LiteLLM 1.108.x cannot verify
  and baseline this stack's fresh Wolfi-backed databases with its default v2
  resolver, so the stack keeps the documented legacy fallback.

### Changed

- Production enables the LiteLLM Chat UI and accepts master-key sign-in to the
  UI, matching development. The promotion register records the Chat UI as
  `prod-canary`.
- Production LiteLLM moves from `v1.106.6` to the `v1.108.2` LTS release.
  Development tracks LiteLLM's nightly channel and moves from `v1.107.0-dev.3`
  to `v1.110.0-dev.1`. Both remain pinned by digest.

### Fixed

- The LiteLLM upgrade compatibility check now waits for the candidate image's
  startup output to settle before reading it. It previously stopped at the
  first success line and could miss the policy summary printed after it,
  wrongly reporting a compatible development image as incompatible.

## [v0.1.1] - 2026-11-06

### Added

- Status badges for every GitHub Actions workflow, the latest release, the
  documentation site, and Dependabot in the README. The documentation site
  drops them so it still makes no third-party requests.
- Security policy, contributing guide, issue and pull request templates,
  release-note categories, and `.editorconfig`.
- A warranty disclaimer and licence notice in the README and in the
  documentation site footer: the stack is published as is, without warranty;
  its own container images are MIT-licensed; every third-party tool keeps its
  own licence.

### Changed

- The documentation site's home page banner and footer are rewritten for a
  published stack. Releases up to `v0.1.0`, whose pages lack the warranty and
  licence notice, are no longer built into the site.
- The OCI `licenses` label of the stack's own images is now `MIT` instead of
  `LicenseRef-Proprietary`.
- The eleven flow diagrams (`diagrams/10`–`20`) are now animated Mermaid
  sequence diagrams that show who sends what to whom, rendered in the
  dashmotion style by the new `scripts/sequence_diagrams.py`. The semantic
  cache sequence no longer shows responses being stored when a request did
  not opt in, matching the cache's `default_off` mode.
- `gpu-host/deploy.py` runs as a uv script (`./deploy.py` or
  `uv run deploy.py`).
- The README says that outside contributions are not accepted at the moment
  and explains how to report a vulnerability privately.

### Fixed

- A release tag now publishes the documentation site through one
  default-branch run, which the `github-pages` environment admits, instead of
  a second, rejected tag deployment.
- The development workflow guide no longer says the documentation site is
  restricted to repository members.

## [v0.1.0] - 2026-11-03

Initial release of the self-hosted LiteLLM gateway stack.

### Gateway and runtime profiles

- Separate LiteLLM `prod` (`v1.106.6`) and `dev` (`v1.107.0-dev.3`) proxies
  behind Traefik, each with its own PostgreSQL database and the
  `postgres-init` one-shot dependency.
- Public hostnames derived from `STACK_DOMAIN`; all on-prem settings
  externalized into `.env` and `.env.optional`, documented in the
  [environment variable reference](docs/environment-variables.md).
- Delivered TLS certificates for on-prem deployment; `mkcert` for private
  workstations only.
- Native LiteLLM admin UI branding and Enterprise virtual-key rotation.

### Models, search, and guardrails

- Remote GPU host stack ([`gpu-host/`](gpu-host/README.md)) serving D3-mini
  semantic routing and the pinned BGE-M3 embedding model behind mkcert TLS,
  trusted from LiteLLM through `GPU_HOST_CA_BUNDLE`.
- Presidio PII masking guardrails, internal-only SearXNG search, and the Xberg
  MCP document extractor with an isolated, DNS-restricted egress path.
- pgvector store with opt-in Xberg auto-indexing, and a development Qdrant
  semantic response cache.

### Observability

- OpenSearch, OpenSearch Dashboards, and Data Prepper as the default
  observability platform for logs, metrics, LiteLLM usage, tracing, alerting,
  and audit trails, with users, roles, and tenants.
- Prometheus with host, container, PostgreSQL, and blackbox exporters, and
  Grafana Alloy as the Docker log collector, scoped to the stack's Compose
  project.
- Optional `grafana` (Grafana and Loki) and `langfuse` profiles, management
  ROI dashboards, and a single alert-rule source in `grafana/alerting/`.

### Security and compliance

- Every image pinned by digest, with Trivy and CISA KEV image scanning,
  reviewed exceptions, SBOMs, licence evidence, and OCI labels on local
  images.
- The locally built images upgrade their Debian packages at build time, and
  the `litellm-pgvector` connector installs patched LiteLLM, FastAPI,
  Starlette, AnyIO, and python-multipart releases over its upstream pins
  (`pgvector/requirements-overrides.txt`), so the release image gate has no
  KEV or fixable Critical findings.
- Edge hardening against DDoS, MITM, and prompt injection: TLS policy, HSTS,
  per-client rate limits, and per-key defaults.
- Least-privilege observability database roles, restricted Docker socket
  access through socket proxies, and resource limits on every service.
- [Third-party licence register](docs/third-party-licences.md) with a drift
  check, [EU technical-control baseline](docs/eu-technical-controls.md), and
  the [dev-to-prod promotion register](docs/dev-to-prod-promotion-register.md).

### Operations

- `uv`-based maintenance scripts that log to the console and a private file,
  covering database backup and restore, LiteLLM upgrade compatibility checks,
  a regression suite, and rollback.
- Quarterly [operations game day](OPERATIONS-GAME-DAY.md) with a preflight
  check, and [hardware sizing](docs/hardware-sizing.md) for two-host
  deployments.

### Delivery and documentation

- Equivalent CI/CD on GitHub Actions and GitLab CI: hermetic validation,
  ShellCheck, Hadolint, Actionlint, Gitleaks, image and dependency
  vulnerability gates, and a development container build.
- Tag-triggered release publication of the source bundle and `SHA256SUMS`.
- Animated architecture diagrams generated from Mermaid sources, and a
  versioned documentation site built with Zensical and published to GitHub
  Pages or GitLab Pages.

[Unreleased]: https://github.com/bella-1337/effective-spoon/compare/v0.4.1...HEAD
[v0.4.1]: https://github.com/bella-1337/effective-spoon/compare/v0.4.0...v0.4.1
[v0.4.0]: https://github.com/bella-1337/effective-spoon/compare/v0.3.1...v0.4.0
[v0.3.1]: https://github.com/bella-1337/effective-spoon/compare/v0.3.0...v0.3.1
[v0.3.0]: https://github.com/bella-1337/effective-spoon/compare/v0.2.1...v0.3.0
[v0.2.1]: https://github.com/bella-1337/effective-spoon/compare/v0.2.0...v0.2.1
[v0.2.0]: https://github.com/bella-1337/effective-spoon/compare/v0.1.1...v0.2.0
[v0.1.1]: https://github.com/bella-1337/effective-spoon/compare/v0.1.0...v0.1.1
[v0.1.0]: https://github.com/bella-1337/effective-spoon/releases/tag/v0.1.0
