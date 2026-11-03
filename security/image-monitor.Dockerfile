FROM docker:29.9.2-cli@sha256:5e6baf67325a79f64ed3bed6863954d5e05d0f3d4e57cbdb14809346814bbfdb AS docker_cli
FROM aquasec/trivy:0.76.1@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c AS trivy
FROM ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca

COPY --from=docker_cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker_cli /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
COPY --from=trivy /usr/local/bin/trivy /usr/local/bin/trivy

# Debian removes superseded package versions from its mirrors, so the base
# image's packages are upgraded at build time; the CI image scan checks them.
RUN apt-get update \
    && apt-get upgrade --yes --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

ENV DOCKER_CONFIG=/tmp/docker-config \
    HOME=/tmp/home \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_PYTHON_PREFERENCE=only-system \
    XDG_CACHE_HOME=/tmp/xdg-cache \
    XDG_DATA_HOME=/tmp/xdg-data

WORKDIR /app
COPY --chown=65532:65532 scripts/maintenance_common.py scripts/monitor-image-vulnerabilities.py scripts/generate-image-sbom.py scripts/run-image-security-scheduler.py /app/scripts/
COPY --chown=65532:65532 security/exceptions.toml /app/security/exceptions.toml

USER 65532:65532
ENTRYPOINT ["uv", "run", "--script", "/app/scripts/run-image-security-scheduler.py"]
