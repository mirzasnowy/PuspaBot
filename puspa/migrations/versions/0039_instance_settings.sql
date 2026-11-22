-- Instance settings for the local admin dashboard (migration 0039_instance_settings.sql).
--
-- Creates the instance_settings table (key/value rows for settings
-- that belong to the instance, not to one user). Today the only key
-- is "instance_name" — the display name shown on the admin
-- dashboard. The key allowlist lives in puspa/pwa/admin_settings.py:
-- a new key is added deliberately, never by a client naming it.
--
-- Source: puspa/pwa/admin_settings.py (inline DDL).
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
--
-- NOTE (numbering): 0038 is claimed by two sibling items in flight
-- (user_language in one worktree, capability_routing in another),
-- so this item takes the next free number, 0039. The merge
-- coordinator renumbers if main lands a different 0038/0039 first.

-- migrate:up
CREATE TABLE IF NOT EXISTS instance_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- migrate:down
DROP TABLE IF EXISTS instance_settings CASCADE;
