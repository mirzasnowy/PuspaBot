-- Learning loop persistence (idempotent; apply with puspa.memory.loop_store.create_schema).
-- Prerequisite: Phase 0 schema (users, agents) already exists.

-- One row per correction: the latest correction per (user, agent, task).
-- A later misprediction for the same task overwrites the row (upsert)
-- and bumps correction_count; the experience ids that built it are
-- kept in experience_ids.
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

-- Upgrade for databases created before feedback_source was
-- stored on the correction row (idempotent).
ALTER TABLE loop_corrections
    ADD COLUMN IF NOT EXISTS feedback_source TEXT;

-- One row per experience that carries a correction: every
-- misprediction experience, and every user-feedback experience whose
-- correction was applied. Experiences without a correction (a
-- "correct" verdict, a "wrong" verdict without a correction) are
-- not stored: they change no prediction.
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

-- RLS (same pattern as Phase 0/1/2 and schema_world.sql: app.current_user_id).
-- Real enforcement only for connections as puspa_app.
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
