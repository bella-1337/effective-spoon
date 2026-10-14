CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS vector_stores (
    id text PRIMARY KEY,
    name text NOT NULL,
    file_counts jsonb NOT NULL DEFAULT '{"in_progress":0,"completed":0,"failed":0,"cancelled":0,"total":0}'::jsonb,
    status text NOT NULL DEFAULT 'completed',
    usage_bytes integer NOT NULL DEFAULT 0 CHECK (usage_bytes >= 0),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    expires_after jsonb,
    expires_at timestamptz,
    last_active_at timestamptz
);

CREATE TABLE IF NOT EXISTS embeddings (
    id text PRIMARY KEY,
    vector_store_id text NOT NULL REFERENCES vector_stores(id) ON DELETE CASCADE,
    content text NOT NULL CHECK (char_length(content) BETWEEN 1 AND 50000),
    embedding vector(1024) NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS embeddings_vector_store_id_idx
    ON embeddings (vector_store_id);
CREATE INDEX IF NOT EXISTS embeddings_metadata_idx
    ON embeddings USING gin (metadata);
CREATE INDEX IF NOT EXISTS embeddings_embedding_hnsw_idx
    ON embeddings USING hnsw (embedding vector_cosine_ops);

INSERT INTO vector_stores (id, name, metadata)
VALUES (
    'sme-knowledge',
    'SME shared knowledge',
    '{"purpose":"shared internal RAG","embedding_model":"bge-m3","embedding_dimensions":1024}'::jsonb
)
ON CONFLICT (id) DO NOTHING;
