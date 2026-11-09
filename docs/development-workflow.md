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
| `docs.yml` | `docs.yml` | Documentation site build and Pages publication |
| `scorecard.yml` | None (GitHub only) | OpenSSF Scorecard |

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
| Python lint (Ruff, `ruff.toml`) | `scripts/ci/static-checks.sh python` | Same |
| GitHub workflow lint (Actionlint) | `actionlint` | Same |
| Workflow and Dependabot security audit (zizmor, `.github/zizmor.yml`) | `scripts/ci/static-checks.sh workflows` | Same |
| Secret scan of the full Git history (Gitleaks, `.gitleaks.toml`) | `scripts/ci/static-checks.sh secrets` | Same |
| Build the four local images (including the optional `litellm-lens`) and verify their OCI labels | `python3 scripts/ci/images.py build` | Changes to image inputs, nightly, manual runs, and tags |
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

Ruff checks syntax errors, Pyflakes, and flake8-bugbear rules only: likely
defects, not formatting. zizmor audits the workflows and `dependabot.yml` for
template injection, credential persistence, over-broad permissions, unpinned
actions, and missing Dependabot cooldowns. On GitHub it receives the read-only
job token and also runs its online audits, such as impostor-commit detection
for the pinned action SHAs; GitLab runs it offline. Silence a finding inline
with `# zizmor: ignore[<audit>]` and a reason, or in `.github/zizmor.yml`.

GitHub also scans the repository itself, outside these workflows:

- **CodeQL** default setup (a repository setting, no workflow file) analyses
  the Python code and the GitHub Actions workflows on every pull request and
  push to `master`.
- **OpenSSF Scorecard** (`scorecard.yml`) rates the repository weekly and on
  every push to `master`, uploads its findings to code scanning, and publishes
  the score behind the README badge.

Both report under **Security > Code scanning**. Neither is a required check.

When Gitleaks reports a committed secret, rotate the credential first. Then
allowlist the finding's fingerprint in a `.gitleaksignore` file; rewriting
history alone does not revoke a leaked credential.

The development container job builds `.devcontainer/` with the Dev Containers
CLI. It proves the pinned base image, the copied `uv`, and the locked
`docker-outside-of-docker` feature still resolve. It is not part of the
release gate because the release bundle does not contain the container.

GitHub disables a public repository's scheduled workflows after 60 days
without repository activity. If the nightly supply-chain scan or the weekly
development container build stops, re-enable it under **Actions**.

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
- Dependabot (`.github/dependabot.yml`) updates pins only on GitHub, and
  ruleset, CodeQL, Scorecard, and immutable-release protection exist only
  there. On a
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

## Dependency updates

Dependabot (`.github/dependabot.yml`) opens weekly pull requests for the
GitHub Actions SHAs, the Dockerfile base images, the pinned Python
requirements, the development container feature, and the image pins in
`docker-compose.yml`, `compose.optional.yml`, and `gpu-host/compose.yml`.
Every entry waits seven days after an upstream release before proposing it.

For the Compose files:

- LiteLLM and the locally built `litellm-*` images are ignored. LiteLLM moves
  through the [upgrade and rollback procedure](upgrades-and-rollback.md).
- Major versions are ignored: a new PostgreSQL, OpenSearch, ClickHouse, Redis,
  or Grafana major needs a planned migration.
- OpenSearch with OpenSearch Dashboards, the two Langfuse images, and the two
  Presidio images each move in one pull request, because each pair must run
  the same version.
- A digest-only update passes CI unchanged. A pull request that moves a tag
  fails validation until you update its row in the
  [component versions](component-versions.md) table and the
  [third-party licence register](third-party-licences.md) on the same branch;
  review the upstream release notes and licence while you do.

## Continuous delivery

Pull requests and pushes to `master` run the reusable CI workflow. Pushing a
semantic-version tag such as `v1.2.3`, or running the Release workflow manually
on an existing tag (`gh workflow run release.yml --ref v1.2.3`, or the tag
under **Use workflow from**), reruns CI and the enforcing supply-chain checks
on the tagged commit; a manual run on a branch fails at once. The publish job
then builds the bundle with `scripts/ci/release-bundle.sh` and creates a GitHub
release containing the source bundle and `SHA256SUMS`. In a public repository
it also records a build-provenance attestation for the bundle. The `release` GitHub environment admits only `v*.*.*` tags and can
also require a reviewer before the publish job runs. Releases are immutable:
GitHub locks the tag and the uploaded assets once a release is published and
records a release attestation, and the `release-tags` ruleset stops anyone
moving or deleting a `v*` tag. Existing releases are never replaced.

In GitLab, a semantic-version tag pipeline runs every check, then offers the
manual `publish-release` job in the `release` environment. Protect that
environment to restrict who may start it. The job refuses an existing release,
uploads the same bundle and `SHA256SUMS` to the project's generic package
registry (`litellm-stack/<tag>`), and creates a GitLab release linking both
files.

The default branch and release tags also rebuild the versioned documentation
site and publish it to GitHub Pages or GitLab Pages; see
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

### Security and delivery settings

Apply these once, and again after changing a file under `.github/rulesets/`.
Every command is idempotent.

