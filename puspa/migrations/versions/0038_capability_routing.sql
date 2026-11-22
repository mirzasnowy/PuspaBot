-- Per-capability model routing: one provider + model per capability (COW-08, migration 0038_capability_routing.sql).
--
-- Creates the capability_routing table (one row per (user, capability):
-- the route only, never a key). A missing row means "the default for
-- that capability" (chat follows the user's existing model_settings
-- selection; vision and embeddings fall back to the hosted default;
-- image and speech have no usable default until the user picks one).
--
-- Source: puspa/pwa/capability_routing.py (inline DDL).
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
CREATE TABLE IF NOT EXISTS capability_routing (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    capability TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    base_url TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, capability)
);

-- migrate:down
DROP TABLE IF EXISTS capability_routing CASCADE;
