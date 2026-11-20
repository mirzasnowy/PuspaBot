-- Scheduler: work items and event receipts (E-08 migration 0018_scheduler.sql).
--
-- Creates the scheduler tables (work_items, event_receipts). No RLS: both tables are keyed by idempotency key, not by user.
--
-- Sources: puspa/scheduler/claim.py (work_items) and puspa/scheduler/receipts.py (event_receipts).
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
CREATE TABLE IF NOT EXISTS work_items (
    id TEXT PRIMARY KEY,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    lease_owner TEXT,
    lease_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_work_items_claimable
    ON work_items (created_at) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS event_receipts (
    idempotency_key TEXT PRIMARY KEY,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- migrate:down
DROP TABLE IF EXISTS event_receipts, work_items CASCADE;
