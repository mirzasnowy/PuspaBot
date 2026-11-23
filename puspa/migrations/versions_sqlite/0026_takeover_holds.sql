-- Generated from puspa/migrations/versions/0026_takeover_holds.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS takeover_holds (
    agent_id TEXT PRIMARY KEY,
    held_by_user_id TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    task_id TEXT,
    held_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_takeover_holds_user
    ON takeover_holds (held_by_user_id);

-- migrate:down
DROP TABLE IF EXISTS takeover_holds;
