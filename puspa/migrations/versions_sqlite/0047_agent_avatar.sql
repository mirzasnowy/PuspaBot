-- Generated from puspa/migrations/versions/0047_agent_avatar.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
ALTER TABLE agent_meta ADD COLUMN IF NOT EXISTS avatar TEXT NOT NULL DEFAULT 'default';

-- migrate:down
ALTER TABLE agent_meta DROP COLUMN IF EXISTS avatar;
