-- Payments: prepared payments and billing usage (E-08 migration 0013_payments.sql).
--
-- Creates the prepared-payments table (prepared_payments, prepare-only: no execution) with RLS, and the billing usage ledger (billing_usage_rows).
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
CREATE TABLE IF NOT EXISTS prepared_payments (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipient_name    TEXT NOT NULL CHECK (recipient_name <> ''),
    recipient_account TEXT NOT NULL CHECK (recipient_account <> ''),
    recipient_verified_by TEXT NOT NULL CHECK (recipient_verified_by <> ''),
    amount_rupiah     BIGINT NOT NULL CHECK (amount_rupiah > 0),
    rail_id           TEXT NOT NULL CHECK (rail_id <> ''),
    rail_display      TEXT NOT NULL CHECK (rail_display <> ''),
    rail_kind         TEXT NOT NULL CHECK (rail_kind <> ''),
    rail_bank         TEXT NOT NULL DEFAULT '',
    evidence          JSONB NOT NULL DEFAULT '[]'::JSONB,
    idempotency_key   TEXT,
    status            TEXT NOT NULL DEFAULT 'awaiting_approval'
                      CHECK (status IN (
                          'awaiting_approval', 'approved', 'rejected',
                          'expired', 'paid_by_user'
                      )),
    room_id           UUID,
    task_id           TEXT NOT NULL DEFAULT '',
    outcome_id        TEXT,
    approval_request_id TEXT,
    prepared_by       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    prepared_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at        TIMESTAMPTZ NOT NULL,
    approved_at       TIMESTAMPTZ,
    approved_by       UUID REFERENCES users(id) ON DELETE SET NULL,
    paid_asserted_at  TIMESTAMPTZ,
    paid_asserted_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    paid_assertion    TEXT,
    UNIQUE (user_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_prepared_payments_user_id ON prepared_payments(user_id);
CREATE INDEX IF NOT EXISTS idx_prepared_payments_status ON prepared_payments(status);
CREATE INDEX IF NOT EXISTS idx_prepared_payments_expires_at ON prepared_payments(expires_at);

GRANT SELECT, INSERT, UPDATE ON prepared_payments TO puspa_app;

ALTER TABLE prepared_payments ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS prepared_payments_select ON prepared_payments;
CREATE POLICY prepared_payments_select ON prepared_payments FOR SELECT TO puspa_app
    USING (user_id = current_setting('app.current_user_id', TRUE)::UUID);
DROP POLICY IF EXISTS prepared_payments_insert ON prepared_payments;
CREATE POLICY prepared_payments_insert ON prepared_payments FOR INSERT TO puspa_app
    WITH CHECK (user_id = current_setting('app.current_user_id', TRUE)::UUID);
DROP POLICY IF EXISTS prepared_payments_update ON prepared_payments;
CREATE POLICY prepared_payments_update ON prepared_payments FOR UPDATE TO puspa_app
    USING (user_id = current_setting('app.current_user_id', TRUE)::UUID);

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
DROP TABLE IF EXISTS billing_usage_rows, prepared_payments CASCADE;
