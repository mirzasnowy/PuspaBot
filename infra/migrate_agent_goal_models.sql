-- Other-agent goal models migration (Kosmos K5, docs/other-agent-models.md).
--
-- Idempotent and additive: run it as many times as you like
-- against an existing puspa_test database. It creates nothing
-- twice and drops nothing. Run it once per existing database:
--
--   psql "dbname=puspa_test" -f infra/migrate_agent_goal_models.sql
--
-- or from Python: puspa.world.store.apply_agent_migration().
-- Fresh databases do not need this file: the same objects come
-- from infra/schema_world.sql (agent_goal_models).
--
-- One row per (user, agent, contact): the goals that contact is
-- pursuing (label, strength, supporting/contradicting counts,
-- evidence links, first/last seen day, status) plus the
-- interaction counts the model keeps (observations, replies,
-- reply delays, channels), the scored predictions, and the
-- accuracy counters. Goals cite ledger events by id in the
-- evidence JSONB only.
--
-- No new ledger event type is added: the closed set of 31
-- revision-7 event types stays closed. Goal models cite ledger
-- events as evidence by id only.

CREATE TABLE IF NOT EXISTS agent_goal_models (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    contact_id TEXT NOT NULL,
    goals JSONB NOT NULL DEFAULT '{}',
    observations INT NOT NULL DEFAULT 0,
    replies INT NOT NULL DEFAULT 0,
    reply_delays JSONB NOT NULL DEFAULT '[]',
    channels JSONB NOT NULL DEFAULT '{}',
    last_day INT,
    predictions JSONB NOT NULL DEFAULT '[]',
    stats JSONB NOT NULL DEFAULT '{"predictions": 0, "correct": 0}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, agent_id, contact_id),
    CHECK (observations >= 0),
    CHECK (replies >= 0),
    CHECK (replies <= observations)
);

CREATE INDEX IF NOT EXISTS idx_agent_goal_models_contact ON agent_goal_models
    (user_id, agent_id, contact_id);

ALTER TABLE agent_goal_models ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS isolasi_agent_goal_models ON agent_goal_models;
    CREATE POLICY isolasi_agent_goal_models ON agent_goal_models
        USING (current_setting('app.current_user_id', true) = user_id::text)
        WITH CHECK (current_setting('app.current_user_id', true) = user_id::text);
END $$;
