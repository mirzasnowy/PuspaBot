-- Generated from puspa/migrations/versions/0040_user_language.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS user_language (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    locale TEXT NOT NULL DEFAULT 'id',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

-- migrate:down
DROP TABLE IF EXISTS user_language CASCADE;
