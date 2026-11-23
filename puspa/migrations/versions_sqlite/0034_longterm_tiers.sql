-- Generated from puspa/migrations/versions/0034_longterm_tiers.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS longterm_tiers (
    id UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    user_id TEXT NOT NULL,
    item_kind TEXT NOT NULL CHECK (item_kind IN ('fact', 'episode')),
    item_id UUID NOT NULL,
    item_key TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL DEFAULT 'warm'
        CHECK (tier IN ('pinned', 'hot', 'warm', 'cold', 'archived')),
    importance REAL NOT NULL DEFAULT 0.5
        CHECK (importance >= 0 AND importance <= 1),
    repetitions INTEGER NOT NULL DEFAULT 1 CHECK (repetitions >= 0),
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    is_correction BOOLEAN NOT NULL DEFAULT FALSE,
    last_accessed_at TIMESTAMPTZ,
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    UNIQUE (user_id, item_kind, item_id)
);

CREATE INDEX IF NOT EXISTS idx_longterm_tiers_user_tier
    ON longterm_tiers(user_id, tier, updated_at DESC);

-- migrate:down
DROP TABLE IF EXISTS longterm_tiers CASCADE;
