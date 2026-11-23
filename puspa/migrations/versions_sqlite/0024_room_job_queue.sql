-- Generated from puspa/migrations/versions/0024_room_job_queue.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS room_job_queue (
    id UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    room_id UUID NOT NULL REFERENCES rooms (id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('summarize', 'note')),
    payload JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'processing', 'done', 'failed')),
    requested_by UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    result TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_room_job_queue_room_status
    ON room_job_queue (room_id, status, created_at);

-- migrate:down
DROP TABLE IF EXISTS room_job_queue;
