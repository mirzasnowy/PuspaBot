-- Generated from puspa/migrations/versions/0037_document_knowledge_base.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS knowledge_documents (
    id           UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename     TEXT NOT NULL,
    title        TEXT NOT NULL,
    content_type TEXT NOT NULL CHECK (content_type IN ('text', 'markdown')),
    byte_size    INTEGER NOT NULL CHECK (byte_size >= 0),
    chunk_count  INTEGER NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
    embedder_id  TEXT NOT NULL,
    dimension    INTEGER NOT NULL CHECK (dimension > 0),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_knowledge_documents_user
    ON knowledge_documents(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id          UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    document_id UUID NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    content     TEXT NOT NULL,
    embedding   BLOB,
    embedder_id TEXT NOT NULL,
    dimension   INTEGER NOT NULL CHECK (dimension > 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_user
    ON knowledge_chunks(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_document
    ON knowledge_chunks(document_id, chunk_index);

-- migrate:down
DROP TABLE IF EXISTS knowledge_chunks CASCADE;

DROP TABLE IF EXISTS knowledge_documents CASCADE;
