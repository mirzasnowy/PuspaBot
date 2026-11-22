-- Missions with checklist (V2-42, issue #219).
--
-- A mission is one multi-step goal the agent works through on its
-- own, with a checklist the user can follow in the PWA. The
-- checklist lives as JSONB on the mission row: one entry per step
-- (title, status, result, and an optional step-ask question with
-- its options and the user's answer). The webhook token wakes the
-- mission from outside: a POST to /api/missions/webhook/<token>
-- records the payload and queues the next step.
--
-- Reversible: yes. The down section drops the table this migration
-- creates (and its data — roll back on a data copy first, never on
-- a live database without a backup).
--
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
CREATE TABLE IF NOT EXISTS missions (
    id            TEXT PRIMARY KEY,
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    goal          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'waiting', 'done', 'failed', 'stopped')),
    steps         JSONB NOT NULL DEFAULT '[]'::jsonb,
    webhook_token TEXT NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_missions_user_id ON missions(user_id);

-- migrate:down
DROP TABLE IF EXISTS missions;
