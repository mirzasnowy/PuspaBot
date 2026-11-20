-- Learning loop: corrections and experiences (E-08 migration 0007_learning_loop.sql).
--
-- Creates the learning-loop tables (loop_corrections, loop_experiences) with RLS.
--
-- Source: infra/schema_learning_loop.sql.
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
CREATE TABLE IF NOT EXISTS loop_corrections (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    task TEXT NOT NULL,
    predicted JSONB NOT NULL,
    observed JSONB NOT NULL,
    error TEXT,
    source TEXT,
    feedback_source TEXT,
    correction_count INTEGER NOT NULL DEFAULT 1,
    experience_ids JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, agent_id, task)
);

ALTER TABLE loop_corrections
    ADD COLUMN IF NOT EXISTS feedback_source TEXT;

CREATE TABLE IF NOT EXISTS loop_experiences (
    id TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    task TEXT NOT NULL,
    kind TEXT,
    source TEXT,
    feedback_source TEXT,
    verdict TEXT,
    predicted JSONB,
    observed JSONB,
    error TEXT,
    context JSONB NOT NULL DEFAULT '{}',
    position INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE loop_corrections ENABLE ROW LEVEL SECURITY;
ALTER TABLE loop_experiences ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS isolasi_loop_corrections ON loop_corrections;
    CREATE POLICY isolasi_loop_corrections ON loop_corrections
        USING (current_setting('app.current_user_id', true) = user_id::text)
        WITH CHECK (current_setting('app.current_user_id', true) = user_id::text);
    DROP POLICY IF EXISTS isolasi_loop_experiences ON loop_experiences;
    CREATE POLICY isolasi_loop_experiences ON loop_experiences
        USING (current_setting('app.current_user_id', true) = user_id::text)
        WITH CHECK (current_setting('app.current_user_id', true) = user_id::text);
END $$;

-- migrate:down
DROP TABLE IF EXISTS loop_experiences, loop_corrections CASCADE;
