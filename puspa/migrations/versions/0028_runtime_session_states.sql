-- Durable session state for the hierarchy runtime (V2-01).
--
-- The hierarchy runtime (`puspa/runtime/hierarchy.py`) kept session
-- state in memory only (`InMemorySessionStore`): a session survived
-- across turns inside one process and was lost on restart. This
-- migration adds the table the durable Postgres backend
-- (`puspa/runtime/session_store.py`, `PostgresSessionStore`) writes
-- to: one row per session id, holding the whole state snapshot as
-- JSONB plus the owning user and app name.
--
-- `user_id` is plain TEXT, not a foreign key: the hierarchy runtime
-- keys sessions by opaque string user ids (see `SessionState`), so
-- there is no users-table row to point at. Ownership is enforced
-- the same way as the other runtime tables: row level security
-- with the `app.current_user_id` setting, plus a grant to the
-- `puspa_app` role when that role exists.
--
-- Reversible: yes. The down section drops the policy and the table
-- (and their data, so roll back on a data copy first, never on a live
-- database without a backup).
--
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
CREATE TABLE IF NOT EXISTS runtime_session_states (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT '',
    app_name TEXT NOT NULL DEFAULT 'puspa',
    state JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_runtime_session_states_user_id
    ON runtime_session_states(user_id);

ALTER TABLE runtime_session_states ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON runtime_session_states TO puspa_app;
    END IF;
END $$;

DROP POLICY IF EXISTS isolasi_runtime_session_states
    ON runtime_session_states;
CREATE POLICY isolasi_runtime_session_states ON runtime_session_states
    USING (current_setting('app.current_user_id', true) = user_id)
    WITH CHECK (current_setting('app.current_user_id', true) = user_id);

-- migrate:down
DROP POLICY IF EXISTS isolasi_runtime_session_states
    ON runtime_session_states;
DROP TABLE IF EXISTS runtime_session_states;
