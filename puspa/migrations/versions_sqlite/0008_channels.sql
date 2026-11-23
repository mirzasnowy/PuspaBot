-- Generated from puspa/migrations/versions/0008_channels.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS channel_deliveries (
    id         TEXT PRIMARY KEY,
    platform   TEXT NOT NULL,          -- "telegram" | "whatsapp" | ...
    chat_id    TEXT NOT NULL,          -- platform chat id, as text
    direction  TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    status     TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed', 'retrying')),
    dedup_key  TEXT NOT NULL,          -- platform:chat:message id
    attempts   INT NOT NULL DEFAULT 1 CHECK (attempts >= 1),
    error      TEXT,                    -- last error text, if any
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    UNIQUE (dedup_key, direction)
);

CREATE INDEX IF NOT EXISTS channel_deliveries_chat_idx
    ON channel_deliveries (platform, chat_id, created_at DESC);

CREATE INDEX IF NOT EXISTS channel_deliveries_dedup_idx
    ON channel_deliveries (dedup_key);

-- migrate:down
DROP TABLE IF EXISTS channel_deliveries CASCADE;
