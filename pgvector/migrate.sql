BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS stack_schema_migrations (
    id text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT NOW()
);

ALTER TABLE vector_stores
    ADD COLUMN IF NOT EXISTS file_counts jsonb
        NOT NULL DEFAULT '{"in_progress":0,"completed":0,"failed":0,"cancelled":0,"total":0}'::jsonb,
    ADD COLUMN IF NOT EXISTS expires_after jsonb,
    ADD COLUMN IF NOT EXISTS expires_at timestamptz;

DO $migration$
DECLARE
    current_dimensions text;
    embedding_count bigint;
BEGIN
    IF EXISTS (
        SELECT 1
        FROM stack_schema_migrations
        WHERE id = 'litellm-pgvector-1024'
    ) THEN
        RETURN;
    END IF;

    SELECT format_type(attribute.atttypid, attribute.atttypmod)
    INTO current_dimensions
    FROM pg_attribute AS attribute
    WHERE attribute.attrelid = 'embeddings'::regclass
      AND attribute.attname = 'embedding'
      AND NOT attribute.attisdropped;

    IF current_dimensions <> 'vector(1024)' THEN
        SELECT COUNT(*) INTO embedding_count FROM embeddings;
        IF embedding_count <> 0 THEN
            RAISE EXCEPTION
                'Cannot change embeddings from % to vector(1024): table contains % rows',
                current_dimensions,
                embedding_count;
        END IF;

        DROP INDEX IF EXISTS embeddings_embedding_hnsw_idx;
        ALTER TABLE embeddings
            ALTER COLUMN embedding TYPE vector(1024)
            USING embedding::vector(1024);
    END IF;

    ALTER TABLE embeddings
        ALTER COLUMN id TYPE text USING id::text;

    INSERT INTO stack_schema_migrations (id)
    VALUES ('litellm-pgvector-1024');
END
$migration$;

UPDATE vector_stores
SET
    file_counts = COALESCE(
        file_counts,
        '{"in_progress":0,"completed":0,"failed":0,"cancelled":0,"total":0}'::jsonb
    ),
    metadata = COALESCE(metadata, '{}'::jsonb)
        || '{"embedding_model":"bge-m3","embedding_dimensions":1024}'::jsonb
WHERE id = 'sme-knowledge';

CREATE INDEX IF NOT EXISTS embeddings_embedding_hnsw_idx
    ON embeddings USING hnsw (embedding vector_cosine_ops);
CREATE UNIQUE INDEX IF NOT EXISTS embeddings_source_chunk_idx
    ON embeddings (
        vector_store_id,
        (metadata->>'source_id'),
        ((metadata->>'chunk_index')::integer)
    )
    WHERE metadata ? 'source_id' AND metadata ? 'chunk_index';

COMMIT;
