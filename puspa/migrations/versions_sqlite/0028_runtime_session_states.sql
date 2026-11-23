-- Generated from puspa/migrations/versions/0028_runtime_session_states.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS runtime_session_states (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT '',
    app_name TEXT NOT NULL DEFAULT 'puspa',
    state JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_runtime_session_states_user_id
    ON runtime_session_states(user_id);

-- migrate:down
DROP TABLE IF EXISTS runtime_session_states;
