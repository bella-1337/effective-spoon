# Databases and pgvector storage

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

## Verify the LiteLLM database schema

LiteLLM schema and column names can change between image releases. After the
selected proxy has completed migrations (and preferably served a request), run
the repository's read-only schema query against the matching database:

```sh
# Production
docker compose exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d litellm_prod' \
  < scripts/verify-schema.sql

# Development
docker compose exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d litellm_dev' \
  < scripts/verify-schema.sql
```

Compare the reported `LiteLLM_SpendLogs` columns with the SQL used by the cost
dashboard before relying on its figures. The script's index and read-only-role
statements are comments; review and apply them manually only when appropriate.

## pgvector storage

The stack deploys a separate PostgreSQL 16 database with pgvector 0.8.7. Its
named `pgvector-data` volume starts with an empty `sme-knowledge` store. In the development profile, Xberg extraction indexing is disabled by default.
When the operator explicitly sets `XBERG_AUTO_INDEX_ENABLED=true`, successful
Xberg `extract` calls with trusted principal attribution are anonymized through
Presidio, chunked, and indexed in this store using local `bge-m3` embeddings.
Docker named volumes grow with stored data; Compose does not preallocate 50 GB.
Monitor the volume and host filesystem and alert before either is exhausted.

The schema uses 1,024-dimensional vectors for the local multilingual `bge-m3`
embedding model, an HNSW cosine-distance index, JSON metadata filtering,
cascading deletion by store, and content length constraints. The database is
only reachable on the internal Compose network. A separate PostgreSQL exporter
exposes database metrics to Prometheus.

The development profile builds BerriAI's official `litellm-pgvector` connector
from commit `5bd8f3ab1fa9129e758a2c4b17ba1c6f3047ba24`. The connector is internal
only, authenticates every vector-store request, and uses development LiteLLM to
embed search queries with the vLLM-backed `bge-m3` model. `sme-knowledge` is declared
in `litellm/config.dev.yaml`, so it appears in the development LiteLLM Vector
Stores UI and survives proxy database resets because the registration is
repository-owned configuration.

Set `PG_VECTOR_API_KEY` to a separate, cryptographically random secret in
`.env`, then build and start the development profile:

```sh
docker compose --profile dev up -d --build
```

The official connector is intentionally development-only. Production neither
lists the store nor depends on the local vLLM model services. Before enabling it in
production, select and validate a production embedding model, data residency,
credentials, capacity, and availability independently.

Validate the store and rejection controls with:

```sh
./scripts/verify-pgvector.py
```

The check verifies unauthenticated rejection at LiteLLM and the connector,
confirms the managed store appears in LiteLLM, searches it through the official
connector and local embedding model, and attempts invalid database
writes with an unknown store, the wrong vector dimensions, and empty content.
Every invalid attempt must fail and the final vector row count must remain
unchanged.

Opt-in automatic indexing is implemented by
`litellm/modules/xberg_auto_index.py` through LiteLLM's
`async_post_mcp_tool_call_hook`. It applies only to the `xberg` server's
`extract` tool and stores the trusted pseudonymous principal in chunk metadata.
Each source is limited to 1,000,000 extracted characters and
256 chunks; chunks are approximately 4,000 characters with overlap. Repeating
an extraction atomically replaces that source's existing chunks instead of
creating duplicates. URL query strings, fragments, and credentials are not
stored in source metadata.

Extracted content must pass through Presidio before embedding; a Presidio
failure fails closed for indexing while leaving the original tool response
available. Indexing is best effort and runs as a bounded background task after extraction:
an embedding-service or vector-database failure is logged while the original
Xberg response is still returned to the caller. The task times out after three
minutes and can be lost if the development proxy stops before it finishes; call
`extract` again to retry. The callback is enabled only in development because
the pgvector connector and local `bge-m3` service are development-only.

Before production ingestion, select a reviewed component that provides:

- authenticated ingestion and search APIs;
- document-, tenant-, and team-level authorization;
- chunking, embedding, and prompt-injection controls;
- source identifiers that support complete update and erasure;
- bounded request sizes, rate limits, and generic errors; and
- retention, backup, restore, and data-subject deletion procedures.
