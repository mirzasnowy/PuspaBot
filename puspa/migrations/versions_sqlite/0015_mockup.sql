-- Generated from puspa/migrations/versions/0015_mockup.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS mockups (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    template TEXT NOT NULL,
    doc JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS mockups_user_idx ON mockups (user_id, updated_at DESC);

-- migrate:down
DROP TABLE IF EXISTS mockups CASCADE;
