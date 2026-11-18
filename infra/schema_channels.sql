-- Channel schema (G14): delivery ledger for Telegram / WhatsApp.
--
-- Tables: channel_deliveries.
--
-- Run AFTER infra/schema_core.sql (the users table must exist first),
-- as a Postgres superuser, from the build/ directory:
--
--     psql "dbname=puspa_test user=root host=/var/run/postgresql" \
--          -f infra/schema_channels.sql
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, so re-running it is safe.
-- The Python entry point is puspa.channels.schema.create_schema(),
-- which executes this file (the test suite calls it; conftest does
-- NOT apply it).
--
-- Design note: the delivery ledger is deliberately its OWN table,
-- not the core ledger (puspa.ledger). The core ledger is a closed
-- set of 31 task event types in a hash-chained audit log; delivery
-- records are operational rows that need lookup by platform
-- message id and per-attempt status updates, which the
-- append-only task log cannot do without a new event type — and no
-- new core event type is added here. See docs/channels.md.
--
-- No password anywhere in this file: auth is peer auth (local
-- socket, OS user = Postgres role). Do not add a password here.
-- No message CONTENT is stored here: only ids, direction, status,
-- attempt counts, and error text. Message text never lands in
-- this table (same rule as the content-free notifications).

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
