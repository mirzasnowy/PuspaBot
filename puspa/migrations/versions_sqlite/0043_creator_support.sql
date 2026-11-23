-- Generated from puspa/migrations/versions/0043_creator_support.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS creator_support_purchases (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES gallery_works (id) ON DELETE CASCADE,
    supporter_id TEXT NOT NULL,
    creator_id TEXT NOT NULL,
    amount_idr BIGINT NOT NULL CHECK (amount_idr >= 1000),
    commission_idr BIGINT NOT NULL CHECK (commission_idr >= 0),
    credits_granted INTEGER NOT NULL CHECK (credits_granted >= 1),
    status TEXT NOT NULL DEFAULT 'prepared' CHECK (status IN ('prepared')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS creator_support_work_idx
    ON creator_support_purchases (work_id, created_at DESC);

CREATE INDEX IF NOT EXISTS creator_support_creator_idx
    ON creator_support_purchases (creator_id, created_at DESC);

-- migrate:down
DROP TABLE IF EXISTS creator_support_purchases CASCADE;
