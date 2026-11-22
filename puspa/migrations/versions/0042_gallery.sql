-- Community gallery: published cultural works + their ratings (B16, migration 0042_gallery.sql).
--
-- Creates gallery_works (one row per published B7 mockup: the owner,
-- the snapshot of the design doc with its version, and provenance)
-- and gallery_ratings (one row per user per work: 1-5 stars plus a
-- short written review, stamped with the work version it rated).
-- A work flagged restricted_customary_use is never stored here at
-- all: the publish path refuses it before the insert, so a restricted
-- work can never appear as a generic pattern in the gallery.
--
-- Source: puspa/gallery/store.py.
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
    published_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (work_id, user_id)
);
CREATE INDEX IF NOT EXISTS gallery_ratings_user_idx
    ON gallery_ratings (user_id, updated_at DESC);

ALTER TABLE gallery_works ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'gallery_works' AND policyname = 'gallery_works_read'
    ) THEN
        CREATE POLICY gallery_works_read ON gallery_works
            FOR SELECT USING (status = 'published');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'gallery_works' AND policyname = 'gallery_works_owner'
    ) THEN
        CREATE POLICY gallery_works_owner ON gallery_works
            USING (owner_id = current_setting('app.current_user_id', true));
    END IF;
END $$;

ALTER TABLE gallery_ratings ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'gallery_ratings' AND policyname = 'gallery_ratings_read'
    ) THEN
        CREATE POLICY gallery_ratings_read ON gallery_ratings
            FOR SELECT USING (true);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'gallery_ratings' AND policyname = 'gallery_ratings_owner'
    ) THEN
        CREATE POLICY gallery_ratings_owner ON gallery_ratings
            USING (user_id = current_setting('app.current_user_id', true));
    END IF;
END $$;

-- migrate:down
DROP TABLE IF EXISTS gallery_ratings CASCADE;
DROP TABLE IF EXISTS gallery_works CASCADE;
