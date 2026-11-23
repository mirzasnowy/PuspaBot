-- Generated from puspa/migrations/versions/0042_gallery.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS gallery_works (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    mockup_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    template TEXT NOT NULL,
    doc JSONB NOT NULL,
    version INTEGER NOT NULL,
    restricted_customary_use BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'published' CHECK (status IN ('published', 'unpublished')),
    published_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS gallery_works_published_idx
    ON gallery_works (published_at DESC) WHERE status = 'published';

CREATE INDEX IF NOT EXISTS gallery_works_owner_idx ON gallery_works (owner_id);

CREATE TABLE IF NOT EXISTS gallery_ratings (
    work_id TEXT NOT NULL REFERENCES gallery_works (id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    rating SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    review TEXT NOT NULL DEFAULT '',
    work_version INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    PRIMARY KEY (work_id, user_id)
);

CREATE INDEX IF NOT EXISTS gallery_ratings_user_idx
    ON gallery_ratings (user_id, updated_at DESC);

-- migrate:down
DROP TABLE IF EXISTS gallery_ratings CASCADE;

DROP TABLE IF EXISTS gallery_works CASCADE;
