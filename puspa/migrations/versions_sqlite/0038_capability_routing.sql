-- Generated from puspa/migrations/versions/0038_capability_routing.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS capability_routing (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    capability TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    base_url TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    PRIMARY KEY (user_id, capability)
);

-- migrate:down
DROP TABLE IF EXISTS capability_routing CASCADE;
