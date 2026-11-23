-- Generated from puspa/migrations/versions/0025_morning_notes.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS morning_notes (
    id UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    source TEXT NOT NULL
        CHECK (source IN ('voice', 'text', 'meeting', 'lecture', 'study')),
    title TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    actions JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_morning_notes_user_created
    ON morning_notes (user_id, created_at DESC);

-- migrate:down
DROP TABLE IF EXISTS morning_notes;
