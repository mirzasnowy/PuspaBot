-- Conflicts: surfaced conflicts of interest (E-08 migration 0019_conflicts.sql).
--
-- Creates the conflict_records table with RLS. This is the fresh-install path for the object the old upgrade script (migrate_conflict_records) used to add to existing databases.
--
-- Source: infra/migrate_conflict_records.sql.
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
CREATE TABLE IF NOT EXISTS conflict_records (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    beneficiary TEXT NOT NULL,
    user_interest TEXT NOT NULL DEFAULT '',
    other_interest TEXT NOT NULL DEFAULT '',
    user_cost TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'low',
    status TEXT NOT NULL DEFAULT 'surfaced',
    user_choice TEXT,
    surfaced_text TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    CHECK (kind IN (
        'engagement_vs_goal',
        'seller_vs_user',
        'provider_vs_user',
        'retention_vs_independence',
        'attention_vs_benefit'
    )),
    CHECK (beneficiary IN ('user', 'seller', 'provider', 'system', 'other_person')),
    CHECK (severity IN ('low', 'high')),
    CHECK (status IN (
        'surfaced',
        'resolved_proceed',
        'resolved_alternative',
        'resolved_declined'
    )),
    CHECK (user_choice IS NULL OR user_choice IN ('proceed', 'alternative', 'decline')),
    CHECK (resolved_at IS NULL OR status <> 'surfaced')
);

CREATE INDEX IF NOT EXISTS idx_conflict_records_user_tool
    ON conflict_records (user_id, agent_id, tool_name, kind, created_at);
CREATE INDEX IF NOT EXISTS idx_conflict_records_unresolved
    ON conflict_records (user_id, agent_id, created_at) WHERE status = 'surfaced';

ALTER TABLE conflict_records ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS isolasi_conflict_records ON conflict_records;
    CREATE POLICY isolasi_conflict_records ON conflict_records
        USING (current_setting('app.current_user_id', true) = user_id::text)
        WITH CHECK (current_setting('app.current_user_id', true) = user_id::text);
END $$;

-- migrate:down
DROP TABLE IF EXISTS conflict_records CASCADE;
