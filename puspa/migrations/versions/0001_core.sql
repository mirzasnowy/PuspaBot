-- Core: ledger, accounts, agent registry (E-08 migration 0001_core.sql).
--
-- Creates the application role, the append-only ledger (events, kill_switch), the account tables (users, sessions, vault) with their RLS policies, and the agent registry (agents). Every other migration depends on this one.
--
-- Sources: puspa/ledger/schema.sql, puspa/accounts/schema.sql, puspa/accounts/schema_agent.sql (role block from the old infra/schema_core.sql setup script).
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
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        CREATE ROLE puspa_app NOLOGIN;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
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
        'false_report', 'data_consent'
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

ALTER TABLE events DROP CONSTRAINT IF EXISTS event_jenis_check;
ALTER TABLE events ADD CONSTRAINT event_jenis_check CHECK (type IN (
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
    'false_report', 'data_consent'
));

ALTER TABLE events ADD COLUMN IF NOT EXISTS valid_at TIMESTAMPTZ;
UPDATE events SET valid_at = created_at WHERE valid_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_event_valid_at ON events (valid_at);

REVOKE UPDATE, DELETE ON events FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT ON events TO puspa_app;
        REVOKE UPDATE, DELETE ON events FROM puspa_app;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS kill_switch (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    active BOOLEAN NOT NULL DEFAULT FALSE,
    actor TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

REVOKE INSERT, UPDATE, DELETE ON kill_switch FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT ON kill_switch TO puspa_app;
        REVOKE INSERT, UPDATE, DELETE ON kill_switch FROM puspa_app;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL UNIQUE,
    device_label    TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked         BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_sesi_user_id ON sessions(user_id);

CREATE TABLE IF NOT EXISTS vault (
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    ciphertext TEXT NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, name)
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON users TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON sessions TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON vault TO puspa_app;
    END IF;
END $$;


ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE vault ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS users_view_own ON users;
CREATE POLICY users_view_own ON users
    FOR SELECT
    USING (id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS users_register_or_own ON users;
CREATE POLICY users_register_or_own ON users
    FOR INSERT
    WITH CHECK (
        coalesce(current_setting('app.current_user_id', true), '') = ''
        OR id::text = coalesce(current_setting('app.current_user_id', true), '')
    );

DROP POLICY IF EXISTS users_update_own ON users;
CREATE POLICY users_update_own ON users
    FOR UPDATE
    USING (id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS users_delete_own ON users;
CREATE POLICY users_delete_own ON users
    FOR DELETE
    USING (id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS sessions_own ON sessions;
CREATE POLICY sessions_own ON sessions
    FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS vault_own ON vault;
CREATE POLICY vault_own ON vault
    FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_user_id ON agents(user_id);

-- migrate:down
DROP TABLE IF EXISTS kill_switch, events, agents, vault, sessions, users CASCADE;
