-- Generated from puspa/migrations/versions/0029_durable_memory.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS durable_memory (
    user_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    version INT NOT NULL DEFAULT 1,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    source_counts JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS narrative_journal (
    id UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    user_id TEXT NOT NULL,
    entry_date DATE NOT NULL,
    text TEXT NOT NULL,
    source_event_ids TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    UNIQUE (user_id, entry_date)
);

CREATE INDEX IF NOT EXISTS idx_narrative_journal_user_date
    ON narrative_journal(user_id, entry_date DESC);

-- migrate:down
DROP TABLE IF EXISTS narrative_journal;

DROP TABLE IF EXISTS durable_memory;
