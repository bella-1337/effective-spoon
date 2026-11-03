FROM python:3.15.0-slim-bookworm@sha256:0e019bbdfb5d9874cb2389f1c2884b64aa2ac97c99960b5bc521cef978c0617d

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Debian removes superseded package versions from its mirrors, so exact pins
# would break rebuilds; the CI image scan checks the installed versions instead.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get upgrade --yes --no-install-recommends \
    && apt-get install --yes --no-install-recommends build-essential curl postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=upstream requirements.txt .
COPY pgvector/requirements-overrides.txt .
# The upstream commit pins releases with known KEV and Critical findings; the
# overrides replace them, and pip check proves the result is consistent.
RUN pip install --no-cache-dir --requirement requirements.txt \
    && pip install --no-cache-dir --requirement requirements-overrides.txt \
    && pip check

COPY --from=upstream . .
RUN prisma generate \
    && useradd --create-home --uid 65532 pgvector \
    && chown --recursive pgvector:pgvector /app /root/.cache \
    && chmod 0755 /root

USER pgvector

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
