# Documentation site for the optional docs profile (compose.optional.yml).
#
# The container runs `zensical serve` on the staged repository Markdown, and
# Traefik routes DOCS_HOST to it and adds the security headers. It serves one
# version: the checked-out revision the stack was built from. The published
# versioned site with its version selector stays on GitHub or GitLab Pages.
#
# The build stage renders the diagrams (Graphviz), stages the published files
# with scripts/build-docs.py (reader versions from docs-site/human/ swapped in,
# each page's own Markdown under llm/ for the "Copy for LLM" button), installs
# the hash-locked Zensical toolchain (docs-site/requirements.txt) outside the
# docs directory, and runs the same strict build as scripts/build-docs.py,
# because `zensical serve` does not support strict mode. The image serves the
# staged tree. Graphviz stays in the build stage.
# docs-site/Dockerfile.dockerignore keeps the build context to the published
# files and the build scripts; .env and every other secret stay out.

FROM ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca AS build

# Debian removes superseded package versions from its mirrors, so exact pins
# would break rebuilds. Graphviz only renders the diagrams here and is not
# copied into the docs image.
# hadolint ignore=DL3008
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
    && python3 scripts/build-docs.py stage --output /stage

# The strict build runs in the staged tree, then drops its output: the image
# serves the sources, and `zensical serve` builds again at start.
WORKDIR /stage
RUN /opt/zensical/bin/zensical build --strict --clean --config-file mkdocs.yml \
    && rm -rf site .cache

FROM ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca

# Debian removes superseded package versions too; the CI image scan checks them.
RUN apt-get update \
    && apt-get upgrade --yes --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/zensical /opt/zensical
COPY --from=build /stage /src
RUN chmod -R a=rX /src /opt/zensical

# The repository commit the stack was built from: the page banner names it and
# the edit buttons open the files at it. Compose passes
# STACK_REPOSITORY_REVISION; anything but a commit SHA is refused.
ARG STACK_REPOSITORY_REVISION=
RUN rev="$STACK_REPOSITORY_REVISION"; \
    case "$rev" in \
      "") ;; \
      *[!0-9a-f]*) echo "STACK_REPOSITORY_REVISION must be a Git commit SHA" >&2; exit 1 ;; \
      *) if [ "${#rev}" -lt 7 ] || [ "${#rev}" -gt 40 ]; then \
           echo "STACK_REPOSITORY_REVISION must be a Git commit SHA" >&2; exit 1; \
         fi ;; \
    esac

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
