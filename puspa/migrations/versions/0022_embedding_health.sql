-- Embedding health: visible degradation (E-14, plan section 7.4).
--
-- Every episodic vector row carries the embedder that produced it
-- (``embedder_id`` + ``dimension``), so vectors from different
-- embedders can never be mixed in one search. Three new tables:
--
-- - embedding_state: one singleton row (id = 1) holding the active
--   embedder, the health status ("healthy" / "degraded"), the
--   hash-fallback count, and the reason for the current status.
--   The write path refuses an embedder that does not match the
--   active one; the sanctioned way to change it is a tiered
--   re-embed job, never a sudden column change.
-- - embedding_reembed_jobs: one row per re-embed job, with its
--   tier plan (JSONB), per-tier progress, and status
--   (queued / running / done / failed).
-- - embedding_canary_baselines: one reference recall row per
--   (embedder_id, dimension), measured on the frozen 30-question
--   canary set. A degraded measurement never silently replaces a
--   good baseline (see puspa/retrieval/embedding_health.py).
--
-- No new ledger event type: degradation is recorded in the ledger
-- as a ``heartbeat`` event with content kind
-- ``embedding_degraded`` (the closed set of 31 types stays closed).
--
-- These tables hold embedder metadata and job progress, never user
-- data, so there is no row-level security policy keyed on a user
-- id. The agent role (puspa_app) may read and write them, like the
-- episodic table itself.
--
-- Reversible: yes. The down section drops the tables this migration
-- creates and the columns it adds (and their data — roll back on a
-- data copy first, never on a live database without a backup). The
-- puspa_app role, once created by 0001, is never dropped by any
-- down migration.
--
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
ALTER TABLE episodic ADD COLUMN IF NOT EXISTS embedder_id TEXT NOT NULL DEFAULT 'hash';
ALTER TABLE episodic ADD COLUMN IF NOT EXISTS dimension INTEGER NOT NULL DEFAULT 384;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'episodic_dimension_positive'
    ) THEN
        ALTER TABLE episodic
            ADD CONSTRAINT episodic_dimension_positive CHECK (dimension > 0);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_episodic_embedder
    ON episodic (embedder_id, dimension);

CREATE TABLE IF NOT EXISTS embedding_state (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    active_embedder_id TEXT NOT NULL DEFAULT 'hash',
    active_dimension INTEGER NOT NULL DEFAULT 384 CHECK (active_dimension > 0),
    status TEXT NOT NULL DEFAULT 'healthy'
        CHECK (status IN ('healthy', 'degraded')),
    fallback_count INTEGER NOT NULL DEFAULT 0 CHECK (fallback_count >= 0),
    reason TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS embedding_reembed_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_embedder_id TEXT NOT NULL,
    to_embedder_id TEXT NOT NULL,
    from_dimension INTEGER NOT NULL CHECK (from_dimension > 0),
    to_dimension INTEGER NOT NULL CHECK (to_dimension > 0),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'done', 'failed')),
    total_rows INTEGER NOT NULL DEFAULT 0 CHECK (total_rows >= 0),
    done_rows INTEGER NOT NULL DEFAULT 0 CHECK (done_rows >= 0),
    current_tier INTEGER NOT NULL DEFAULT 0 CHECK (current_tier >= 0),
    tiers JSONB NOT NULL DEFAULT '[]'::jsonb,
    error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_reembed_jobs_status
    ON embedding_reembed_jobs (status, created_at DESC);

CREATE TABLE IF NOT EXISTS embedding_canary_baselines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    embedder_id TEXT NOT NULL,
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    recall DOUBLE PRECISION NOT NULL CHECK (recall >= 0 AND recall <= 1),
    question_count INTEGER NOT NULL CHECK (question_count > 0),
    measured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (embedder_id, dimension)
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON embedding_state TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON embedding_reembed_jobs TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON embedding_canary_baselines TO puspa_app;
    END IF;
END $$;

-- migrate:down
DROP TABLE IF EXISTS embedding_canary_baselines CASCADE;
DROP TABLE IF EXISTS embedding_reembed_jobs CASCADE;
DROP TABLE IF EXISTS embedding_state CASCADE;
DROP INDEX IF EXISTS idx_episodic_embedder;
ALTER TABLE episodic DROP CONSTRAINT IF EXISTS episodic_dimension_positive;
ALTER TABLE episodic DROP COLUMN IF EXISTS dimension;
ALTER TABLE episodic DROP COLUMN IF EXISTS embedder_id;
