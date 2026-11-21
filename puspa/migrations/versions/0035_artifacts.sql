-- Artifacts + run-logs browser (COW-12 issue, migration 0035_artifacts.sql).
--
-- One row per file a run produced: which agent and run it came
-- from, its name, kind, media type, size, and the bytes
-- themselves. The run-log half of the browser needs no new
-- table: a run's log is its ledger events, already replayed by
-- puspa.pwa.replay, so this table only holds the artifacts.
--
-- The first producer is the replay GIF export: exporting a
-- run's replay records the generated GIF here (see
-- puspa.pwa.replay_api). Any later producer records through
-- ArtifactStore.record_artifact with the same columns.
--
-- `user_id` is plain TEXT, not a foreign key: artifact state is
-- keyed by the same opaque string user ids the rest of the
-- layer uses. Ownership is enforced the same way as the other
-- tables: row level security with the `app.current_user_id`
-- setting, plus a grant to the `puspa_app` role when that role
-- exists. The API layer also checks ownership before serving
-- a row (an artifact of another user's agent reads as 404).
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
CREATE TABLE IF NOT EXISTS artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    run_id TEXT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    size_bytes INTEGER NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    content BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_artifacts_agent_created
    ON artifacts(agent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_artifacts_user_created
    ON artifacts(user_id, created_at DESC);

ALTER TABLE artifacts ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT, DELETE
            ON artifacts TO puspa_app;
    END IF;
END $$;

DROP POLICY IF EXISTS isolasi_artifacts
    ON artifacts;
CREATE POLICY isolasi_artifacts ON artifacts
    USING (current_setting('app.current_user_id', true) = user_id)
    WITH CHECK (current_setting('app.current_user_id', true) = user_id);

-- migrate:down
DROP POLICY IF EXISTS isolasi_artifacts ON artifacts;
DROP TABLE IF EXISTS artifacts CASCADE;
