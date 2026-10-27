FROM ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca AS uv

FROM mcr.microsoft.com/devcontainers/python@sha256:28835c112fd097cb6d2fe1e195b660d66ae051859070a9b871fa2308f35897d3

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
