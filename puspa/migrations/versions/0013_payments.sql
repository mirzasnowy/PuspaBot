-- Payments: prepared payments and billing usage (E-08 migration 0013_payments.sql).
--
-- Creates the billing usage ledger (billing_usage_rows).
--
-- The prepared-payments table (prepared_payments, prepare-only:
-- no execution) lives in the hosted service: no public module
-- reads or writes it, so its DDL is not in this file.
--
-- Sources: infra/schema_payments.sql; billing_usage_rows from puspa/billing/ledger.py (USAGE_SCHEMA_SQL).
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
CREATE TABLE IF NOT EXISTS billing_usage_rows (
    id BIGSERIAL PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    thread_id TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    driver TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    cached_tokens BIGINT NOT NULL DEFAULT 0,
    cost_idr BIGINT NOT NULL DEFAULT 0,
    trigger TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    steps INT NOT NULL DEFAULT 0,
    tools JSONB NOT NULL DEFAULT '[]',
    tool_errors INT NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'IDR',
    provider TEXT NOT NULL DEFAULT '',
    module_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_billing_usage_user_time
    ON billing_usage_rows (user_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_billing_usage_task
    ON billing_usage_rows (user_id, task_id);
CREATE INDEX IF NOT EXISTS idx_billing_usage_bot_time
    ON billing_usage_rows (bot_id, recorded_at);

-- migrate:down
DROP TABLE IF EXISTS billing_usage_rows CASCADE;
