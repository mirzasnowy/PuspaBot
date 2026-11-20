-- Model settings: per-user model choice (E-08 migration 0016_model_settings.sql).
--
-- Creates the model_settings table (one row per user: the model choice only, never a key).
--
-- Source: puspa/pwa/model_settings.py (inline DDL).
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
CREATE TABLE IF NOT EXISTS model_settings (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    base_url TEXT,
    model TEXT,
    provider TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- migrate:down
DROP TABLE IF EXISTS model_settings CASCADE;
