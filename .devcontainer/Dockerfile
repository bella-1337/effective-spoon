FROM ghcr.io/astral-sh/uv:0.9.6-python3.13-bookworm-slim@sha256:4b47bc764bc2eb124d81bbf6a79dc881acfcabe5f1f46199e7e29a0d9205baf7 AS uv

FROM mcr.microsoft.com/devcontainers/python@sha256:7876580dc67fd460fd962f004cbeb480027e9bbc0657096f1087db11f9eaff39

USER root

RUN rm -f /etc/apt/sources.list.d/yarn.list

COPY --from=uv /usr/local/bin/uv /usr/local/bin/uv
COPY --from=uv /usr/local/bin/uvx /usr/local/bin/uvx

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_CACHE_DIR=/home/vscode/.cache/uv \
    UV_LINK_MODE=copy \
    UV_PYTHON_PREFERENCE=only-system

USER vscode
