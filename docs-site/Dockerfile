# Documentation site for the optional docs profile (compose.optional.yml).
#
# The container runs `zensical serve` on the repository's own Markdown, and
# Traefik routes DOCS_HOST to it and adds the security headers. It serves one
# version: the checked-out revision the stack was built from. The published
# versioned site with its version selector stays on GitHub or GitLab Pages.
#
# The build stage renders the diagrams (Graphviz) and installs the hash-locked
# Zensical toolchain (docs-site/requirements.txt) outside the docs directory,
# then runs the same strict build as scripts/build-docs.py, because
# `zensical serve` does not support strict mode. Graphviz stays in the build
# stage. docs-site/Dockerfile.dockerignore keeps the build context to the
# published files and the build scripts; .env and every other secret stay out.

FROM ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca AS build

# Debian removes superseded package versions from its mirrors, so install with
# a fresh package index.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends graphviz \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_PREFERENCE=only-system \
    MAINTENANCE_FILE_LOGGING=false

WORKDIR /src
COPY . .

# Toolchain outside /src: Zensical walks its whole docs directory.
RUN uv venv --quiet --python 3.13 /opt/zensical \
    && uv pip install --quiet --require-hashes --python /opt/zensical/bin/python \
         --requirement docs-site/requirements.txt \
    && python3 scripts/render-diagrams.py \
    && /opt/zensical/bin/zensical build --strict --clean --config-file mkdocs.yml \
    && rm -rf site .cache

FROM ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca

# Debian removes superseded package versions too; the CI image scan checks them.
RUN apt-get update \
    && apt-get upgrade --yes --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/zensical /opt/zensical
COPY --from=build /src /src
RUN chmod -R a=rX /src /opt/zensical

# The repository commit the stack was built from: the page banner names it and
# the edit buttons open the files at it. Compose passes
# STACK_REPOSITORY_REVISION; anything but a commit SHA is refused.
ARG STACK_REPOSITORY_REVISION=
RUN printf '%s' "$STACK_REPOSITORY_REVISION" | grep -Eqx '([0-9a-f]{7,40})?' \
    || { echo "STACK_REPOSITORY_REVISION must be a Git commit SHA" >&2; exit 1; }

# Read by mkdocs.yml (!ENV). One version, so no version selector.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    DOCS_VERSION=deployed \
    DOCS_VERSION_KIND=deployment \
    DOCS_VERSION_PROVIDER=none \
    DOCS_GIT_COMMIT=${STACK_REPOSITORY_REVISION} \
    DOCS_SOURCE_REF=${STACK_REPOSITORY_REVISION:-master} \
    DOCS_EDIT_URI=blob/${STACK_REPOSITORY_REVISION:-master}/

WORKDIR /src
USER 65532:65532
EXPOSE 8000
# Zensical writes its build to /src/site and its cache to /src/.cache; Compose
# mounts both as tmpfs on the otherwise read-only root.
CMD ["/opt/zensical/bin/zensical", "serve", "--dev-addr", "0.0.0.0:8000", "--config-file", "/src/mkdocs.yml"]
