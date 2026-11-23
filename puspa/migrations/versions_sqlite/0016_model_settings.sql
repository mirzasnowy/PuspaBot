-- Generated from puspa/migrations/versions/0016_model_settings.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS model_settings (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    base_url TEXT,
    model TEXT,
    provider TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

-- migrate:down
DROP TABLE IF EXISTS model_settings CASCADE;
