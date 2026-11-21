# LiteLLM stack on Kubernetes

A Helm chart that ports the Docker Compose stack of
[bella-1337/effective-spoon](https://github.com/bella-1337/effective-spoon)
(the upstream) to Kubernetes: separate LiteLLM production and development
gateways behind an ingress, with PostgreSQL and pgvector, Presidio, SearXNG,
the Xberg MCP sandbox, Prometheus, and OpenSearch with OpenSearch Dashboards as
the default observability platform. Grafana and Loki, Langfuse, LiteLLM Lens,
and scheduled image scanning are optional.

The chart is in
[`deploy/helm/litellm-stack`](https://github.com/bella-1337/symmetrical-engine/tree/main/deploy/helm/litellm-stack).
The Validate workflow is set up to lint it and check the rendered manifests with
`kubeconform` against Kubernetes 1.27 and 1.30; it has not run in GitHub Actions
yet. The chart has not been installed on a cluster; the documentation says so
where it matters.

## Quick start

```sh
export CHART=deploy/helm/litellm-stack
# 1. Publish the in-house images (deploy/images/README.md), create the namespace and Secrets.
# 2. Write my-values.yaml, then validate it:
helm lint "$CHART" -f my-values.yaml
# 3. Install without --wait:
helm install litellm-stack "$CHART" --namespace litellm -f my-values.yaml
```

Follow [Getting started](deploy/docs/getting-started.md) for the full procedure,
and see the [chart overview](deploy/README.md) for the prerequisites.

## Documentation

The pages in this repository are also published as a searchable site at
<https://bella-1337.github.io/symmetrical-engine/>. It is built from the same
Markdown files you read on GitHub, with
[Zensical](https://zensical.org/); see
[Documentation site](deploy/docs/documentation-site.md) to preview it locally.

| Page | Covers |
| --- | --- |
| [Chart overview](deploy/README.md) | What the chart deploys, prerequisites, security notes |
| [Getting started](deploy/docs/getting-started.md) | Images, secrets, values file, validation, install, verification |
| [Secrets, TLS, and ingress](deploy/docs/secrets-and-ingress.md) | Credentials, hostnames, TLS, ingress annotations |
| [In-house container images](deploy/images/README.md) | Building and publishing the four images the chart expects |
| [Upgrades, rollback, and removal](deploy/docs/upgrades-and-rollback.md) | `helm upgrade`, rollback limits, what uninstall keeps |
| [Validation](deploy/docs/validation.md) | Lint, schema validation, full-render checks |
| [Architecture](deploy/docs/architecture.md) | Compose-to-Kubernetes mapping, components, network policies, limitations |
| [Values reference](deploy/docs/values-reference.md) | Every top-level values key and what it controls |
| [Changelog](deploy/CHANGELOG.md) | Notable changes to the chart |

## Repository layout

| Path | Purpose |
| --- | --- |
| [`deploy/helm/litellm-stack/`](https://github.com/bella-1337/symmetrical-engine/tree/main/deploy/helm/litellm-stack) | The Helm chart: templates, bundled configuration files, `values.yaml`, `values.schema.json` |
| [`deploy/ci/values-full.yaml`](https://github.com/bella-1337/symmetrical-engine/blob/main/deploy/ci/values-full.yaml) | Values that enable every optional component, used by the chart checks |
| [`deploy/docs/`](https://github.com/bella-1337/symmetrical-engine/tree/main/deploy/docs), [`deploy/images/`](https://github.com/bella-1337/symmetrical-engine/tree/main/deploy/images) | Chart documentation |
| [`mkdocs.yml`](https://github.com/bella-1337/symmetrical-engine/blob/main/mkdocs.yml), [`docs-site/`](https://github.com/bella-1337/symmetrical-engine/tree/main/docs-site) | Documentation site configuration, theme overrides, assets, and the hash-locked toolchain |
| [`scripts/`](https://github.com/bella-1337/symmetrical-engine/tree/main/scripts) | Site build script, CI entry points, and their tests |
| [`.github/workflows/`](https://github.com/bella-1337/symmetrical-engine/tree/main/.github/workflows) | The Validate and Documentation workflows |
| [`LICENSE`](https://github.com/bella-1337/symmetrical-engine/blob/main/LICENSE) | Apache License 2.0 |

## Licence

This repository is licensed under the
[Apache License 2.0](https://github.com/bella-1337/symmetrical-engine/blob/main/LICENSE).
The chart is derived from the upstream stack, whose own files are also licensed
under the
[Apache License 2.0](https://github.com/bella-1337/effective-spoon/blob/master/LICENSE).
Every third-party image the chart deploys is distributed under its own licence,
which you must comply with. Check the upstream
[third-party licence register](https://github.com/bella-1337/effective-spoon/blob/master/docs/third-party-licences.md)
before use. The chart and its documentation are provided as is, without warranty
of any kind.
