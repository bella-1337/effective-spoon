# Contributing

Thank you for improving the LiteLLM stack. Every change follows the stack
maintenance workflow in `SKILL.md`; human and AI contributors alike also
follow `AGENTS.md`.

## Before you start

- Report security problems privately as described in `.github/SECURITY.md`,
  not in an issue.
- Open an issue for larger changes so the impact on the `prod` and `dev`
  profiles, data stores, and observability can be agreed first.
- Never commit `.env`, `.env.optional`, the Prometheus master-key file, or
  TLS private material. Use placeholders in tests and examples.

## Development environment

Open the repository in the development container (`.devcontainer/`), or
install Docker with Compose v2.20 or newer, Python 3.11 or newer, and `uv`.
See `docs/development-workflow.md`.

## Making a change

1. Branch from `master`.
2. Update every connected surface named in `SKILL.md`: Compose files, LiteLLM
   configuration, OpenSearch and Grafana assets, environment examples, and
   the guides under `docs/`.
3. When a service, network, profile, data store, external dependency, or flow
   step changes, edit the Mermaid sources in `diagrams/*.mmd` and check that
   they render with `./scripts/render-diagrams.py`. Commit only the `.mmd`
   files; CI renders the HTML for the documentation site.
4. When you add, move, or remove a Markdown file, update `nav:` in
   `mkdocs.yml`.
5. Add the change under `[Unreleased]` in `CHANGELOG.md`.

## Checks

Run before you open a pull request:

```sh
./scripts/ci/validate.sh
./scripts/build-docs.py build      # when Markdown or mkdocs.yml changed
python3 scripts/ci/images.py build # when a Dockerfile or image input changed
python3 scripts/ci/images.py scan --report-only
```

CI runs the same checks, plus ShellCheck, Hadolint, Ruff, Actionlint, zizmor,
Gitleaks, and the dependency scan, on GitHub Actions and GitLab CI. On GitHub,
`master` accepts changes only through a pull request whose required checks
pass, and CodeQL and OpenSSF Scorecard report to code scanning.

## Pull requests

- Keep each pull request to one concern and describe its operational impact.
- Write commit subjects in the imperative mood, for example
  `Pin the BGE-M3 model revision`.
- Pin new images by digest and new GitHub Actions by full commit SHA.

## Releases

Maintainers cut releases by tagging `master`; see
"Cut a release" in `docs/development-workflow.md`.
