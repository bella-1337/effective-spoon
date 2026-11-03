# Image security and supply-chain evidence

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

## Monitor deployed image vulnerabilities

The repository includes a fail-closed, scheduled image-security monitor. It
uses Trivy `0.76.1` from an immutable container digest, downloads a fresh
vulnerability database, retrieves and validates the CISA Known Exploited
Vulnerabilities (KEV) catalog, and scans the platform-specific digest actually
used by each configured gateway and service container:

```bash
./scripts/maintenance.py security-scan
```

The default scope is every running service in the Compose project, without
reporting intentionally inactive profile services as unknown. The remote GPU
host's BGE-M3 and D3-mini containers belong to the separate `gpu-host/` project;
scan their pinned images on that host or in the registry. Restrict a manual
scan only when a narrower reviewed scope is required:

```bash
./scripts/maintenance.py security-scan \
  --services litellm-prod,litellm-dev
```

The monitor reads running-container metadata directly from Docker. It never
persists or logs rendered Compose configuration, which may contain interpolated
secrets. Digest-pinned registry images are scanned remotely so the Trivy
container does not need the Docker socket. A running local or mutable image is
reported as `not-applicable`/unknown instead of being presented as clean.

The normal scheduled mode reports findings and returns nonzero only for
operational failures. Use `--enforce` in a reviewed release gate to also fail on
an unexcepted CISA KEV match or fixable Critical finding:

```bash
./scripts/maintenance.py security-scan --enforce
```

### Reports, state, and metrics

Runtime output is ignored by Git and separated by sensitivity:

| Directory | Mode | Contents |
|---|---:|---|
| `security-state/` | `0700` | Private state, Trivy and uv caches, and scheduler lock |
| `security-reports/` | `0700` | Timestamped private Trivy JSON and KEV evidence; files are `0600` |
| `security-metrics/` | `0755` | One aggregate `0644` Prometheus textfile with no CVE IDs or secrets |

Override these roots with `IMAGE_SECURITY_STATE_DIR`,
`IMAGE_SECURITY_REPORT_DIR`, and `IMAGE_SECURITY_METRICS_DIR`. Set
`IMAGE_SECURITY_RETENTION_DAYS` to change the default 30-day report retention,
and `IMAGE_SECURITY_COMMAND_TIMEOUT_SECONDS` to change the default 900-second
per-command limit.

Private JSON state is the source of truth. Every attempt regenerates the entire
Prometheus file, including explicit failed/never-successful sentinels. A failed
database update, registry request, KEV retrieval, or image scan therefore
cannot leave a stale `success=1` result. The metrics are written to a non-`.prom`
temporary file, flushed, synchronized, and atomically renamed without
client-side sample timestamps.

### Time-limited exceptions

Security exceptions live in `security/exceptions.toml`. Each exception must
identify the service, vulnerability, package, installed version, reviewer,
technical reason, and expiry date. Raw reports remain complete; exceptions
only affect the unexcepted policy metrics and `--enforce` result.

Do not add permanent wildcard exceptions. An expired exception is no longer
applied, and Grafana warns seven days before the earliest active exception
expires.

