# LiteLLM stack on Kubernetes

A Helm chart that runs the [LiteLLM stack](https://github.com/bella-1337/effective-spoon)
on Kubernetes. It is a port of that repository's Docker Compose stack: separate
LiteLLM production and development gateways behind an ingress, with PostgreSQL,
pgvector, Presidio, SearXNG, Xberg MCP, Prometheus, and OpenSearch with
OpenSearch Dashboards as the default observability platform. Grafana/Loki,
Langfuse, LiteLLM Lens, and scheduled image scanning remain optional.

The chart lives in [`helm/litellm-stack`](https://github.com/bella-1337/symmetrical-engine/tree/main/deploy/helm/litellm-stack). Every
service in `docker-compose.yml` and `compose.optional.yml` has a Kubernetes
workload; the Compose profiles `prod`, `dev`, `demo`, `grafana`, `langfuse`,
`lens`, and `security` map to `*.enabled` values. See
[Architecture](docs/architecture.md) for what changed in the port.

## Prerequisites

- Kubernetes 1.27 or newer (`kubeVersion` in `Chart.yaml`)
- Helm 3 (the chart was validated with Helm 3.16.4)
- An ingress controller. The default annotations target ingress-nginx; see
  [Secrets, TLS, and ingress](docs/secrets-and-ingress.md#ingress)
- A default StorageClass, or `global.storageClass` set
- On every node that can run OpenSearch, `vm.max_map_count` of at least 262144:

  ```sh
  sudo sysctl -w vm.max_map_count=262144
  echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-opensearch.conf
  ```

  The chart does not set this itself.
- The four in-house images published to a registry your cluster can pull from;
  see [In-house container images](images/README.md)
- A TLS certificate Secret for the public hostnames, or cert-manager
- `kubectl` and, for the checks in [Validation](docs/validation.md), `python3`
- Optional: a remote GPU host serving the D3-mini decisions model and, for the
  development gateway, BGE-M3 embeddings, deployed with the upstream
  [`gpu-host/`](https://github.com/bella-1337/effective-spoon/tree/master/gpu-host)
  stack

Run all commands from the repository root.

## Getting started

Follow the [getting-started guide](docs/getting-started.md):

1. [Publish the in-house images](docs/getting-started.md#1-publish-the-in-house-images)
2. [Prepare the namespace and secrets](docs/getting-started.md#2-prepare-the-namespace-and-secrets)
3. [Write a values file](docs/getting-started.md#3-write-a-values-file)
4. [Validate the chart](docs/getting-started.md#4-validate-the-chart)
5. [Install](docs/getting-started.md#5-install)
6. [Verify the release](docs/getting-started.md#6-verify-the-release)

To upgrade, roll back, or remove a release, see
[Upgrades, rollback, and removal](docs/upgrades-and-rollback.md).

## Documentation

### Guides

| Guide | Covers |
| --- | --- |
| [Getting started](docs/getting-started.md) | Images, namespace, secrets, values file, validation, installing without `--wait`, verifying the release |
| [Secrets, TLS, and ingress](docs/secrets-and-ingress.md) | Generated versus existing Secrets, required formats, rotation, hostnames, TLS, ingress annotations |
| [Upgrades, rollback, and removal](docs/upgrades-and-rollback.md) | `helm upgrade` behaviour, preserved secrets, rollback limits, what uninstall keeps |
| [Validation](docs/validation.md) | Lint, schema validation with `kubeconform`, rendering with every component, cross-reference checks |
| [In-house container images](images/README.md) | Building and publishing the four images the chart expects, digests, pull secrets |

### Reference

| Document | Covers |
| --- | --- |
| [Changelog](CHANGELOG.md) | Notable changes to the chart |
| [Architecture](docs/architecture.md) | Compose-to-Kubernetes mapping, components, hook order, network policies, known limitations |
| [Values reference](docs/values-reference.md) | Every top-level values key, its default, and what it controls |

## Security notes

- Never commit credentials. Provide an existing Secret
  (`secrets.existingSecret`) or let the chart generate one, and review it
  before use.
- Do not apply `helm template` output to a cluster when secrets are generated.
  `lookup` returns nothing outside a live release, so every render produces new
  random values; see [Upgrades](docs/upgrades-and-rollback.md#generated-secrets).
- Keep `networkPolicies.enabled: true`. It reproduces the Compose network
  segmentation, including the Xberg sandbox allowing egress only to its proxy.
- Keep image digests set. Floating tags are discouraged by the upstream
  security notes; see [In-house container images](images/README.md#digests).
- Do not expose the development gateway to untrusted users.

## Licence

The chart is derived from the upstream stack, whose own files are licensed under
the
[Apache License 2.0](https://github.com/bella-1337/effective-spoon/blob/master/LICENSE).
Every third-party image the chart deploys is distributed under its own licence,
which you must comply with. Check the upstream
[third-party licence register](https://github.com/bella-1337/effective-spoon/blob/master/docs/third-party-licences.md)
before use. The stack is provided as is, without warranty of any kind.
