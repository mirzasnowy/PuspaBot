-- Generated from puspa/migrations/versions/0001_core.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS events (
    id UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    subagent_id TEXT,
    type TEXT NOT NULL CHECK (type IN (
        'task_created', 'plan_created', 'subagent_created',
        'handoff_proposed', 'handoff_accepted', 'handoff_handed_over',
        'handoff_rejected', 'handoff_expired', 'heartbeat',
        'tool_called', 'tool_completed', 'evidence_recorded',
        'verification_completed', 'approval_requested', 'approval_granted',
        'approval_rejected', 'user_message', 'agent_message', 'budget_exhausted',
        'task_completed', 'task_failed', 'task_stopped',
        'workflow_proposed', 'workflow_activated', 'workflow_paused',
        'trigger_fired', 'trigger_missed',
        'module_proposed', 'module_activated',
        'false_report', 'data_consent',
        'takeover_started', 'takeover_released'
    )),
    content JSONB NOT NULL DEFAULT '{}',
    parent_id UUID REFERENCES events(id),
    previous_hash TEXT NOT NULL,
    hash TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_event_task_id_id ON events (task_id, id);

CREATE INDEX IF NOT EXISTS idx_event_jenis ON events (type);

CREATE INDEX IF NOT EXISTS idx_event_time ON events (created_at);

CREATE INDEX IF NOT EXISTS idx_events_outcome_user
    ON events ((content->>'kind'), (content->>'user_id'));

CREATE INDEX IF NOT EXISTS idx_events_outcome_room
    ON events ((content->>'kind'), (content->>'room_id'));

ALTER TABLE events ADD COLUMN IF NOT EXISTS valid_at TIMESTAMPTZ;

UPDATE events SET valid_at = created_at WHERE valid_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_event_valid_at ON events (valid_at);

CREATE TABLE IF NOT EXISTS kill_switch (
    id INTEGER PRIMARY KEY AUTOINCREMENT DEFAULT 1 CHECK (id = 1),
    active BOOLEAN NOT NULL DEFAULT FALSE,
    actor TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    id              UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL UNIQUE,
    device_label    TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    revoked         BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_sesi_user_id ON sessions(user_id);

CREATE TABLE IF NOT EXISTS vault (
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    ciphertext TEXT NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    PRIMARY KEY (user_id, name)
);

CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_agent_user_id ON agents(user_id);

-- migrate:down
DROP TABLE IF EXISTS kill_switch, events, agents, vault, sessions, users CASCADE;