The CI image gate (`scripts/ci/images.py scan`) reads the same file for the
three images this repository builds. Because one image can serve several
services (for example the OpenSearch tools image), CI accepts a finding only
when exceptions cover every service that runs the image. See
[Local and continuous integration](development-workflow.md#local-and-continuous-integration).

### Prometheus, OpenSearch, and Grafana alerts

The Compose configuration enables node-exporter's official textfile collector
and mounts `security-metrics/` read-only. The rules under `grafana/alerting/`
become OpenSearch Alerting monitors by default and are also provisioned into
Grafana when the optional `grafana` profile runs. Recreate these services after
changing a rule:

```bash
docker compose up -d --force-recreate node-exporter opensearch-setup opensearch-bridge
docker compose --env-file .env --env-file .env.optional \
  --profile grafana up -d --force-recreate grafana
```

Provisioned rules alert on:

- unexcepted fixable Critical and CISA KEV findings;
- per-service scan failure or a result older than 36 hours;
- failed, missing, or stale scheduler runs;
- missing/unparseable node-exporter textfile metrics;
- Trivy database and KEV feed staleness;
- running images outside scan coverage; and
- exceptions expiring within seven days.
- production LiteLLM becoming unavailable after previously being healthy;
- PostgreSQL exporter loss and host disk exhaustion; and
- a missing or stale complete database backup.

The repository deliberately does not commit notification credentials. Set
`OPENSEARCH_ALERT_CHANNEL_ID` to an OpenSearch Notifications channel (see
[Alerting](observability.md#alerting)); with the Grafana profile, also configure a Grafana
contact point and notification policy. A firing rule without a routed channel is
visible in the UI but cannot notify an operator.

Traefik and Alloy reach Docker metadata through dedicated read-only socket
proxies with Docker API `POST` disabled. They do not mount the Docker socket
directly. Keep those proxies on their isolated internal networks and do not
enable additional API sections unless the consumer demonstrably requires them.

The same maintenance workflow verifies coding routing after upgrades. With the
development profile running, the check fails unless the D3 Decisions request
succeeds and the Auto Router response reports the decision classifier rather
than a silent fallback:

```bash
./scripts/maintenance.py upgrade-verify \
  --only dev-vllm-embedding,coding-routing,observability
```

### Run the scheduler inside the stack

The optional `security` Compose profile runs the same scanner and policy inside
the stack. It executes once when started and then daily at 02:45 in the host's
timezone. Set the deployment account's Linux UID and GID in `.env.optional` so the
non-root monitor can write the three bind-mounted runtime directories:

```bash
IMAGE_SECURITY_UID=1000
IMAGE_SECURITY_GID=1000
```

Use `id -u` and `id -g` on the Linux deployment host rather than assuming those
example values. Start only the security services:

```bash
docker compose --env-file .env --env-file .env.optional \
  --profile security up -d --build image-security-monitor
docker compose --env-file .env --env-file .env.optional \
  logs -f image-security-monitor
```

Change the daily time or disable the immediate startup scan with
`IMAGE_SECURITY_SCHEDULE_HOUR`, `IMAGE_SECURITY_SCHEDULE_MINUTE`, and
`IMAGE_SECURITY_RUN_ON_START` in `.env.optional`. `COMPOSE_PROJECT_NAME` must match the
project label on the containers being scanned; `.env.example` pins it to
`litellm-stack` so it does not vary with the checkout directory.

The in-stack scheduler scans every running service. Profile-specific services
that are intentionally stopped are not emitted as unknown coverage, while any
running immutable image outside the former fixed list is included automatically.

The monitor image contains the same pinned Trivy release and invokes it directly,
so it does not create nested scanner containers. It obtains only read-only
container and image metadata through the separately pinned
`image-security-docker-proxy`; the monitor itself never receives the host Docker
socket. The proxy is isolated on a dedicated, unpublished network, permits only
Docker `GET` requests for the container and image API sections, and denies all
write requests. The proxy necessarily mounts the Docker socket, so keep it
isolated and pinned as configured.

Stop and remove the in-stack scheduler with:

```bash
docker compose --env-file .env --env-file .env.optional \
  --profile security rm -sf image-security-monitor image-security-docker-proxy
```

### Install the nightly Linux host schedule

The alternative host-scheduled mode uses a system-level systemd timer. Install
it at 02:45 local time while explicitly selecting a non-root account that already
has access to the Docker daemon:

```bash
sudo /usr/local/bin/uv run --script scripts/maintenance.py \
  security-schedule install \
  --service-user litellm \
  --uv-path /usr/local/bin/uv \
  --project litellm-stack
```

Choose another time when needed:

```bash
sudo /usr/local/bin/uv run --script scripts/maintenance.py \
  security-schedule install \
  --service-user litellm \
  --uv-path /usr/local/bin/uv \
  --project litellm-stack \
  --hour 3 \
  --minute 30
```

Replace `litellm` and `/usr/local/bin/uv` with the deployment account and the
absolute `uv` path on the host. Do not use `root` as the service account. The
installer writes `litellm-stack-image-security.service` and
`litellm-stack-image-security.timer` under `/etc/systemd/system`, enables the
persistent timer, and grants write access only to the state, report, and metrics
directories. The service applies systemd filesystem, device, privilege, kernel,
and process hardening while retaining the network and Docker access required by
the scanner. Output is available in the system journal:

When the deployment uses non-default runtime locations, pass them explicitly
rather than relying on environment preservation through `sudo`:

```bash
--state-dir /var/lib/litellm-security/state \
--report-dir /var/lib/litellm-security/reports \
--metrics-dir /srv/litellm-stack/security-metrics \
--exceptions-file /srv/litellm-stack/security/exceptions.toml
```

```bash
systemctl status litellm-stack-image-security.timer
journalctl -u litellm-stack-image-security.service
```

Inspect or remove the schedule through the maintenance entry point:

```bash
./scripts/maintenance.py security-schedule status
sudo /usr/local/bin/uv run --script scripts/maintenance.py \
  security-schedule uninstall
```

The scanner service fails if the selected account cannot access Docker. Verify
that access before installation with `sudo -u litellm docker info`. Keep the
working tree and `security-metrics/` path accessible to both that account and
Docker.

Use either the Compose scheduler or the systemd timer for a deployment, not both.
The scanner lock prevents concurrent corruption, but duplicate schedules add no
coverage and can create avoidable failed-run alerts.

Before enabling vulnerability pages, verify scanner-health alerts by testing a
missing metrics file, unavailable registry, failed Trivy database update, stale
KEV feed, and a partial service-scan failure. Enable KEV/Critical notification
routing only after these failure signals are proven.

## Generate SBOMs and licence evidence

The licence record for the stack is the curated
[third-party licence register](third-party-licences.md). The
`doctor`/game-day contract check and `scripts/ci/validate.sh` fail when an image,
build base image, auxiliary deployment image, or pinned model revision is not
listed there, so every tag change must update the register in the same commit.

Generated SBOMs provide the matching evidence of what actually ran. They are
produced by the same pinned Trivy release from the image digests that are
running, not from the Compose files, so profiles, overrides, and the host
platform are reflected exactly:

```bash
./scripts/maintenance.py sbom
./scripts/maintenance.py sbom --services litellm-prod,litellm-dev
```

- Registry images are resolved to their platform-specific digest and read
  remotely. The three locally built images (`litellm-pgvector`,
  `litellm-opensearch-tools`, `litellm-image-security-monitor`) are exported
  with `docker image save` to a temporary archive below
  `security-state/trivy-cache/sbom-input/` and keyed by their local image ID.
  The archive is deleted after the scan. Reserve free disk space of about the
  largest local image's size.
- Each new digest produces one CycloneDX JSON SBOM and, unless
  `IMAGE_SECURITY_SBOM_LICENCE_SCAN=false`, one raw Trivy full licence scan.
  Evidence for an existing digest is reused; `--refresh` regenerates it.
- Every run writes a stack-level CycloneDX SBOM under
  `security-reports/sbom/runs/` that lists each running image with its services,
  digest, platform, OCI labels, and a BOM-Link to its image SBOM, plus a
  summary of licence categories. This file is the audit snapshot of the stack
  on that date.
- Each run prints one `REVIEW:` line per image with the number of licences
  Trivy classifies as forbidden, restricted, reciprocal, or unknown, and names
  the forbidden ones (for example AGPL or SSPL). Compare them with the register.
  Debian and Ubuntu base images always report many GPL/LGPL and non-SPDX
  package licences; these are expected and acceptable for internal use.
- The command exits nonzero when any running service could not be covered.

| Path | Mode | Contents |
|---|---:|---|
| `security-reports/sbom/images/` | `0700` | Per-image `*.cdx.json`, `*.licences.json`, and `*.meta.json`; files are `0600` |
| `security-reports/sbom/runs/` | `0700` | One `stack-<UTC timestamp>.cdx.json` per run; files are `0600` |

SBOMs reveal exact package versions and therefore remain private and are never
committed. `IMAGE_SECURITY_SBOM_RETENTION_DAYS` (default 400) removes older
stack runs and any image evidence that no retained run references; the newest
run is always kept. The 30-day vulnerability-report retention does not touch
the `sbom/` directory.

Both schedulers generate SBOMs for new digests after each scan: the Compose
`security` profile unless `IMAGE_SECURITY_SBOM_ENABLED=false`, and the systemd
timer unless it was installed with `--no-sbom`. If the scan step of the timer
fails, the SBOM step is skipped for that run and catches up on the next run.
SBOM generation shares the scanner lock, so it never runs concurrently with a
vulnerability scan.

Model weights are not inside any image and therefore not in the SBOMs. They are
pinned to immutable revisions (BGE-M3 in `gpu-host/compose.yml` and
`D3_MODEL_REVISION` in `gpu-host/deploy.py`) and recorded in the register.

## Labels of locally built images

The three images this repository builds carry the standard OCI image labels,
set in the Compose `build.labels` of `litellm-opensearch-tools`,
`litellm-pgvector`, and `litellm-image-security-monitor`:

| Label | Value |
|---|---|
| `org.opencontainers.image.vendor` | `STACK_DOMAIN`, identifying the organisation that builds and operates the image |
| `org.opencontainers.image.title`, `.description` | Image name and purpose |
| `org.opencontainers.image.version` | The Compose image tag |
| `org.opencontainers.image.revision` | `STACK_REPOSITORY_REVISION`, or the upstream commit for `litellm-pgvector` |
| `org.opencontainers.image.licenses` | `LicenseRef-Proprietary` for repository code; `MIT` for `litellm-pgvector` |
| `org.opencontainers.image.base.name` | The final `FROM` image of the Dockerfile, without digest |
| `org.opencontainers.image.source`, `.url` | `STACK_REPOSITORY_URL` (empty when unset), or the upstream repository for `litellm-pgvector` |
| `org.opencontainers.image.created` | Always empty; see below |

Build with the repository commit recorded:

```bash
export STACK_REPOSITORY_REVISION="$(git rev-parse HEAD)"
docker compose --env-file .env --env-file .env.optional \
  --profile dev --profile security build
docker image inspect --format '{{json .Config.Labels}}' litellm-opensearch-tools:0.1.0
```

`doctor`, the game-day preflight, and `scripts/ci/validate.sh` fail when a
label is missing or when the vendor, version, or base-name label drifts from
`STACK_DOMAIN`, the image tag, or the Dockerfile, so a base-image tag update
must change the label and the register row in the same commit.

Base images also carry OCI labels; the `uv` base, for example, sets `source`,
`url`, and `created` for uv itself. Docker would otherwise copy those into the
stack's own images, so `source`, `url`, and `created` are always overridden.
`created` stays empty on purpose: Docker records the build time in the image
metadata, and a timestamp label would make otherwise identical builds differ. The SBOM generator copies all
`org.opencontainers.image.*` labels into the stack SBOM.
