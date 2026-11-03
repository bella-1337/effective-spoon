# Development and delivery workflow

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

## Development container

Open the repository in VS Code and select **Dev Containers: Reopen in
Container**. The versioned configuration in `.devcontainer/` provides Python
3.15, `uv`, Docker Buildx, and Docker Compose while reusing the host Docker
daemon. The host and development container must use the same CPU architecture.

On Docker Desktop the container reaches the host daemon through a `socat`
relay. `.devcontainer/post-start.sh` restarts that relay on every start so that
`docker run` output printed after a pause is not cut off; Linux hosts that
need no relay are unchanged.

On first creation, the container copies `.env.example` to the ignored `.env`
when one does not already exist and creates the ignored runtime directories. It
does not create `.env.optional`, generate production credentials, provider keys,
or TLS material. Replace placeholders before starting any service.

When opening a linked Git worktree, the worktree's common Git directory must
also be accessible inside the container. If the common directory is outside the
opened workspace, clone the repository into a container volume or add a local
bind mount for that directory; this limitation does not apply to a standard
clone.

## Local and continuous integration

Run the core hermetic checks used by both CI systems:

```sh
./scripts/ci/validate.sh
```

The command requires Python 3.11 or newer. It compiles Python, runs the
standard-library unit suite, parses JSON and TOML, enforces immutable GitHub
Action references and digest-pinned GitLab CI images, checks that the GitLab
entry point includes every file under `.gitlab/ci/`, validates every Compose
profile without starting containers, and runs the game-day safety checks. It
creates temporary ignored validation inputs only when local equivalents are
absent and removes those temporary files on exit.

GitHub Actions (`.github/workflows/`) and GitLab CI (`.gitlab/ci/`) run the
same scripts under `scripts/ci/` with the same digest-pinned tool images, so a
check behaves identically on both platforms. Each GitLab file mirrors the GitHub
workflow of the same name:

| GitHub Actions | GitLab CI | Jobs |
|---|---|---|
| `validate.yml` | `validate.yml` | Hermetic validation, linters, secret scan |
| `supply-chain.yml` | `supply-chain.yml` | Image build and vulnerability gate, dependency scan |
| `devcontainer.yml` | `devcontainer.yml` | Development container build |
| `release.yml` | `release.yml` | Release publication |
| `docs.yml` | `docs.yml` | Documentation site build and private Pages publication |

The root `.gitlab-ci.yml` only includes these files and defines the pipeline
rules, stages, and defaults. `.gitlab/ci/images.yml` holds every digest-pinned
job image, and `.gitlab/ci/templates.yml` holds the shared Docker-in-Docker
template. Add a new GitLab job file to the `include:` list; validation fails on
a file that is not included.

The checks:

| Check | Command | When it runs |
|---|---|---|
| Hermetic validation | `./scripts/ci/validate.sh` | Every pull or merge request, default-branch push, and tag |
| Shell lint (ShellCheck, every `*.sh`) | `scripts/ci/static-checks.sh shell` | Same |
| Dockerfile lint (Hadolint, `.hadolint.yaml`) | `scripts/ci/static-checks.sh dockerfiles` | Same |
| GitHub workflow lint (Actionlint) | `actionlint` | Same |
| Secret scan of the full Git history (Gitleaks, `.gitleaks.toml`) | `scripts/ci/static-checks.sh secrets` | Same |
| Build the three local images and verify their OCI labels | `python3 scripts/ci/images.py build` | Changes to image inputs, nightly, manual runs, and tags |
| Image vulnerability gate (Trivy, CISA KEV, `security/exceptions.toml`) | `python3 scripts/ci/images.py scan` | Same as the build |
| Python dependency scan (Trivy, fixable High and Critical) | `scripts/ci/static-checks.sh dependencies` | Same as the build |
| Development container build | `npx @devcontainers/cli build` | Changes to `.devcontainer/`, weekly or nightly, and manual runs |
| Versioned documentation site build (Zensical strict mode) | `scripts/ci/docs.sh check` | Every pull or merge request, default-branch push, and tag |

