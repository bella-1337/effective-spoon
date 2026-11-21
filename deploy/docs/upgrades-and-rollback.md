# Upgrades, rollback, and removal

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

```sh
export CHART=deploy/helm/litellm-stack
export NS=litellm
export RELEASE=litellm-stack
```

## Before you upgrade

1. Read the [changelog](../CHANGELOG.md) and the upstream release notes for the
   commit recorded in `Chart.yaml` under `litellm-stack/upstream-commit`.
2. Validate the new values and chart as described in [Validation](validation.md).
3. Back up the databases. The upstream
   [backup and restore guide](https://github.com/bella-1337/effective-spoon/blob/master/docs/backup-and-restore.md)
   uses Docker Compose scripts that do not run here; take a `pg_dump` of the
   `litellm_prod` and `litellm_dev` databases from the `postgres` StatefulSet
   instead, and snapshot the PersistentVolumes if your storage supports it.
4. For a LiteLLM image upgrade, change only `images.litellmProd` (and
   `images.litellmDev`) to a candidate that has both a tag and a digest. Keep image
   upgrades and enabling new capabilities as separate rollouts, as upstream does.

## Upgrade

```sh
helm upgrade "$RELEASE" "$CHART" --namespace "$NS" -f my-values.yaml
```

Do not use `--wait`; see
[Install](getting-started.md#5-install). Use `-f` with your full values file
rather than `--reuse-values`, which keeps stale values from earlier chart versions
and hides new defaults.

On upgrade the chart:

- runs the preflight hooks, then the post-upgrade hooks in the order listed in
  [Getting started](getting-started.md#5-install);
- re-runs the certificate Job. The upstream script reissues a leaf certificate
  only when it is missing or inside `opensearch.certificate.renewBeforeDays`, so
  this is also the rotation path;
- rolls workloads whose ConfigMaps changed, because the pod templates carry a
  `checksum/config` annotation;
- does not run the demo-data or initial image-scan Jobs again, which are
  `post-install` only.

### Generated secrets

When `secrets.existingSecret` is empty, the Secret template reads the existing
Secret with `lookup` and keeps every value it finds, so an upgrade does not
rotate passwords that the running databases still hold.

`lookup` returns nothing during `helm template` and `--dry-run` without a cluster.
Every such render shows new random values. Never feed `helm template` output to
`kubectl apply` for a release that already exists. That would overwrite the Secret
with fresh passwords, and the stateful services would no longer accept them.

## Roll back

```sh
helm history "$RELEASE" --namespace "$NS"
helm rollback "$RELEASE" <revision> --namespace "$NS"
```

A rollback restores the Kubernetes objects only. It does not undo anything the
hook Jobs or the applications did to their data, so plan for these limits:

- **Database migrations.** LiteLLM migrates its schema on start. After upgrading
  to a newer LiteLLM image, rolling back to an older one can fail against the
  migrated schema. Restore the pre-upgrade database dump together with the
  older image, as the upstream
  [upgrade guide](https://github.com/bella-1337/effective-spoon/blob/master/docs/upgrades-and-rollback.md)
  requires.
- **Secrets.** A rendered Secret is kept across revisions; a rollback does not
  restore older generated values.
- **Volumes.** Volume sizes can grow but not shrink. A rollback that lowers a
  `persistence.size` is rejected by Kubernetes for a StatefulSet and has no
  effect on an existing volume.

## Removal

```sh
helm uninstall "$RELEASE" --namespace "$NS"
```

This removes the workloads, Services, Ingress, and NetworkPolicies. It leaves
behind, deliberately:

| Left behind | Why | Remove with |
| --- | --- | --- |
| The credentials Secret (`helm.sh/resource-policy: keep`) | The volumes below still hold data protected by its passwords | `kubectl delete secret <release>-secrets` |
| StatefulSet volumes (PostgreSQL, OpenSearch, Prometheus, and the optional stores) | Kubernetes does not delete a StatefulSet's volumes with it | `kubectl delete pvc --selector app.kubernetes.io/instance=$RELEASE` |
| The OpenSearch certificate Secret | It is created by a hook Job, so Helm does not track it | `kubectl delete secret <release>-opensearch-certs` |
| Completed hook Jobs | `deletePolicy` removes a Job only when the same hook runs again | `kubectl delete job --selector app.kubernetes.io/instance=$RELEASE` |

Reinstalling into a namespace that still has the old volumes and Secret reuses
both, which is the intended recovery path. To start clean, delete all of the
above first. Deleting the volumes destroys the data permanently.
