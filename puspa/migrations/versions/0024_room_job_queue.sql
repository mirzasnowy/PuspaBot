-- Per-room job queue (P-05, plan section 12.1): one serial queue
-- per room so two members asking for conflicting things are
-- serialised instead of racing. A job is a small durable record;
-- the worker claims the oldest queued job for a room while holding
-- a Postgres advisory lock keyed on the room id, runs its handler,
-- then marks it done (with the handler's result) or failed (with
-- the error). Kinds today: ``summarize`` (deterministic chat
-- summary, recorded as a room summary — no model call) and ``note``
-- (record one room-visible note).
--
-- Reversible: yes. The down section drops the table.
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
CREATE TABLE IF NOT EXISTS room_job_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id UUID NOT NULL REFERENCES rooms (id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('summarize', 'note')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'processing', 'done', 'failed')),
    requested_by UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    result TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_room_job_queue_room_status
    ON room_job_queue (room_id, status, created_at);

-- migrate:down
DROP TABLE IF EXISTS room_job_queue;
