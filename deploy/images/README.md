# In-house container images

Four images this chart runs are **not** published by a vendor. The upstream
Compose stack built them from this repository at `docker compose build` time.
A cluster cannot do that, so they must be built once and pushed to the
additional registry before the chart can install.

Everything else in `values.yaml` under `images:` is a third-party image, pinned
by digest. Those come from their own registries and only need mirroring if your
cluster cannot reach them.

## What must be published

| Values key | Built from | Dockerfile | Tag the chart expects |
| --- | --- | --- | --- |
| `opensearchTools` | this repository | `opensearch/tools/Dockerfile` | `0.1.0` |
| `pgvector` | this repository + `BerriAI/litellm-pgvector@5bd8f3a` | `pgvector/Dockerfile` | `5bd8f3ab1fa9129e758a2c4b17ba1c6f3047ba24` |
| `lens` | `BerriAI/lens@b2ac5f8` | `deploy/runtime/Dockerfile` (upstream) | `1.108.0-rc.1` |
| `imageSecurityMonitor` | this repository | `security/image-monitor.Dockerfile` | `0.1.0` |

`lens` has no upstream release image, which is why upstream pinned a release
candidate commit and built it. The chart keeps that: it expects the image in
your registry rather than a vendor tag that does not exist.

## Build and push

Run from a checkout of `https://github.com/bella-1337/effective-spoon` at the
commit recorded in `Chart.yaml` (`litellm-stack/upstream-commit`, currently
`603f178`).

```sh
export REGISTRY=registry.inhouse.example
export REVISION=$(git rev-parse HEAD)

# 1. OpenSearch tools: certificate generation, cluster setup, dashboards, bridge.
docker build \
  --file opensearch/tools/Dockerfile \
  --label org.opencontainers.image.revision="$REVISION" \
  --tag "$REGISTRY/litellm/litellm-opensearch-tools:0.1.0" .
docker push "$REGISTRY/litellm/litellm-opensearch-tools:0.1.0"

# 2. pgvector connector, built against the pinned upstream commit.
docker build \
  --file pgvector/Dockerfile \
  --build-context upstream=https://github.com/BerriAI/litellm-pgvector.git#5bd8f3ab1fa9129e758a2c4b17ba1c6f3047ba24 \
  --label org.opencontainers.image.revision="$REVISION" \
  --tag "$REGISTRY/litellm/litellm-pgvector:5bd8f3ab1fa9129e758a2c4b17ba1c6f3047ba24" .
docker push "$REGISTRY/litellm/litellm-pgvector:5bd8f3ab1fa9129e758a2c4b17ba1c6f3047ba24"

# 3. Image security monitor: pinned Trivy plus the upstream scan scripts.
docker build \
  --file security/image-monitor.Dockerfile \
  --label org.opencontainers.image.revision="$REVISION" \
  --tag "$REGISTRY/litellm/litellm-image-security-monitor:0.1.0" .
docker push "$REGISTRY/litellm/litellm-image-security-monitor:0.1.0"

# 4. Lens, optional. Only needed for the lens profile.
docker build \
  --build-context lens=https://github.com/BerriAI/lens.git#b2ac5f8a289c3f77f02b81d67ca1ee075bfead6d \
  --file deploy/runtime/Dockerfile \
  --target runtime \
  --build-arg LENS_VERSION=1.108.0-rc.1 \
  --build-arg LENS_RUNTIME_IMAGE=cgr.dev/chainguard/wolfi-base@sha256:24f549a7fcace88e69b9557941168c74645688ecc4acf7fb6d734a6b74e9019d \
  --label org.opencontainers.image.revision=b2ac5f8a289c3f77f02b81d67ca1ee075bfead6d \
  --tag "$REGISTRY/litellm/litellm-lens:1.108.0-rc.1" .
docker push "$REGISTRY/litellm/litellm-lens:1.108.0-rc.1"
```

The Lens build step is the one place the chart's own script differs from the
Compose file: `--build-context`/`--file` are resolved against the checked-out
repository, and the upstream Compose file builds Lens from a remote git context
directly. Either form produces the same image; adapt if you build in CI.

## Point the chart at the registry

One setting covers every image, including the third-party ones:

```yaml
global:
  imageRegistry: registry.inhouse.example
```

With that set, `opensearchTools` resolves to
`registry.inhouse.example/litellm/litellm-opensearch-tools@sha256:…` or
`:0.1.0` depending on `useDigest`.

## Digests

The chart prefers a digest over a tag (`useDigest: true`). The four in-house
images ship with an empty `digest:` in `values.yaml` because their digests only
exist after your first push. Record them:

```sh
docker buildx imagetools inspect \
  "$REGISTRY/litellm/litellm-opensearch-tools:0.1.0" --format '{{json .Manifest.Digest}}'
```

Then set them so a rebuilt tag cannot change what a release runs:

```yaml
images:
  opensearchTools:
    digest: sha256:…
  pgvector:
    digest: sha256:…
  imageSecurityMonitor:
    digest: sha256:…
  lens:
    digest: sha256:…
```

Until a digest is set, an empty digest falls back to the tag and the chart still
installs; `useDigest` only takes effect for images that have one.

## Pull authentication

Create the pull secret in the release namespace and reference it:

```sh
kubectl create secret docker-registry inhouse-registry \
  --docker-server=registry.inhouse.example \
  --docker-username="$USER" \
  --docker-password="$TOKEN" \
  --namespace=litellm
```

```yaml
global:
  imagePullSecrets:
    - inhouse-registry
```

The security profile reuses the same secrets for Trivy, via a projected volume
that assembles a `config.json` from the `.dockerconfigjson` key of each.

## Verifying what a release runs

```sh
helm template litellm deploy/helm/litellm-stack \
  --set global.imageRegistry=registry.inhouse.example \
  --show-only templates/litellm.yaml | grep 'image:'
```

Every reference should point at your registry. If one still names a vendor
registry, that image is outside `global.imageRegistry` because its values entry
sets its own `registry:`; override it in your values file.
