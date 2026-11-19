-- Bitemporal state migration (Kosmos K3, docs/bitemporal-state.md).
--
-- Idempotent and additive: run it as many times as you like
-- against an existing puspa_test database. It creates nothing
-- twice and drops nothing. Run it once per existing database:
--
--   psql "dbname=puspa_test" -f infra/migrate_bitemporal.sql
--
-- or from Python: puspa.world.store.apply_bitemporal_migration().
-- Fresh databases do not need this file: the same objects come
-- from infra/schema_world.sql (belief_versions) and
-- puspa/ledger/schema.sql via Ledger.create_schema() (events.valid_at).

-- 1. Belief versions: one row per belief version, two time pairs
-- per row. The same DDL lives in infra/schema_world.sql (the
-- fresh-install path); the two copies must stay identical.
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

-- 2. Ledger entries: one valid instant per entry (an event is a
-- point in valid time; beliefs are intervals, events are not).
-- The recorded time stays `created_at` (chain order); `valid_at`
-- is metadata recorded alongside, OUTSIDE the hashed payload,
-- so the hash chain is untouched. Backfill is honest: the past
-- has no separately recorded valid time, so existing entries
-- get valid_at = created_at. New entries get valid_at from the
-- caller, defaulting to created_at when the caller does not
-- know better.
-- The DO block guards the case where the ledger table does not
-- exist yet in this database (fresh install path creates it
-- later through Ledger.create_schema()).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'events'
    ) THEN
        EXECUTE 'ALTER TABLE events ADD COLUMN IF NOT EXISTS valid_at TIMESTAMPTZ';
        EXECUTE 'UPDATE events SET valid_at = created_at WHERE valid_at IS NULL';
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_event_valid_at ON events (valid_at)';
    END IF;
END $$;
