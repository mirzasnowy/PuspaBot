-- User language choice: per-user display language for the PWA (COW-18, migration 0040_user_language.sql).
--
-- Creates the user_language table (one row per user: the locale code only).
-- The default is Indonesian ("id"); English ("en") is the other shipped
-- locale. The catalogs themselves live in puspa/i18n/locales/*.json.
--
-- Source: puspa/pwa/language.py (inline DDL).
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
CREATE TABLE IF NOT EXISTS user_language (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    locale TEXT NOT NULL DEFAULT 'id',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- migrate:down
DROP TABLE IF EXISTS user_language CASCADE;
