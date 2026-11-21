-- Long-term memory tiers (K16, migration 0034_longterm_tiers.sql).
--
-- One row per long-term memory item: which tier it sits in
-- (`pinned`, `hot`, `warm`, `cold`, `archived`), how important
-- it is, how often its pattern repeated, and whether it is a
-- user correction. The forgetting curve only ever moves an item
-- between tiers: nothing is deleted by decay. An archived item
-- stays searchable and comes back to `warm` when it is accessed
-- again, so the archive is a cold shelf, not a bin.
--
-- `user_id` is plain TEXT, not a foreign key: tier state is
-- keyed by the same opaque string user ids the rest of the
-- memory layer uses. Ownership is enforced the same way as the
-- other memory tables: row level security with the
-- `app.current_user_id` setting, plus a grant to the `puspa_app`
-- role when that role exists.
--
-- Reversible: yes. The down section drops the policy and the
-- table (and their data, so roll back on a data copy first,
-- never on a live database without a backup).
--
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
CREATE TABLE IF NOT EXISTS longterm_tiers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, item_kind, item_id)
);
CREATE INDEX IF NOT EXISTS idx_longterm_tiers_user_tier
    ON longterm_tiers(user_id, tier, updated_at DESC);

ALTER TABLE longterm_tiers ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON longterm_tiers TO puspa_app;
    END IF;
END $$;

DROP POLICY IF EXISTS isolasi_longterm_tiers
    ON longterm_tiers;
CREATE POLICY isolasi_longterm_tiers ON longterm_tiers
    USING (current_setting('app.current_user_id', true) = user_id)
    WITH CHECK (current_setting('app.current_user_id', true) = user_id);

-- migrate:down
DROP POLICY IF EXISTS isolasi_longterm_tiers ON longterm_tiers;
DROP TABLE IF EXISTS longterm_tiers CASCADE;
