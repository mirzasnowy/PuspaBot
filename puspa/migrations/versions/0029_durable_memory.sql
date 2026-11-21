-- Durable MEMORY.md store + narrative journal (V2-05 / COW-05).
--
-- The brain keeps a human-readable long-term memory file
-- (MEMORY.md) rebuilt nightly from the lower memory layers
-- (profile facts, episodic rows, consolidation summaries, and the
-- knowledge graph built from those episodes). The file lives in
-- the database, one row per user, so the hosted install and the
-- local install share the same store; the PWA renders the same
-- text the user can read and edit.
--
-- `durable_memory`: one row per user holding the latest rebuilt
-- document, its content hash (so a rebuild that changes nothing
-- is a no-op), a version counter that only grows when the text
-- changes, and per-section counts for the rebuild report.
--
-- `narrative_journal`: one plain-language entry per user per day,
-- written by the nightly rebuild from that day's summaries and
-- episodes. It is the day-by-day story of what happened; the
-- latest entries are rendered into the Journal section of
-- MEMORY.md. One row per (user, day): rebuilding the same day
-- replaces the draft entry for that day, history for past days
-- is never rewritten.
--
-- `user_id` is plain TEXT, not a foreign key: durable memory is
-- keyed by the same opaque string user ids the rest of the
-- memory layer uses. Ownership is enforced the same way as the
-- other memory tables: row level security with the
-- `app.current_user_id` setting, plus a grant to the `puspa_app`
-- role when that role exists.
--
-- Reversible: yes. The down section drops the policy and the
-- tables (and their data, so roll back on a data copy first,
-- never on a live database without a backup).
--
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
CREATE TABLE IF NOT EXISTS durable_memory (
    user_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    version INT NOT NULL DEFAULT 1,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_counts JSONB NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS narrative_journal (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    entry_date DATE NOT NULL,
    text TEXT NOT NULL,
    source_event_ids TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, entry_date)
);
CREATE INDEX IF NOT EXISTS idx_narrative_journal_user_date
    ON narrative_journal(user_id, entry_date DESC);

ALTER TABLE durable_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE narrative_journal ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON durable_memory TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON narrative_journal TO puspa_app;
    END IF;
END $$;

DROP POLICY IF EXISTS isolasi_durable_memory
    ON durable_memory;
CREATE POLICY isolasi_durable_memory ON durable_memory
    USING (current_setting('app.current_user_id', true) = user_id)
    WITH CHECK (current_setting('app.current_user_id', true) = user_id);

DROP POLICY IF EXISTS isolasi_narrative_journal
    ON narrative_journal;
CREATE POLICY isolasi_narrative_journal ON narrative_journal
    USING (current_setting('app.current_user_id', true) = user_id)
    WITH CHECK (current_setting('app.current_user_id', true) = user_id);

-- migrate:down
DROP POLICY IF EXISTS isolasi_durable_memory
    ON durable_memory;
DROP POLICY IF EXISTS isolasi_narrative_journal
    ON narrative_journal;
DROP TABLE IF EXISTS narrative_journal;
DROP TABLE IF EXISTS durable_memory;
