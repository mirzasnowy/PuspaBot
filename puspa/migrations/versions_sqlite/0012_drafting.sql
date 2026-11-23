-- Generated from puspa/migrations/versions/0012_drafting.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS drafts (
    id                  UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind                TEXT NOT NULL CHECK (kind IN ('message', 'document', 'email')),
    brief               TEXT NOT NULL,
    text                TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'approved', 'sent')),
    recipient           TEXT,
    channel             TEXT,
    decision_flags      JSONB NOT NULL DEFAULT '[]',
    related_decisions   JSONB NOT NULL DEFAULT '[]',
    voice_rules_applied JSONB NOT NULL DEFAULT '[]',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    sent_at             TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_drafts_user_id ON drafts(user_id);

CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(user_id, status);

CREATE TABLE IF NOT EXISTS draft_approvals (
    id           UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    draft_id     UUID NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'granted'
                 CHECK (status IN ('granted', 'consumed')),
    granted_at   TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    expires_at   TIMESTAMPTZ NOT NULL DEFAULT ((strftime('%Y-%m-%dT%H:%M:%f+00:00','now','+24 hours'))),
    consumed_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_draft_approvals_draft_id ON draft_approvals(draft_id);

-- migrate:down
DROP TABLE IF EXISTS draft_approvals, drafts CASCADE;
