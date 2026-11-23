-- Generated from puspa/migrations/versions/0022_embedding_health.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
ALTER TABLE episodic ADD COLUMN IF NOT EXISTS embedder_id TEXT NOT NULL DEFAULT 'hash';

ALTER TABLE episodic ADD COLUMN IF NOT EXISTS dimension INTEGER NOT NULL DEFAULT 384;

CREATE INDEX IF NOT EXISTS idx_episodic_embedder
    ON episodic (embedder_id, dimension);

CREATE TABLE IF NOT EXISTS embedding_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT DEFAULT 1 CHECK (id = 1),
    active_embedder_id TEXT NOT NULL DEFAULT 'hash',
    active_dimension INTEGER NOT NULL DEFAULT 384 CHECK (active_dimension > 0),
    status TEXT NOT NULL DEFAULT 'healthy'
        CHECK (status IN ('healthy', 'degraded')),
    fallback_count INTEGER NOT NULL DEFAULT 0 CHECK (fallback_count >= 0),
    reason TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE TABLE IF NOT EXISTS embedding_reembed_jobs (
    id UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    from_embedder_id TEXT NOT NULL,
    to_embedder_id TEXT NOT NULL,
    from_dimension INTEGER NOT NULL CHECK (from_dimension > 0),
    to_dimension INTEGER NOT NULL CHECK (to_dimension > 0),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'done', 'failed')),
    total_rows INTEGER NOT NULL DEFAULT 0 CHECK (total_rows >= 0),
    done_rows INTEGER NOT NULL DEFAULT 0 CHECK (done_rows >= 0),
    current_tier INTEGER NOT NULL DEFAULT 0 CHECK (current_tier >= 0),
    tiers JSONB NOT NULL DEFAULT '[]',
    error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_reembed_jobs_status
    ON embedding_reembed_jobs (status, created_at DESC);

CREATE TABLE IF NOT EXISTS embedding_canary_baselines (
    id UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    embedder_id TEXT NOT NULL,
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    recall DOUBLE PRECISION NOT NULL CHECK (recall >= 0 AND recall <= 1),
    question_count INTEGER NOT NULL CHECK (question_count > 0),
    measured_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    UNIQUE (embedder_id, dimension)
);

-- migrate:down
DROP TABLE IF EXISTS embedding_canary_baselines CASCADE;

DROP TABLE IF EXISTS embedding_reembed_jobs CASCADE;

DROP TABLE IF EXISTS embedding_state CASCADE;

DROP INDEX IF EXISTS idx_episodic_embedder;

ALTER TABLE episodic DROP COLUMN IF EXISTS dimension;

ALTER TABLE episodic DROP COLUMN IF EXISTS embedder_id;
