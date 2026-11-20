FROM docker:29.9.3-cli@sha256:5a7af0dcb0e46c803efe9b45371dd21cb500e5b4453250394ac6f06d49b6e6f1 AS docker_cli
FROM aquasec/trivy:0.76.2@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb AS trivy
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
