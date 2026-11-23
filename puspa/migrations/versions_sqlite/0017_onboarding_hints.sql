-- Generated from puspa/migrations/versions/0017_onboarding_hints.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS onboarding_hint_seen (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    flag TEXT NOT NULL,
    seen_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    PRIMARY KEY (user_id, flag)
);

-- migrate:down
DROP TABLE IF EXISTS onboarding_hint_seen CASCADE;
