-- Creator support purchases: credit-only revenue share, no cash-out (B17, migration 0043_creator_support.sql).
--
-- Creates creator_support_purchases (one row per support purchase of a
-- published B16 gallery work: the supporter, the creator, the amount
-- paid in Rupiah, the platform commission, and the credits granted to
-- the creator). The creator's share is paid in Puspa credits ONLY:
-- there is no cash-out path anywhere in this feature, and the credit
-- value is always below the price paid, so buying your own work to
-- farm credits always loses money (see puspa/gallery/support.py).
--
-- Beta follows the B8 prepare-only rule: a support is recorded with
-- status 'prepared' and no real money moves. Charging the supporter
-- and minting the credits for real is hosted-service work that comes
-- after B8 leaves prepare-only.
--
-- The table is append-only by design: the store only INSERTs and
-- SELECTs, never UPDATEs or DELETEs a purchase row. Commission
-- applies only to digital gallery works: the split function refuses
-- any other subject kind (price check, authenticity check), so no
-- commission is ever taken on physical goods.
--
-- Source: puspa/gallery/support.py.
--
-- Reversible: yes. The down section drops the table this migration
-- creates (and its data — roll back on a data copy first, never on
-- a live database without a backup). The puspa_app role, once created
-- by 0001, is never dropped by any down migration.
--
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
CREATE TABLE IF NOT EXISTS creator_support_purchases (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES gallery_works (id) ON DELETE CASCADE,
    supporter_id TEXT NOT NULL,
    creator_id TEXT NOT NULL,
    amount_idr BIGINT NOT NULL CHECK (amount_idr >= 1000),
    commission_idr BIGINT NOT NULL CHECK (commission_idr >= 0),
    credits_granted INTEGER NOT NULL CHECK (credits_granted >= 1),
    status TEXT NOT NULL DEFAULT 'prepared' CHECK (status IN ('prepared')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS creator_support_work_idx
    ON creator_support_purchases (work_id, created_at DESC);
CREATE INDEX IF NOT EXISTS creator_support_creator_idx
    ON creator_support_purchases (creator_id, created_at DESC);

ALTER TABLE creator_support_purchases ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'creator_support_purchases' AND policyname = 'creator_support_read'
    ) THEN
        CREATE POLICY creator_support_read ON creator_support_purchases
            FOR SELECT USING (true);
    END IF;
END $$;

-- migrate:down
DROP TABLE IF EXISTS creator_support_purchases CASCADE;
