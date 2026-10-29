FROM ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca

ENV HOME=/tmp/home \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_PYTHON_PREFERENCE=only-system

WORKDIR /app

# Every dependency, including transitive ones, is pinned by version and hash.
COPY opensearch/tools/requirements.txt /app/requirements.txt
RUN uv pip install --system --no-cache --require-hashes --only-binary :all: -r /app/requirements.txt

COPY opensearch/tools/generate-certs.sh /app/generate-certs.sh
COPY opensearch/tools/metrics.yml /app/metrics.yml
COPY opensearch/tools/litellm_opensearch /app/litellm_opensearch
COPY grafana/alerting /app/alerting

USER 65532:65532
ENTRYPOINT ["python", "-m", "litellm_opensearch"]
CMD ["--help"]
