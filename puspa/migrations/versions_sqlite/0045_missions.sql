-- Generated from puspa/migrations/versions/0045_missions.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS missions (
    id            TEXT PRIMARY KEY,
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    goal          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'waiting', 'done', 'failed', 'stopped')),
    steps         JSONB NOT NULL DEFAULT '[]',
    webhook_token TEXT NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_missions_user_id ON missions(user_id);

-- migrate:down
DROP TABLE IF EXISTS missions;
