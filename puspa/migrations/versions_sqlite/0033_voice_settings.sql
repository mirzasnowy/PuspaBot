-- Generated from puspa/migrations/versions/0033_voice_settings.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS voice_settings (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    asr_provider TEXT NOT NULL DEFAULT 'none',
    asr_base_url TEXT,
    asr_model TEXT,
    tts_provider TEXT NOT NULL DEFAULT 'edge',
    tts_voice TEXT,
    tts_model TEXT,
    tts_base_url TEXT,
    tts_region TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

-- migrate:down
DROP TABLE IF EXISTS voice_settings CASCADE;
