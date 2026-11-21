-- Marketplace: installs, versions, audit (E-08 migration 0014_marketplace.sql).
--
-- Creates the marketplace tables (marketplace_installs, marketplace_versions, marketplace_audit). No RLS: the store filters by user_id in application code.
--
-- Source: puspa/marketplace/store.py (SCHEMA_SQL).
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
CREATE TABLE IF NOT EXISTS marketplace_installs (
    skill_id      TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    version       TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    source        TEXT NOT NULL,
    license       TEXT NOT NULL DEFAULT '',
    price_credits INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'disabled',
    installed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (skill_id, user_id)
);

CREATE TABLE IF NOT EXISTS marketplace_versions (
    skill_id      TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    version       TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    source        TEXT NOT NULL,
    license       TEXT NOT NULL DEFAULT '',
    price_credits INTEGER NOT NULL DEFAULT 0,
    manifest      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (skill_id, user_id, version)
);

CREATE TABLE IF NOT EXISTS marketplace_audit (
    id            BIGSERIAL PRIMARY KEY,
    event         TEXT NOT NULL,
    skill_id      TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    sha256        TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT '',
    license       TEXT NOT NULL DEFAULT '',
    price_credits INTEGER NOT NULL DEFAULT 0,
    detail        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- migrate:down
DROP TABLE IF EXISTS marketplace_audit, marketplace_versions, marketplace_installs CASCADE;
