# Development and delivery workflow

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

## Development container

Open the repository in VS Code and select **Dev Containers: Reopen in
Container**. The versioned configuration in `.devcontainer/` provides Python
3.12, `uv`, Docker Buildx, and Docker Compose while reusing the host Docker
daemon. The host and development container must use the same CPU architecture.

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

Run the core hermetic checks used by GitHub Actions:

```sh
./scripts/ci/validate.sh
```

The command requires Python 3.11 or newer. It compiles Python, runs the
standard-library unit suite, parses JSON and TOML, enforces immutable GitHub
Action references, validates every Compose profile without starting containers,
and runs the game-day safety checks. It creates temporary ignored validation
inputs only when local equivalents are absent and removes those temporary files
on exit. CI additionally runs ShellCheck, Actionlint, and a complete development
container build.

Checks that require external services are intentionally deferred for now:

- starting the full Compose stack and waiting for all service health checks;
- loading the GPU-backed BGE-M3 model or the CPU semantic router model;
- calling OpenAI or other provider APIs;
- downloading vulnerability databases or live CISA feeds;
- probing a deployed on-prem environment.

These are not silently treated as passing CI tests. Run the documented
operational verification procedures against an explicitly provisioned
environment when the required services and credentials are available.

## Continuous delivery

Pull requests and pushes to `master` run the reusable CI workflow. Pushing a
semantic-version tag such as `v1.2.3`, or manually selecting an existing tag in
the Release workflow, reruns CI and then publishes a GitHub release containing a
source bundle and `SHA256SUMS`. The `release` GitHub environment can be
configured with required reviewers before granting the publish job approval.
Existing releases are never replaced.

The release is a validated delivery artifact, not an automatic production
rollout. On-prem deployment still requires an operator to supply secrets and
TLS material, review the release, and follow the
[upgrade and rollback procedure](upgrades-and-rollback.md).
