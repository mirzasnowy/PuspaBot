-- Document knowledge base: upload files and ask questions about them (AST-02, migration 0037_document_knowledge_base.sql).
--
-- Creates the knowledge_documents + knowledge_chunks tables. A user
-- uploads plain text or Markdown files; each file is split into
-- chunks, each chunk is embedded with the repo's local embedder
-- (puspa.memory.embeddings, same 384 dimensions as the episodic
-- index), and questions are answered from the closest chunks with
-- a citation per chunk (source file + chunk number). PDF is NOT
-- stored: no PDF parser exists in the repo and this item adds no
-- heavy new dependency for one (see puspa/knowledge/store.py).
--
-- Source: puspa/knowledge/store.py (inline DDL).
--
-- Reversible: yes. The down section drops the tables this migration
-- creates (and their data — roll back on a data copy first, never on
-- a live database without a backup). The puspa_app role, once created
-- by 0001, is never dropped by any down migration.
--
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
CREATE TABLE IF NOT EXISTS knowledge_documents (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename     TEXT NOT NULL,
    title        TEXT NOT NULL,
    content_type TEXT NOT NULL CHECK (content_type IN ('text', 'markdown')),
    byte_size    INTEGER NOT NULL CHECK (byte_size >= 0),
    chunk_count  INTEGER NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
    embedder_id  TEXT NOT NULL,
    dimension    INTEGER NOT NULL CHECK (dimension > 0),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_user
    ON knowledge_documents(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    content     TEXT NOT NULL,
    embedding   vector(384),
    embedder_id TEXT NOT NULL,
    dimension   INTEGER NOT NULL CHECK (dimension > 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_user
    ON knowledge_chunks(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_document
    ON knowledge_chunks(document_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding
    ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON knowledge_documents TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON knowledge_chunks TO puspa_app;
    END IF;
END $$;

ALTER TABLE knowledge_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_chunks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS knowledge_documents_own_own ON knowledge_documents;
CREATE POLICY knowledge_documents_own_own ON knowledge_documents FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS knowledge_chunks_own_own ON knowledge_chunks;
CREATE POLICY knowledge_chunks_own_own ON knowledge_chunks FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

-- migrate:down
DROP TABLE IF EXISTS knowledge_chunks CASCADE;
DROP TABLE IF EXISTS knowledge_documents CASCADE;
