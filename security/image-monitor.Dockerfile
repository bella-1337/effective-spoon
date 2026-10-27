FROM docker:29.9.2-cli@sha256:5e6baf67325a79f64ed3bed6863954d5e05d0f3d4e57cbdb14809346814bbfdb AS docker_cli
FROM aquasec/trivy:0.76.1@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c AS trivy
FROM ghcr.io/astral-sh/uv:0.9.6-python3.13-bookworm-slim@sha256:4b47bc764bc2eb124d81bbf6a79dc881acfcabe5f1f46199e7e29a0d9205baf7

COPY --from=docker_cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker_cli /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
COPY --from=trivy /usr/local/bin/trivy /usr/local/bin/trivy

ENV DOCKER_CONFIG=/tmp/docker-config \
    HOME=/tmp/home \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_PYTHON_PREFERENCE=only-system \
    XDG_CACHE_HOME=/tmp/xdg-cache \
    XDG_DATA_HOME=/tmp/xdg-data

WORKDIR /app
COPY --chown=65532:65532 scripts/maintenance_common.py scripts/monitor-image-vulnerabilities.py scripts/run-image-security-scheduler.py /app/scripts/
COPY --chown=65532:65532 security/exceptions.toml /app/security/exceptions.toml

USER 65532:65532
ENTRYPOINT ["uv", "run", "--script", "/app/scripts/run-image-security-scheduler.py"]
