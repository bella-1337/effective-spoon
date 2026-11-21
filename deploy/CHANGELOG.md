# Changelog

Notable changes to the `litellm-stack` Helm chart. The chart version is in
`Chart.yaml`; `appVersion` tracks the upstream Compose stack release it was
ported from.

## Unreleased

### Fixed

- Removed the duplicate `BGE_M3_API_KEY` entry under `secrets.values` in
  `values.yaml`.
- The `grafana-alerting` ConfigMap now renders when either Grafana or OpenSearch
  is enabled. Previously only Grafana rendered it, although the OpenSearch setup
  Job and bridge also mount it, so they could not start in a default install.
- The OpenSearch certificate Job now runs under its own ServiceAccount, with a
  Role and RoleBinding limited to the certificate Secret (`get` and `update` on
  that name, plus `create`). It had no ServiceAccount permissions to write the
  Secret it publishes.

### Added

- Documentation: getting started, secrets and ingress, upgrades and rollback,
  validation, architecture, and a values reference.

### Known issues

- `global.timezone` and `jobs.hooks.enabled` are defined in `values.yaml` but not
  used by any template.
- The certificate Job is a `post-install` hook that OpenSearch depends on, so
  `helm install --wait` is expected to time out. The Langfuse and Lens preflight
  hooks need the credentials Secret before Helm creates it on a first install
  without `secrets.existingSecret`. Moving the certificate Job and its RBAC to
  `pre-install` hooks would remove the first limitation. Neither has been tested
  on a cluster.

## 0.1.0

Initial port of upstream commit `603f178` (see `Chart.yaml`).
