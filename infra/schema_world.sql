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

-- Bitemporal belief versions (Kosmos K3, docs/bitemporal-state.md).
-- One row per belief version, never updated in place: a correction
-- appends a new row and closes the old row's known_to (and valid_to
-- when the fact itself stopped being true). Both intervals are
-- half-open [from, to); a NULL end means "still open".
-- The same DDL lives in infra/migrate_bitemporal.sql (the upgrade
-- path for existing databases); the two copies must stay identical.
CREATE TABLE IF NOT EXISTS belief_versions (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    belief_key TEXT NOT NULL,
    version INT NOT NULL,
    value JSONB NOT NULL DEFAULT '{}',
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ,
    known_from TIMESTAMPTZ NOT NULL,
    known_to TIMESTAMPTZ,
    supersedes INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, agent_id, belief_key, version),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (known_to IS NULL OR known_to > known_from)
);

CREATE INDEX IF NOT EXISTS idx_belief_versions_known ON belief_versions
    (user_id, agent_id, belief_key, known_from, known_to);
CREATE INDEX IF NOT EXISTS idx_belief_versions_valid ON belief_versions
    (user_id, agent_id, belief_key, valid_from, valid_to);

ALTER TABLE belief_versions ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS isolasi_belief_versions ON belief_versions;
    CREATE POLICY isolasi_belief_versions ON belief_versions
        USING (current_setting('app.current_user_id', true) = user_id::text)
        WITH CHECK (current_setting('app.current_user_id', true) = user_id::text);
END $$;
