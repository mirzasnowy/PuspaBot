-- Onboarding: seen-hint flags (E-08 migration 0017_onboarding_hints.sql).
--
-- Creates the onboarding_hint_seen table (one row per user per hint flag).
--
-- Source: puspa/onboarding/hints.py (HINT_SCHEMA_SQL).
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
CREATE TABLE IF NOT EXISTS onboarding_hint_seen (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    flag TEXT NOT NULL,
    seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, flag)
);

-- migrate:down
DROP TABLE IF EXISTS onboarding_hint_seen CASCADE;
