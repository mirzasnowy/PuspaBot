-- Mockups: design drafts (E-08 migration 0015_mockup.sql).
--
-- Creates the mockup table (mockups) with its owner policy. Note the policy reads app.user_id (not app.current_user_id): kept verbatim from the original.
--
-- Source: infra/schema_mockup.sql.
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
CREATE TABLE IF NOT EXISTS mockups (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    template TEXT NOT NULL,
    doc JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS mockups_user_idx ON mockups (user_id, updated_at DESC);

ALTER TABLE mockups ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'mockups' AND policyname = 'mockups_owner'
    ) THEN
        CREATE POLICY mockups_owner ON mockups
            USING (user_id = current_setting('app.user_id', true));
    END IF;
END $$;

-- migrate:down
DROP TABLE IF EXISTS mockups CASCADE;
