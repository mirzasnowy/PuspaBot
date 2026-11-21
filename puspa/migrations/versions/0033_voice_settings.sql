-- Voice settings: per-user ASR + TTS choice (V2-30, migration 0033_voice_settings.sql).
--
-- Creates the voice_settings table (one row per user: the voice choice only, never a key).
--
-- Source: puspa/pwa/voice_settings.py (inline DDL).
--
-- Reversible: yes. The down section drops the tables this migration
-- creates (and their data — roll back on a data copy first, never on
-- a live database without a backup). The puspa_app role, once created
-- by 0001, is never dropped by any down migration.
--
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

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
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- migrate:down
DROP TABLE IF EXISTS voice_settings CASCADE;
