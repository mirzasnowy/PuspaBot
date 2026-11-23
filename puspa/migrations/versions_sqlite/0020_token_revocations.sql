-- Generated from puspa/migrations/versions/0020_token_revocations.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS token_revocations (
    jti TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    revoked_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS agent_revocations (
    agent_id TEXT PRIMARY KEY,
    revoked_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    reason TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_token_revocations_agent
    ON token_revocations (agent_id, revoked_at);

-- migrate:down
DROP TABLE IF EXISTS token_revocations CASCADE;

DROP TABLE IF EXISTS agent_revocations CASCADE;
