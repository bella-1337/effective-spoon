FROM python:3.11.18-slim-bookworm@sha256:94a8fee35252d06ab75856f15f12c7bae67b10e8a6da93c7fded82b2610926cd

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Debian removes superseded package versions from its mirrors, so exact pins
# would break rebuilds; the CI image scan checks the installed versions instead.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential curl postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=upstream requirements.txt .
RUN pip install --no-cache-dir --requirement requirements.txt

COPY --from=upstream . .
RUN prisma generate \
    && useradd --create-home --uid 65532 pgvector \
    && chown --recursive pgvector:pgvector /app /root/.cache \
    && chmod 0755 /root

USER pgvector

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
