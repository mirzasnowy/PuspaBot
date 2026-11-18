-- World model persistence (idempotent; apply with puspa.world.store.create_schema).
-- Prerequisite: Phase 0 schema (users, agents) already exists.

CREATE TABLE IF NOT EXISTS world_beliefs (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    state JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, agent_id)
);

-- RLS (same pattern as Phase 0/1/2: app.current_user_id).
-- Real enforcement only for connections as puspa_app.
ALTER TABLE world_beliefs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS isolasi_world_beliefs ON world_beliefs;
    CREATE POLICY isolasi_world_beliefs ON world_beliefs
        USING (current_setting('app.current_user_id', true) = user_id::text)
        WITH CHECK (current_setting('app.current_user_id', true) = user_id::text);
END $$;
