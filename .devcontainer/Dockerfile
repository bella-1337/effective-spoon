FROM ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca AS uv

FROM mcr.microsoft.com/devcontainers/python:3.3.2-3.12-bookworm@sha256:75db3f306884600a165906c619a91668f27b2ffbee663af1829a0e5b2dc248d0

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
