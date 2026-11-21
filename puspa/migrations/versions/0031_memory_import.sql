-- Memory import batches, quarantine and forget list (V2-10).
--
-- Importing memory is the easiest way to plant a false memory, so
-- every import is tracked, quarantined text is held for approval,
-- and erased items are remembered so an old archive cannot bring
-- them back.
--
-- `memory_import_batches`: one row per approved import, with the
-- source (kosmos archive, MEMORY.md file, or adapter name), the
-- status (`importing`, `completed`, `rolled_back`) and the plan
-- counts, so a whole import can be rolled back by batch id.
--
-- `memory_import_items`: which facts one batch created (fact id,
-- key, disposition), so rollback expires exactly those facts.
--
-- `memory_import_quarantine`: imported text held for user review
-- (untrusted origin, or blocked as instruction-like / secret).
-- Status is `pending`, `approved`, `rejected` or `blocked`.
-- Blocked rows are kept for review and are never stored as facts.
--
-- `memory_forget_list`: erased (key, content hash) pairs. The
-- list stores the hash, not the text, so the forgotten content
-- itself is not kept. A later import skips any candidate that
-- matches a row here.
--
-- `user_id` is plain TEXT, not a foreign key: import state is
-- keyed by the same opaque string user ids the rest of the
-- memory layer uses. Ownership is enforced the same way as the
-- other memory tables: row level security with the
-- `app.current_user_id` setting, plus a grant to the `puspa_app`
-- role when that role exists.
--
-- Reversible: yes. The down section drops the policies and the
-- tables (and their data, so roll back on a data copy first,
-- never on a live database without a backup).
--
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
CREATE TABLE IF NOT EXISTS memory_import_batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'importing'
        CHECK (status IN ('importing', 'completed', 'rolled_back', 'failed')),
    counts JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_memory_import_batches_user
    ON memory_import_batches(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS memory_import_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID NOT NULL REFERENCES memory_import_batches(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    fact_id UUID,
    fact_key TEXT NOT NULL,
    disposition TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_memory_import_items_batch
    ON memory_import_items(batch_id, created_at);

CREATE TABLE IF NOT EXISTS memory_import_quarantine (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID REFERENCES memory_import_batches(id) ON DELETE SET NULL,
    user_id TEXT NOT NULL,
    fact_key TEXT NOT NULL,
    text TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'blocked')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_memory_import_quarantine_user_status
    ON memory_import_quarantine(user_id, status, created_at);

CREATE TABLE IF NOT EXISTS memory_forget_list (
    user_id TEXT NOT NULL,
    fact_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, fact_key, content_hash)
);

ALTER TABLE memory_import_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_import_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_import_quarantine ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_forget_list ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON memory_import_batches, memory_import_items,
               memory_import_quarantine, memory_forget_list TO puspa_app;
    END IF;
END $$;

DROP POLICY IF EXISTS isolasi_memory_import_batches
    ON memory_import_batches;
CREATE POLICY isolasi_memory_import_batches ON memory_import_batches
    USING (current_setting('app.current_user_id', true) = user_id)
    WITH CHECK (current_setting('app.current_user_id', true) = user_id);

DROP POLICY IF EXISTS isolasi_memory_import_items
    ON memory_import_items;
CREATE POLICY isolasi_memory_import_items ON memory_import_items
    USING (current_setting('app.current_user_id', true) = user_id)
    WITH CHECK (current_setting('app.current_user_id', true) = user_id);

DROP POLICY IF EXISTS isolasi_memory_import_quarantine
    ON memory_import_quarantine;
CREATE POLICY isolasi_memory_import_quarantine ON memory_import_quarantine
    USING (current_setting('app.current_user_id', true) = user_id)
    WITH CHECK (current_setting('app.current_user_id', true) = user_id);

DROP POLICY IF EXISTS isolasi_memory_forget_list
    ON memory_forget_list;
CREATE POLICY isolasi_memory_forget_list ON memory_forget_list
    USING (current_setting('app.current_user_id', true) = user_id)
    WITH CHECK (current_setting('app.current_user_id', true) = user_id);

-- migrate:down
DROP POLICY IF EXISTS isolasi_memory_import_batches
    ON memory_import_batches;
DROP POLICY IF EXISTS isolasi_memory_import_items
    ON memory_import_items;
DROP POLICY IF EXISTS isolasi_memory_import_quarantine
    ON memory_import_quarantine;
DROP POLICY IF EXISTS isolasi_memory_forget_list
    ON memory_forget_list;
DROP TABLE IF EXISTS memory_import_items;
DROP TABLE IF EXISTS memory_import_quarantine;
DROP TABLE IF EXISTS memory_import_batches;
DROP TABLE IF EXISTS memory_forget_list;
