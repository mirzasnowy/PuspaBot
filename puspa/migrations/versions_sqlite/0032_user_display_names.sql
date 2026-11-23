-- Generated from puspa/migrations/versions/0032_user_display_names.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name TEXT;

-- migrate:down
ALTER TABLE users DROP COLUMN IF EXISTS display_name;
