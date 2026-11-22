-- Telemetry toggle: per-user opt-in for anonymous usage telemetry (AST-09, migration 0036_telemetry_settings.sql).
--
-- Creates the telemetry_settings table (one row per user: the toggle only, never an endpoint,
-- a key, or any usage data). The toggle defaults to off; the PUSPA_TELEMETRY environment flag
-- can still override it either way (see puspa/telemetry/config.py).
--
-- Source: puspa/telemetry/settings.py (inline DDL).
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
CREATE TABLE IF NOT EXISTS telemetry_settings (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- migrate:down
DROP TABLE IF EXISTS telemetry_settings CASCADE;
