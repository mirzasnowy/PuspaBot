-- Write-through turn-commit retry queue (V2-06).
--
-- `commit_turn()` writes the ledger entry and the durable memory
-- in a single transaction. When either write fails, the whole
-- transaction rolls back and the turn must not report success.
-- The failed write must not vanish: its payload lands in this
-- queue, in a separate transaction, so it can be retried later
-- and so the failure stays visible (`list_pending_retries`).
--
-- `turn_commit_retries`: one row per failed commit attempt.
-- `payload` is the full `commit_turn()` call (user, task, agent,
-- items, reply text) as JSON, so a retry rebuilds the same call.
-- `error` is the failure that stopped the write, in plain words.
-- `attempts` counts tries including the first failed one.
-- `status` is `pending` (waiting for a retry), `done` (a retry
-- committed it), or `dead` (too many failed retries; needs a
-- human to look at it, never retried again automatically).
--
-- `user_id` is plain TEXT, not a foreign key: durable memory is
-- keyed by the same opaque string user ids the rest of the
-- memory layer uses. Ownership is enforced the same way as the
-- other memory tables: row level security with the
-- `app.current_user_id` setting, plus a grant to the `puspa_app`
-- role when that role exists.
--
-- Reversible: yes. The down section drops the policy and the
-- table (and their data, so roll back on a data copy first,
-- never on a live database without a backup).
--
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
CREATE TABLE IF NOT EXISTS turn_commit_retries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    error TEXT NOT NULL,
    attempts INT NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'done', 'dead')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_turn_commit_retries_pending
    ON turn_commit_retries(status, created_at);

ALTER TABLE turn_commit_retries ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON turn_commit_retries TO puspa_app;
    END IF;
END $$;

DROP POLICY IF EXISTS isolasi_turn_commit_retries
    ON turn_commit_retries;
CREATE POLICY isolasi_turn_commit_retries ON turn_commit_retries
    USING (current_setting('app.current_user_id', true) = user_id)
    WITH CHECK (current_setting('app.current_user_id', true) = user_id);

-- migrate:down
DROP POLICY IF EXISTS isolasi_turn_commit_retries
    ON turn_commit_retries;
DROP TABLE IF EXISTS turn_commit_retries;
