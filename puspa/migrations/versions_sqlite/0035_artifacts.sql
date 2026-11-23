-- Generated from puspa/migrations/versions/0035_artifacts.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS artifacts (
    id UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    user_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    run_id TEXT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    size_bytes INTEGER NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    content BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_artifacts_agent_created
    ON artifacts(agent_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_artifacts_user_created
    ON artifacts(user_id, created_at DESC);

-- migrate:down
DROP TABLE IF EXISTS artifacts CASCADE;
