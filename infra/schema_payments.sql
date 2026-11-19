-- Agent payments, phase 1: prepare only (B8).
--
-- One row per prepared payment: a verified recipient + an amount
-- in integer Rupiah + a named Indonesian rail + the evidence that
-- proves both, with an idempotency key and an expiry. The user
-- pays in their own bank or e-wallet app; Puspa never executes a
-- payment, so this table has no gateway reference, no credential
-- column, and no executed flag to flip — there is nothing here
-- an executor could run.
--
-- Status flow: awaiting_approval -> approved -> paid_by_user
-- (the user asserts they paid in their own app), or
-- awaiting_approval -> rejected, or any open status -> expired
-- once past expires_at. A preparation becomes visible as
-- payable only after approval (see puspa.payments.prepare).
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + DROP POLICY IF
-- EXISTS, so the file is safe to apply on every test run and
-- after infra/schema_core.sql (the users table must exist
-- first). Amounts are integer Rupiah (not sen): B8 prepares
-- real-world payments, and the B11 split maths already own
-- the sen unit.
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

-- Owner-only: a user sees and changes only their own prepared
-- payments. The app sets app.current_user_id per request (same
-- session variable the room policies use).
DROP POLICY IF EXISTS prepared_payments_select ON prepared_payments;
CREATE POLICY prepared_payments_select ON prepared_payments FOR SELECT TO puspa_app
    USING (user_id = current_setting('app.current_user_id', TRUE)::UUID);
DROP POLICY IF EXISTS prepared_payments_insert ON prepared_payments;
CREATE POLICY prepared_payments_insert ON prepared_payments FOR INSERT TO puspa_app
    WITH CHECK (user_id = current_setting('app.current_user_id', TRUE)::UUID);
DROP POLICY IF EXISTS prepared_payments_update ON prepared_payments;
CREATE POLICY prepared_payments_update ON prepared_payments FOR UPDATE TO puspa_app
    USING (user_id = current_setting('app.current_user_id', TRUE)::UUID);
