-- Mockups RLS repair (E-13, plan section 6.9 differential tests).
--
-- Migration 0015 created the `mockups_owner` policy against the
-- wrong session variable (`app.user_id`, which nothing ever sets)
-- and granted nothing to the application role, so as `puspa_app`
-- the mockups table was unusable: SELECT failed with
-- `permission denied`, and even with a grant the policy would
-- have hidden every row from its owner (the "RLS too strict,
-- legitimate reads fail silently" failure mode of plan 6.9).
-- The code layer (MockupStore filters by user_id in SQL) kept
-- working because the store connects as the table owner, so the
-- disagreement was invisible until the differential tests ran
-- the same read through both layers.
--
-- This migration repairs only the database layer: grant the
-- application role the same privileges the other PWA tables have,
-- and point the policy at `app.current_user_id`, the variable
-- `puspa/accounts/rls.py` sets. It changes no application code.
--
-- Reversible: yes. The down section revokes the grant and
-- restores the 0015 policy verbatim (wrong variable included),
-- so a rollback returns to exactly the pre-0021 state.
--
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON mockups TO puspa_app;
    END IF;
END $$;

DROP POLICY IF EXISTS mockups_owner ON mockups;
CREATE POLICY mockups_owner ON mockups
    USING (user_id = current_setting('app.current_user_id', true))
    WITH CHECK (user_id = current_setting('app.current_user_id', true));

-- migrate:down
REVOKE SELECT, INSERT, UPDATE, DELETE ON mockups FROM puspa_app;
DROP POLICY IF EXISTS mockups_owner ON mockups;
CREATE POLICY mockups_owner ON mockups
    USING (user_id = current_setting('app.user_id', true));
