-- Workflows and custom modules (E-08 migration 0005_workflow.sql).
--
-- Creates the custom-workflow tables (workflows, workflow_versions, workflow_runs, workflow_approvals) and the custom-module tables (modules, module_versions, module_tables, module_rows) with RLS. The version tables carry no RLS: their isolation is checked in application code against the owning workflow/module.
--
-- Source: infra/schema_workflow.sql.
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
CREATE TABLE IF NOT EXISTS workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL,
    name TEXT NOT NULL,
    trigger JSONB NOT NULL DEFAULT '{}'::jsonb,
    step JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'draft',
    active_version INTEGER NOT NULL DEFAULT 1,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    successful_runs INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT workflow_status_check CHECK (status IN ('draft', 'active', 'paused', 'deleted'))
);

CREATE TABLE IF NOT EXISTS workflow_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    versi INTEGER NOT NULL,
    trigger JSONB NOT NULL DEFAULT '{}'::jsonb,
    step JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workflow_id, versi)
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL,
    trigger_jenis TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    cost_rp INTEGER NOT NULL DEFAULT 0,
    step_results JSONB NOT NULL DEFAULT '[]'::jsonb,
    reason TEXT NOT NULL DEFAULT '',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS workflow_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    jalan_id UUID NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    workflow_id UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT approval_workflow_status_check
        CHECK (status IN ('pending', 'granted', 'rejected', 'expired', 'cancelled'))
);

CREATE TABLE IF NOT EXISTS modules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    manifest JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    active_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT module_status_check CHECK (status IN ('draft', 'active', 'paused', 'deleted'))
);

CREATE TABLE IF NOT EXISTS module_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    module_id UUID NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    versi INTEGER NOT NULL,
    manifest JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (module_id, versi)
);

CREATE TABLE IF NOT EXISTS module_tables (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    module_id UUID NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kolom JSONB NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE (module_id, name)
);

CREATE TABLE IF NOT EXISTS module_rows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    table_id UUID NOT NULL REFERENCES module_tables(id) ON DELETE CASCADE,
    module_id UUID NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_workflow_user ON workflows(user_id, status);
CREATE INDEX IF NOT EXISTS idx_jalan_workflow_workflow ON workflow_runs(workflow_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_approval_workflow_jalan ON workflow_approvals(jalan_id, status);
CREATE INDEX IF NOT EXISTS idx_module_user ON modules(user_id, status);
CREATE INDEX IF NOT EXISTS idx_rows_module_table ON module_rows(table_id, created_at DESC);

ALTER TABLE workflows ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE modules ENABLE ROW LEVEL SECURITY;
ALTER TABLE module_tables ENABLE ROW LEVEL SECURITY;
ALTER TABLE module_rows ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['workflows', 'workflow_runs', 'workflow_approvals',
                             'modules', 'module_tables', 'module_rows'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', 'isolasi_' || t, t);
        EXECUTE format(
            'CREATE POLICY %I ON %I USING (current_setting(%L, true) = user_id::text) WITH CHECK (current_setting(%L, true) = user_id::text)',
            'isolasi_' || t, t, 'app.current_user_id', 'app.current_user_id');
    END LOOP;
END $$;

-- migrate:down
DROP TABLE IF EXISTS module_rows, module_tables, module_versions, modules, workflow_approvals, workflow_runs, workflow_versions, workflows CASCADE;
