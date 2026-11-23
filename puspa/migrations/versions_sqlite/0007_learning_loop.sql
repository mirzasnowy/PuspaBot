-- Generated from puspa/migrations/versions/0007_learning_loop.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS loop_corrections (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    task TEXT NOT NULL,
    predicted JSONB NOT NULL,
    observed JSONB NOT NULL,
    error TEXT,
    source TEXT,
    feedback_source TEXT,
    correction_count INTEGER NOT NULL DEFAULT 1,
    experience_ids JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    PRIMARY KEY (user_id, agent_id, task)
);

ALTER TABLE loop_corrections
    ADD COLUMN IF NOT EXISTS feedback_source TEXT;

CREATE TABLE IF NOT EXISTS loop_experiences (
    id TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    task TEXT NOT NULL,
    kind TEXT,
    source TEXT,
    feedback_source TEXT,
    verdict TEXT,
    predicted JSONB,
    observed JSONB,
    error TEXT,
    context JSONB NOT NULL DEFAULT '{}',
    position INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

-- migrate:down
DROP TABLE IF EXISTS loop_experiences, loop_corrections CASCADE;
