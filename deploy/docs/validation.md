# Validation

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

Run these checks after any change to the chart or your values. None of them needs
a cluster. They do not replace an install on a test cluster.

```sh
export CHART=deploy/helm/litellm-stack
```

## 1. Chart checks

One script runs the static checks. The Validate workflow runs the same script as
its `chart` job on every pull request and every push to `main`:

```sh
scripts/ci/chart-checks.sh
```

It needs `helm`, [`kubeconform`](https://github.com/yannh/kubeconform), and
[`uv`](https://docs.astral.sh/uv/) (for `uvx yamllint`) on `PATH`, and network
access: `kubeconform` fetches the Kubernetes schemas from
`raw.githubusercontent.com`. It runs every check even when an earlier one fails,
prints which ones failed, and exits non-zero if any did. It checks:

| Check | What it catches |
| --- | --- |
| `helm lint`, default values and `deploy/ci/values-full.yaml` | Template errors, and values that `values.schema.json` rejects, including unknown top-level keys |
| `helm template`, same two value sets | Templates that fail to render. The full set enables every optional component, so templates that are only reached then are rendered too |
| `kubeconform -strict`, Kubernetes 1.27.0 and 1.30.0, on both renders | Objects that do not match the API schema. 1.27 is the `kubeVersion` floor in `Chart.yaml` |
| `yamllint` with only `key-duplicates` enabled, on `values.yaml` and `values-full.yaml` | A key repeated in a mapping. Helm silently keeps the last one, which hides mistakes such as a repeated secret name |

`helm lint` prints `icon is recommended` for `Chart.yaml`; that is informational.

The full set is the file
[`deploy/ci/values-full.yaml`](https://github.com/bella-1337/symmetrical-engine/blob/main/deploy/ci/values-full.yaml).
It enables the development gateway, the demo seed jobs, Xberg with its DNS
egress, Grafana, Langfuse, Lens, the security components, the node exporter, the
Kubernetes scrape targets, and Qdrant persistence, with example hostnames. It is
for these checks only. `xberg.egress.dns.clusterIP` is set only to satisfy the
guard that requires it; in a real release, install once and read the address
from the `xberg-dns` Service.

To check your own values as well, run `helm lint "$CHART" -f my-values.yaml`,
and pipe `helm template t "$CHART" --namespace stack -f my-values.yaml` into
`kubeconform -strict -summary -kubernetes-version 1.30.0 -`. Use the oldest
Kubernetes version you run.

### Tool versions

The script does not pin `helm`; it uses the one on `PATH`. It has passed with
Helm 3.16.4 and 3.22.1, the version listed for the `ubuntu-24.04` runner image
at the time of writing. The rest is pinned:

- `kubeconform` v0.8.0, downloaded in the `chart` job and checked against the
  SHA-256 from the release's `CHECKSUMS` file before use;
- `yamllint` 1.38.0, in the script, run with `uvx`;
- `uv` 0.9.30 through `astral-sh/setup-uv`, with its checksum.

When you raise one of them, change it in `.github/workflows/validate.yml` or
`scripts/ci/chart-checks.sh` and take the new checksum from the release.

## 2. Check references between objects

Linting and schema validation do not notice a Deployment that mounts a ConfigMap
the chart never renders. That kind of mistake only shows up as a pod stuck in
`ContainerCreating`. The script does not check this; do it by hand when you add
or rename an object. Render both value sets:

```sh
helm template t "$CHART" --namespace stack > default.yaml
helm template t "$CHART" --namespace stack -f deploy/ci/values-full.yaml > full-render.yaml
```

For each rendered pod template, confirm that:

- every ConfigMap, Secret, and PersistentVolumeClaim it mounts or reads exists in
  the render;
- every `secretKeyRef` key exists in the rendered Secret;
- every Service selector matches a pod, and every `targetPort` is exposed by it;
- every Ingress backend names a Service that exists;
- every ServiceAccount exists.

Run this on both renders. One reference is expected to be missing: the
`<release>-opensearch-certs` Secret, which the post-install certificate hook Job
creates at runtime. Anything else is a bug.

A past run of this check found a `grafana-alerting` ConfigMap rendered only when
Grafana was enabled while the OpenSearch setup Job also mounted it; see the
[changelog](../CHANGELOG.md).

## 3. Compare bundled files with upstream

The files under `files/` come from the upstream repository at the commit in
`Chart.yaml`. Compare them with a checkout of that commit when you change one
(clone it into your home directory and delete it afterwards, not under a
nearly-full root filesystem):

```sh
diff -r deploy/helm/litellm-stack/files/litellm /path/to/effective-spoon/litellm
```

The two Alloy files are expected to differ. They discover Kubernetes pods
instead of Docker containers and use `POD_NAMESPACE` and `RELEASE_NAME` for the
namespace and release filters.

## Continuous integration

The Validate workflow (`.github/workflows/validate.yml`) runs on pull requests,
on pushes to `main`, and on demand. Its jobs:

| Job | Runs |
| --- | --- |
| `chart` | `scripts/ci/chart-checks.sh`, section 1 |
| `docs-tests` | The unit tests of the documentation build: `uv run --no-project --with pyyaml python -m unittest discover -s scripts/tests -p "test_*.py" -v` |
| `workflows` | actionlint 1.7.12 and zizmor 1.32.0 on `.github/`, with `.github/zizmor.yml` |

The [Documentation workflow](documentation-site.md#publishing) builds the site in
strict mode on every pull request.

Neither workflow has run in GitHub Actions yet. The chart script, the unit
tests, actionlint, and zizmor have been run on a workstation, not through a
workflow.

## What these checks do not cover

- That images exist at the registry and digest the chart names.
- Admission controllers, PodSecurity levels, and your ingress controller's
  annotation policy.
- Runtime behaviour, including the hook order and the OpenSearch certificate
  hand-off. Install into a scratch namespace to test those.
