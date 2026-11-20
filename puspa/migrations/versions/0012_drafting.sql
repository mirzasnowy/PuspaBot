-- Drafting: co-written drafts and approvals (E-08 migration 0012_drafting.sql).
--
-- Creates the co-drafting tables (drafts, draft_approvals) with RLS.
--
-- Source: infra/schema_drafting.sql.
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

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON drafts TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON draft_approvals TO puspa_app;
    END IF;
END $$;

ALTER TABLE drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE draft_approvals ENABLE ROW LEVEL SECURITY;

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

-- migrate:down
DROP TABLE IF EXISTS draft_approvals, drafts CASCADE;
