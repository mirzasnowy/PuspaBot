-- Generated from puspa/migrations/versions/0002_memory.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS profile_facts (
    id             UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    user_id        UUID NOT NULL,
    agent_id        TEXT,
    key          TEXT NOT NULL,
    value          TEXT NOT NULL,
    source           TEXT NOT NULL CHECK (source IN ('user_stated', 'user_confirmed', 'inferred')),
    status         TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN ('proposed', 'active', 'rejected', 'replaced',
                                      'superseded', 'expired')),
    valid_from   TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    valid_until TIMESTAMPTZ,
    superseded_by UUID,
    event_id    UUID,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_fact_user_key ON profile_facts(user_id, key, status);

ALTER TABLE profile_facts ADD COLUMN IF NOT EXISTS superseded_by UUID;

ALTER TABLE profile_facts ADD COLUMN IF NOT EXISTS updated_at
    TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));

CREATE TABLE IF NOT EXISTS episodic (
    id          UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    user_id     UUID NOT NULL,
    agent_id     TEXT,
    task_id    UUID,
    type       TEXT NOT NULL,
    content         TEXT NOT NULL,
    embedding   BLOB,
    event_id UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_episodik_user ON episodic(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS procedures (
    id                UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    user_id           UUID NOT NULL,
    service           TEXT NOT NULL,
    versi             INTEGER NOT NULL DEFAULT 1,
    step           JSONB NOT NULL DEFAULT '[]',
    output_schema    JSONB NOT NULL DEFAULT '{}',
    required_checks JSONB NOT NULL DEFAULT '[]',
    status            TEXT NOT NULL DEFAULT 'trial'
                      CHECK (status IN ('trial', 'verified', 'broken')),
    successful_runs      INTEGER NOT NULL DEFAULT 0,
    failed_runs       INTEGER NOT NULL DEFAULT 0,
    consecutive_failed_runs INTEGER NOT NULL DEFAULT 0,
    agent_ids          JSONB NOT NULL DEFAULT '[]',
    source_event_id  UUID,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_prosedur_user_service ON procedures(user_id, service, status);

CREATE TABLE IF NOT EXISTS slice_grants (
    id           UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    user_id      UUID NOT NULL,
    agent_id      TEXT,
    subagent_id  TEXT,
    query        TEXT NOT NULL,
    kolom        TEXT[] NOT NULL DEFAULT '{}',
    token_ref    TEXT NOT NULL,
    event_id  UUID,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_irisan_user ON slice_grants(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS summaries (
    id            UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    user_id       UUID NOT NULL,
    agent_id       TEXT,
    period_start  TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    summary     TEXT NOT NULL,
    event_ids  UUID[] NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_summary_user ON summaries(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS style_examples (
    id      UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    user_id UUID NOT NULL,
    text    TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_example_gaya_user ON style_examples(user_id, created_at ASC);

CREATE TABLE IF NOT EXISTS onboarding (
    user_id               UUID PRIMARY KEY,
    current_step      INTEGER NOT NULL DEFAULT 0,
    name                  TEXT,
    style                  TEXT,
    test_notification             TEXT,
    connector              TEXT NOT NULL DEFAULT 'nanti',
    first_task         TEXT,
    first_result_verified BOOLEAN,
    started_at               TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    completed_at               TIMESTAMPTZ,
    duration_seconds          NUMERIC
);

CREATE TABLE IF NOT EXISTS data_consents (
    id            UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    user_id       UUID NOT NULL,
    text_version    TEXT NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    event_id   UUID
);

CREATE INDEX IF NOT EXISTS idx_approval_user ON data_consents(user_id, approved_at DESC);

CREATE TABLE IF NOT EXISTS used_email_links (
    token_hash  TEXT PRIMARY KEY,
    used_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

-- migrate:down
DROP TABLE IF EXISTS used_email_links, data_consents, onboarding, style_examples, summaries, slice_grants, procedures, episodic, profile_facts CASCADE;
