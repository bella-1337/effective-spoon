<!-- human-version-of: docs/databases-and-pgvector.md sha256: 1c7fd2a2f2018fc74e6b0cd85aa13be9bb918e188e5d925f4a79767eee09d6af -->

# Databases and pgvector storage

[← Back to the README](../README.md#documentation). Run all commands from the repository root.

This page covers two things: checking the LiteLLM database schema after an
image change, and the separate pgvector database that stores vector embeddings
for the development profile. Read it after a LiteLLM upgrade, or before you use
or extend the vector store.

## Verify the LiteLLM database schema

LiteLLM schema and column names can change between image releases. Wait until
the selected proxy has completed migrations (and preferably served a request).
Then run the repository's read-only schema query against the matching
database:

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

Before you rely on the cost dashboard's figures, compare the reported
`LiteLLM_SpendLogs` columns with the SQL the dashboard uses.

The script's index and read-only-role statements are comments. Review and
apply them manually, and only when appropriate.

## pgvector storage

The stack deploys a separate PostgreSQL 16 database with pgvector 0.8.7. Its
named `pgvector-data` volume starts with an empty `sme-knowledge` store.

!!! note "Disk use"
    Docker named volumes grow with stored data; Compose does not preallocate
    50 GB. Monitor the volume and host filesystem, and alert before either is
    exhausted.

### Schema and network

- 1,024-dimensional vectors for the multilingual `bge-m3` embedding model.
- An HNSW cosine-distance index.
- JSON metadata filtering.
- Cascading deletion by store.
- Content length constraints.

The database is reachable only on the internal Compose network. A separate
PostgreSQL exporter exposes database metrics to Prometheus.

### The litellm-pgvector connector

The development profile builds BerriAI's official `litellm-pgvector` connector
from commit `5bd8f3ab1fa9129e758a2c4b17ba1c6f3047ba24`.

That commit pins releases with known exploited and critical vulnerabilities.
So `pgvector/requirements-overrides.txt` installs patched LiteLLM, FastAPI,
Starlette, AnyIO, and python-multipart releases over its requirements, and the
build fails if `pip check` finds them inconsistent. Remove an override once
upstream pins a fixed release.

The connector:

- is internal only;
- authenticates every vector-store request; and
- uses development LiteLLM to embed search queries with the remote `bge-m3`
  model.

`sme-knowledge` is declared in `litellm/config.dev.yaml`. It therefore appears
in the development LiteLLM Vector Stores UI, and it survives proxy database
resets, because the registration is repository-owned configuration.

### Start and validate the store

Set `PG_VECTOR_API_KEY` to a separate, cryptographically random secret in
`.env`, then build and start the development profile:

```sh
docker compose --profile dev up -d --build
```

The official connector is intentionally development-only. Production neither
lists the store nor depends on the local vLLM model services. Before you enable
it in production, select and validate these independently: a production
embedding model, data residency, credentials, capacity, and availability.

Validate the store and rejection controls with:

```sh
./scripts/verify-pgvector.py
```

The check:

1. verifies that LiteLLM and the connector reject unauthenticated requests;
2. confirms the managed store appears in LiteLLM;
3. searches it through the official connector and local embedding model; and
4. attempts invalid database writes with an unknown store, the wrong vector
   dimensions, and empty content.

Every invalid attempt must fail, and the final vector row count must remain
unchanged.

### Automatic indexing of Xberg extractions

In the development profile, Xberg extraction indexing is disabled by default.
When the operator explicitly sets `XBERG_AUTO_INDEX_ENABLED=true`, successful
Xberg `extract` calls with trusted principal attribution are anonymized through
Presidio, chunked, and indexed in this store using remote `bge-m3` embeddings.

Opt-in automatic indexing is implemented by
`litellm/modules/xberg_auto_index.py` through LiteLLM's
`async_post_mcp_tool_call_hook`. It applies only to the `xberg` server's
`extract` tool, and stores the trusted pseudonymous principal in chunk
metadata.

Limits and behaviour:

- Each source is limited to 1,000,000 extracted characters and 256 chunks.
- Chunks are approximately 4,000 characters, with overlap.
- Repeating an extraction atomically replaces that source's existing chunks
  instead of creating duplicates.
- URL query strings, fragments, and credentials are not stored in source
  metadata.

What happens on failure:

- Extracted content must pass through Presidio before embedding. A Presidio
  failure fails closed for indexing, while the original tool response stays
  available.
- Indexing is best effort and runs as a bounded background task after
  extraction. An embedding-service or vector-database failure is logged, and
  the original Xberg response is still returned to the caller.
- The task times out after three minutes. It can be lost if the development
  proxy stops before it finishes; call `extract` again to retry.

The callback is enabled only in development, because the pgvector connector
and the remote `bge-m3` service are development-only.

### Requirements for production ingestion

Before production ingestion, select a reviewed component that provides:

- authenticated ingestion and search APIs;
- document-, tenant-, and team-level authorization;
- chunking, embedding, and prompt-injection controls;
- source identifiers that support complete update and erasure;
- bounded request sizes, rate limits, and generic errors; and
- retention, backup, restore, and data-subject deletion procedures.