```sh
REPO=bella-1337/effective-spoon

# Labels that dependabot.yml and the release-note categories use. Dependabot
# silently skips a label that does not exist.
gh label create dependencies --repo "$REPO" --force --color 0366d6 --description "Dependency updates"
gh label create ci           --repo "$REPO" --force --color 5319e7 --description "CI/CD workflows"
gh label create containers   --repo "$REPO" --force --color 1d76db --description "Container images"
gh label create python       --repo "$REPO" --force --color 2b67c6 --description "Python dependencies"
gh label create devcontainer --repo "$REPO" --force --color c5def5 --description "Development container"
gh label create security     --repo "$REPO" --force --color b60205 --description "Security fixes and hardening"

# Branch and tag rulesets, kept as code in .github/rulesets/. A ruleset with
# the same name is updated in place.
for file in .github/rulesets/*.json; do
  name=$(jq -r .name "$file")
  id=$(gh api "repos/$REPO/rulesets" --jq ".[] | select(.name == \"$name\") | .id")
  if [ -n "$id" ]; then
    gh api -X PUT "repos/$REPO/rulesets/$id" --input "$file" >/dev/null
  else
    gh api -X POST "repos/$REPO/rulesets" --input "$file" >/dev/null
  fi
done

# Immutable releases: lock each published release's tag and assets.
gh api -X PUT "repos/$REPO/immutable-releases"

# The release environment admits only semantic-version tags. This PUT replaces
# the environment's protection rules; add "reviewers" to require an approval.
gh api -X PUT "repos/$REPO/environments/release" --input - <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
gh api "repos/$REPO/environments/release/deployment-branch-policies" \
  --jq '.branch_policies[] | select(.type == "tag" and .name == "v*.*.*")' \
  | grep -q . \
  || gh api -X POST "repos/$REPO/environments/release/deployment-branch-policies" \
       -f name='v*.*.*' -f type=tag

# Actions: read-only default token, no pull-request approvals by Actions,
# full-length SHA pins enforced, and approval before any outside
# contributor's fork pull request runs a workflow.
gh api -X PUT "repos/$REPO/actions/permissions/workflow" \
  -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false
gh api "repos/$REPO/actions/permissions" \
  | jq '{enabled, allowed_actions, sha_pinning_required: true}' \
  | gh api -X PUT "repos/$REPO/actions/permissions" --input -
gh api -X PUT "repos/$REPO/actions/permissions/fork-pr-contributor-approval" \
  -f approval_policy=all_external_contributors

# Secret scanning with push protection, Dependabot alerts and security
# updates, and CodeQL default setup (Python and GitHub Actions).
gh api -X PATCH "repos/$REPO" --input - <<'JSON'
{"security_and_analysis": {
  "secret_scanning": {"status": "enabled"},
  "secret_scanning_push_protection": {"status": "enabled"}
}}
JSON
gh api -X PUT "repos/$REPO/vulnerability-alerts"
gh api -X PUT "repos/$REPO/automated-security-fixes"
gh api -X PATCH "repos/$REPO/code-scanning/default-setup" \
  -f state=configured -f query_suite=default
```

Check the result:

```sh
gh api "repos/$REPO/rulesets" --jq '.[] | [.name, .target, .enforcement] | @tsv'
gh api "repos/$REPO/immutable-releases"
gh api "repos/$REPO/actions/permissions" --jq .sha_pinning_required
gh api "repos/$REPO/code-scanning/default-setup" --jq '[.state, .languages]'
gh api "repos/$REPO" --jq .security_and_analysis
```

The `master` ruleset requires a pull request and the `validate`, `lint`,
`secrets` (CI), and `build` (Documentation) checks from the GitHub Actions app.
These four run on every pull request; the path-filtered supply-chain and
development container checks are not required, so a pull request that does not
trigger them can still merge. It needs no approving review, so a sole
maintainer can merge their own pull request once the checks pass. When you
rename a required job, update `.github/rulesets/master.json` in the same change
and reapply it, or every pull request waits for a check that never reports.
Immutable releases apply only to releases published after the setting is on.

## Planned improvements

These CI/CD additions are proposed but not yet implemented:

- **Compose smoke test.** Start the `prod` profile on a public GitHub-hosted
  runner (4 vCPU, 16 GB; the core and `prod` profile idles at about 7 GiB) with
  `docker compose --profile prod up --wait`, then probe the LiteLLM liveness
  endpoint, the Prometheus targets, and the OpenSearch status. Run it on
  changes to Compose or service configuration, on Dependabot Compose pull
  requests, and nightly. This closes most of the deferred "full Compose
  startup" check above.
- **Release SBOM attestation.** Move the release job to `actions/attest`, which
  records both the build provenance and an SBOM of the bundle
  (`sbom-path`), replacing `actions/attest-build-provenance`;
  `actions/attest-sbom` is being deprecated.
- **Dependency review.** Run `actions/dependency-review-action` on pull
  requests to block a newly introduced vulnerable or disallowed-licence pip
  package or action. It does not cover container images.
- **Link check.** Check the published documentation site's links weekly with
  lychee.
