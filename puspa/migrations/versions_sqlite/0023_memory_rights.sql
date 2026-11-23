-- Generated from puspa/migrations/versions/0023_memory_rights.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
ALTER TABLE profile_facts ADD COLUMN IF NOT EXISTS confidence REAL
    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1));

ALTER TABLE profile_facts ADD COLUMN IF NOT EXISTS source_detail TEXT;

ALTER TABLE data_consents ADD COLUMN IF NOT EXISTS withdrawn_at TIMESTAMPTZ;

-- migrate:down
ALTER TABLE profile_facts DROP COLUMN IF EXISTS confidence;

ALTER TABLE profile_facts DROP COLUMN IF EXISTS source_detail;

ALTER TABLE data_consents DROP COLUMN IF EXISTS withdrawn_at;
