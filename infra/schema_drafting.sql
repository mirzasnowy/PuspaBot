-- Co-drafting schema (B13).
--
-- Tables: drafts, draft_approvals.
--
-- A draft is text the agent wrote in the user's voice; it is never
-- sent by this schema or by any code path in puspa/drafting except
-- through DraftingService.send_draft, which requires a recorded,
-- unexpired, unconsumed approval bound to the exact draft text
-- (see puspa/drafting/drafting.py). ``status`` is only ever
-- ``draft``, ``approved``, or ``sent``.
--
-- A draft approval is a single-use record: it binds one draft id
-- to the SHA-256 of the draft text at approval time, expires after
-- 24 hours (the same window as the G4 gate), and is consumed by
-- the one send it authorises. Editing a draft after approval
-- changes its text hash, which voids the approval; the user must
-- approve again. Approvals are never deleted: a consumed approval
-- stays as the audit trail of the send.
--
-- Run AFTER infra/schema_core.sql (the users table must exist
-- first), as a Postgres superuser, from the build/ directory:
--
--     psql "dbname=puspa_test user=root host=/var/run/postgresql" \
--          -f infra/schema_drafting.sql
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + DROP POLICY IF EXISTS, so
-- re-running it is safe. The Python entry point is
-- puspa.drafting.create_schema(), which executes this file (the test
-- suite calls it; conftest does NOT apply it).

CREATE TABLE IF NOT EXISTS drafts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind                TEXT NOT NULL CHECK (kind IN ('message', 'document', 'email')),
    brief               TEXT NOT NULL,
    text                TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'approved', 'sent')),
    recipient           TEXT,
    channel             TEXT,
    decision_flags      JSONB NOT NULL DEFAULT '[]'::jsonb,
    related_decisions   JSONB NOT NULL DEFAULT '[]'::jsonb,
    voice_rules_applied JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at             TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_drafts_user_id ON drafts(user_id);
CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(user_id, status);

CREATE TABLE IF NOT EXISTS draft_approvals (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    draft_id     UUID NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'granted'
                 CHECK (status IN ('granted', 'consumed')),
    granted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '24 hours'),
    consumed_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_draft_approvals_draft_id ON draft_approvals(draft_id);

-- Application-role grants (same shape as infra/schema_rooms.sql):
-- puspa_app may read/write both tables; isolation between users
-- is enforced by the RLS policies below, not by revoking grants.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON drafts TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON draft_approvals TO puspa_app;
    END IF;
END $$;

ALTER TABLE drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE draft_approvals ENABLE ROW LEVEL SECURITY;

-- A draft belongs to one user: only its owner may read, write, or
-- delete it. A draft is personal memory, never shared with a room.
DROP POLICY IF EXISTS drafts_select ON drafts;
CREATE POLICY drafts_select ON drafts FOR SELECT TO puspa_app
    USING (user_id = current_setting('app.current_user_id', TRUE)::UUID);
DROP POLICY IF EXISTS drafts_insert ON drafts;
CREATE POLICY drafts_insert ON drafts FOR INSERT TO puspa_app
    WITH CHECK (user_id = current_setting('app.current_user_id', TRUE)::UUID);
DROP POLICY IF EXISTS drafts_update ON drafts;
CREATE POLICY drafts_update ON drafts FOR UPDATE TO puspa_app
    USING (user_id = current_setting('app.current_user_id', TRUE)::UUID);
DROP POLICY IF EXISTS drafts_delete ON drafts;
CREATE POLICY drafts_delete ON drafts FOR DELETE TO puspa_app
    USING (user_id = current_setting('app.current_user_id', TRUE)::UUID);

DROP POLICY IF EXISTS draft_approvals_select ON draft_approvals;
CREATE POLICY draft_approvals_select ON draft_approvals FOR SELECT TO puspa_app
    USING (user_id = current_setting('app.current_user_id', TRUE)::UUID);
DROP POLICY IF EXISTS draft_approvals_insert ON draft_approvals;
CREATE POLICY draft_approvals_insert ON draft_approvals FOR INSERT TO puspa_app
    WITH CHECK (user_id = current_setting('app.current_user_id', TRUE)::UUID);
DROP POLICY IF EXISTS draft_approvals_update ON draft_approvals;
CREATE POLICY draft_approvals_update ON draft_approvals FOR UPDATE TO puspa_app
    USING (user_id = current_setting('app.current_user_id', TRUE)::UUID);
DROP POLICY IF EXISTS draft_approvals_delete ON draft_approvals;
CREATE POLICY draft_approvals_delete ON draft_approvals FOR DELETE TO puspa_app
    USING (user_id = current_setting('app.current_user_id', TRUE)::UUID);