The image gate applies the policy of `maintenance.py security-scan --enforce`
to the images CI has just built: an unexcepted CISA KEV match or fixable
Critical finding fails. An exception must cover every service that runs the
image. On pull requests, merge requests, and branch pushes the scan reports
findings as warnings (`--report-only`) so a newly published CVE does not block
unrelated changes; nightly, manual, and release runs enforce it. Run the
container-based checks locally with the commands shown in the workflow files,
for example:

```sh
python3 scripts/ci/images.py build
python3 scripts/ci/images.py scan --report-only
```

When Gitleaks reports a committed secret, rotate the credential first. Then
allowlist the finding's fingerprint in a `.gitleaksignore` file; rewriting
history alone does not revoke a leaked credential.

The development container job builds `.devcontainer/` with the Dev Containers
CLI. It proves the pinned base image, the copied `uv`, and the locked
`docker-outside-of-docker` feature still resolve. It is not part of the
release gate because the release bundle does not contain the container.

Checks that require external services are intentionally deferred for now:

- starting the full Compose stack and waiting for all service health checks;
- calling the remote GPU host's BGE-M3 and D3-mini endpoints;
- calling OpenAI or other provider APIs;
- probing a deployed on-prem environment.

These are not silently treated as passing CI tests. Run the documented
operational verification procedures against an explicitly provisioned
environment when the required services and credentials are available.

### GitLab runner and project setup

- The `image-scan` and `devcontainer` jobs use Docker-in-Docker. Use a Docker
  executor with `privileged = true` and `volumes = ["/certs/client", "/cache"]`
  in the runner's `config.toml`. The other jobs need no privileges.
- Create a nightly pipeline schedule under **Build > Pipeline schedules**.
  GitLab cannot declare schedules in its CI files; without one, the
  enforcing image scan and the development container build run only on
  matching changes, tags, and manual pipelines.
- The release job's `release:` keyword runs `glab` from the pinned GitLab CLI
  image, which needs GitLab 18.0 or newer.
- Keep the project's CI/CD configuration file setting at its default,
  `.gitlab-ci.yml`; that file includes the jobs under `.gitlab/ci/`.
- Dependabot (`.github/dependabot.yml`) updates pins only on GitHub. On a
  GitLab-only installation, update digests manually or configure Renovate.
- The Docker-in-Docker jobs wait at most 120 seconds for the daemon
  (`scripts/ci/wait-for-docker.sh`) and then fail with the daemon's error, so a
  runner without `privileged = true` or the `/certs/client` volume fails fast.

### Run the pipelines locally

