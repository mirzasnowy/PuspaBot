-- Channels: delivery ledger (E-08 migration 0008_channels.sql).
--
-- Creates the channel delivery ledger (channel_deliveries). No RLS: delivery rows carry no user_id and are only ever read by the channel workers that own them.
--
-- Source: infra/schema_channels.sql.
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
CREATE TABLE IF NOT EXISTS channel_deliveries (
    id         TEXT PRIMARY KEY,
    platform   TEXT NOT NULL,          -- "telegram" | "whatsapp" | ...
    chat_id    TEXT NOT NULL,          -- platform chat id, as text
    direction  TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    status     TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed', 'retrying')),
    dedup_key  TEXT NOT NULL,          -- platform:chat:message id
    attempts   INT NOT NULL DEFAULT 1 CHECK (attempts >= 1),
    error      TEXT,                    -- last error text, if any
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dedup_key, direction)
);

CREATE INDEX IF NOT EXISTS channel_deliveries_chat_idx
    ON channel_deliveries (platform, chat_id, created_at DESC);
CREATE INDEX IF NOT EXISTS channel_deliveries_dedup_idx
    ON channel_deliveries (dedup_key);

-- migrate:down
DROP TABLE IF EXISTS channel_deliveries CASCADE;
