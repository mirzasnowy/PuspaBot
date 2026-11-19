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

-- Causal hypotheses (Kosmos K4, docs/causal-ledger.md).
-- One row per hypothesis: cause X leads to effect Y under
-- condition C, with strength, evidence links, TTL, and the
-- bitemporal valid interval of the claim. Status is active,
-- expired, or contradicted; the last two stay as history, so
-- the triple is NOT unique. The same DDL lives in
-- infra/migrate_causal_ledger.sql (the upgrade path for
-- existing databases); the two copies must stay identical.
CREATE TABLE IF NOT EXISTS causal_hypotheses (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    cause TEXT NOT NULL,
    effect TEXT NOT NULL,
    effect_value JSONB NOT NULL DEFAULT 'null',
    condition TEXT NOT NULL DEFAULT '',
    strength DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    evidence JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'active',
    supporting INT NOT NULL DEFAULT 0,
    contradicting INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (strength >= 0 AND strength <= 1),
    CHECK (status IN ('active', 'expired', 'contradicted')),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (supporting >= 0),
    CHECK (contradicting >= 0)
);

CREATE INDEX IF NOT EXISTS idx_causal_hypotheses_cause ON causal_hypotheses
    (user_id, agent_id, cause, condition, status);
CREATE INDEX IF NOT EXISTS idx_causal_hypotheses_expiry ON causal_hypotheses
    (user_id, agent_id, expires_at) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_causal_hypotheses_valid ON causal_hypotheses
    (user_id, agent_id, valid_from, valid_to);

ALTER TABLE causal_hypotheses ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS isolasi_causal_hypotheses ON causal_hypotheses;
    CREATE POLICY isolasi_causal_hypotheses ON causal_hypotheses
        USING (current_setting('app.current_user_id', true) = user_id::text)
        WITH CHECK (current_setting('app.current_user_id', true) = user_id::text);
END $$;