Both pipelines can run in the development container with
[`act`](https://github.com/nektos/act) for GitHub Actions and
[`gitlab-ci-local`](https://github.com/firecow/gitlab-ci-local) for GitLab. Both
test committed files, so commit first, ideally in a disposable clone. The CI
steps bind-mount `$PWD` into tool containers, and the host daemon resolves that
path, so the checkout must exist at the same path on the host and in the
container: open a clone outside `/workspaces`, or add a bind mount of the
workspace at its host path.

```sh
act pull_request --bind \
  -P ubuntu-24.04=catthehacker/ubuntu:act-24.04
gitlab-ci-local --privileged \
  --variable CI_PIPELINE_SOURCE=merge_request_event \
  --variable DOCKER_TLS_CERTDIR= --variable DOCKER_TLS_VERIFY= \
  --variable DOCKER_CERT_PATH= --variable DOCKER_HOST=tcp://docker:2375
```

`gitlab-ci-local` does not share the `/certs/client` volume with service
containers, so the variables above run Docker-in-Docker without TLS on the
job's private network. It also ignores `workflow:rules` and does not create
GitLab releases. The `act` runner image has no `gh`, so the GitHub publish step
fails locally after the bundle is built.

## Continuous delivery

Pull requests and pushes to `master` run the reusable CI workflow. Pushing a
semantic-version tag such as `v1.2.3`, or manually selecting an existing tag in
the Release workflow, reruns CI and the enforcing supply-chain checks. The
publish job then builds the bundle with `scripts/ci/release-bundle.sh` and
creates a GitHub release containing the source bundle and `SHA256SUMS`. In a
public repository it also records a build-provenance attestation for the
bundle. The `release` GitHub environment can be configured with required
reviewers before granting the publish job approval. Existing releases are never
replaced.

In GitLab, a semantic-version tag pipeline runs every check, then offers the
manual `publish-release` job in the `release` environment. Protect that
environment to restrict who may start it. The job refuses an existing release,
uploads the same bundle and `SHA256SUMS` to the project's generic package
registry (`litellm-stack/<tag>`), and creates a GitLab release linking both
files.

The default branch and release tags also rebuild the versioned documentation
site and, once enabled, publish it to GitHub Pages or GitLab Pages with access
restricted to repository members; see
[Documentation site](documentation-site.md#publishing).

The release is a validated delivery artifact, not an automatic production
rollout. On-prem deployment still requires an operator to supply secrets and
TLS material, review the release, and follow the
[upgrade and rollback procedure](upgrades-and-rollback.md).

### Cut a release

1. On `master`, move the `[Unreleased]` entries in
   [`CHANGELOG.md`](../CHANGELOG.md) under a new `[vMAJOR.MINOR.PATCH] -
   YYYY-MM-DD` heading, update the comparison links at the end of the file,
   and merge that change.
2. Run `./scripts/ci/validate.sh` and `scripts/ci/docs.sh check` on the merged
   commit.
3. Create an annotated tag on that commit and push it:

   ```sh
   git tag -a v1.2.3 -m "LiteLLM stack v1.2.3"
   git push origin v1.2.3
   ```

The tag push starts the release pipelines described above. Never move or
delete a published tag; fix a bad release with a new patch version.

## Repository settings

These settings live in GitHub, not in files, so apply them once with an
authenticated GitHub CLI (`gh auth login -s admin:org,repo`). Custom
properties need organisation-owner or property-admin rights.

```sh
REPO=bella-1337/effective-spoon

# About section: description, documentation site, and topics.
gh repo edit "$REPO" \
  --description "Self-hosted LiteLLM gateway stack on Docker Compose: prod and dev proxies, PII guardrails, OpenSearch observability, and supply-chain-gated releases." \
  --homepage "https://bella-1337.github.io/effective-spoon/" \
  --add-topic litellm,llm-gateway,ai-gateway,llmops,docker-compose,self-hosted \
  --add-topic opensearch,observability,prometheus,grafana-alloy,presidio,pgvector

# The security policy routes reports to private vulnerability reporting.
gh api -X PUT "repos/$REPO/private-vulnerability-reporting"

# Custom properties: list the organisation's schema, then set the values that
# the schema defines. Property names and allowed values come from the schema.
gh api "orgs/bella-1337/properties/schema"
gh api -X PATCH "repos/$REPO/properties/values" --input - <<'JSON'
{"properties": [
  {"property_name": "<name from the schema>", "value": "<allowed value>"}
]}
JSON
gh api "repos/$REPO/properties/values"
```

Suggested custom property values, to map onto the organisation's schema:

| Meaning | Value |
| --- | --- |
| System or product | `litellm-stack` |
| Repository type | Infrastructure / deployment configuration |
| Lifecycle | Active, released (`v0.1.0` and later) |
| Data classification | Internal (no secrets are committed; see the security policy) |
| Deployment | Self-hosted on-prem, Docker Compose |
| Compliance scope | EU technical controls (`docs/eu-technical-controls.md`) |
