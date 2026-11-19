-- Causal Ledger migration (Kosmos K4, docs/causal-ledger.md).
--
-- Idempotent and additive: run it as many times as you like
-- against an existing puspa_test database. It creates nothing
-- twice and drops nothing. Run it once per existing database:
--
--   psql "dbname=puspa_test" -f infra/migrate_causal_ledger.sql
--
-- or from Python: puspa.world.store.apply_causal_migration().
-- Fresh databases do not need this file: the same objects come
-- from infra/schema_world.sql (causal_hypotheses).
--
-- One row per hypothesis: cause X leads to effect Y under
-- condition C, with a strength (0..1), evidence links (ledger
-- event ids and/or observation ids, as JSONB), a TTL
-- (expires_at), and the bitemporal valid interval of the claim
-- (valid_from / valid_to). Status is active, expired, or
-- contradicted; the last two are terminal and stay stored as
-- history, so the (cause, effect, condition) triple is NOT
-- unique — a re-proposed claim after expiry or contradiction
-- is a second row with a new id. The primary key is the
-- hypothesis id alone.
--
-- No new ledger event type is added: the closed set of 31
-- revision-7 event types stays closed. Hypotheses reference
-- ledger events by id in the evidence JSONB only.

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
