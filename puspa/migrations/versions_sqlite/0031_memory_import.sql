-- Generated from puspa/migrations/versions/0031_memory_import.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS memory_import_batches (
    id UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    user_id TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'importing'
        CHECK (status IN ('importing', 'completed', 'rolled_back', 'failed')),
    counts JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_memory_import_batches_user
    ON memory_import_batches(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS memory_import_items (
    id UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    batch_id UUID NOT NULL REFERENCES memory_import_batches(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    fact_id UUID,
    fact_key TEXT NOT NULL,
    disposition TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_memory_import_items_batch
    ON memory_import_items(batch_id, created_at);

CREATE TABLE IF NOT EXISTS memory_import_quarantine (
    id UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    batch_id UUID REFERENCES memory_import_batches(id) ON DELETE SET NULL,
    user_id TEXT NOT NULL,
    fact_key TEXT NOT NULL,
    text TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'blocked')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    decided_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_memory_import_quarantine_user_status
    ON memory_import_quarantine(user_id, status, created_at);

CREATE TABLE IF NOT EXISTS memory_forget_list (
    user_id TEXT NOT NULL,
    fact_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    PRIMARY KEY (user_id, fact_key, content_hash)
);

-- migrate:down
DROP TABLE IF EXISTS memory_import_items;

DROP TABLE IF EXISTS memory_import_quarantine;

DROP TABLE IF EXISTS memory_import_batches;

DROP TABLE IF EXISTS memory_forget_list;
