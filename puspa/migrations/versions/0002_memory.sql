-- Memory: profile facts, episodic, procedures (E-08 migration 0002_memory.sql).
--
-- Creates the memory tables (profile_facts, episodic, procedures, slice_grants, summaries, style_examples, onboarding, data_consents, used_email_links) with RLS, plus the pgvector extension the episodic embedding column needs.
--
-- Source: puspa/memory/schema.sql (includes the G7 versioned-facts upgrade block).
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
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS profile_facts (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL,
    agent_id        TEXT,
    key          TEXT NOT NULL,
    value          TEXT NOT NULL,
    source           TEXT NOT NULL CHECK (source IN ('user_stated', 'user_confirmed', 'inferred')),
    status         TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN ('proposed', 'active', 'rejected', 'replaced',
                                      'superseded', 'expired')),
    valid_from   TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_until TIMESTAMPTZ,
    superseded_by UUID,
    event_id    UUID,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fact_user_key ON profile_facts(user_id, key, status);

ALTER TABLE profile_facts ADD COLUMN IF NOT EXISTS superseded_by UUID;
ALTER TABLE profile_facts ADD COLUMN IF NOT EXISTS updated_at
    TIMESTAMPTZ NOT NULL DEFAULT now();
DO $$
DECLARE cname TEXT;
BEGIN
    SELECT conname INTO cname FROM pg_constraint
        WHERE conrelid = 'profile_facts'::regclass AND contype = 'c'
        AND pg_get_constraintdef(oid) LIKE '%proposed%'
        AND pg_get_constraintdef(oid) NOT LIKE '%superseded%';
    IF cname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE profile_facts DROP CONSTRAINT %I', cname);
        ALTER TABLE profile_facts ADD CONSTRAINT profile_facts_status_check
            CHECK (status IN ('proposed', 'active', 'rejected', 'replaced',
                              'superseded', 'expired'));
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS episodic (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL,
    agent_id     TEXT,
    task_id    UUID,
    type       TEXT NOT NULL,
    content         TEXT NOT NULL,
    embedding   vector(384),
    event_id UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_episodik_user ON episodic(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_episodik_embedding
    ON episodic USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS procedures (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL,
    service           TEXT NOT NULL,
    versi             INTEGER NOT NULL DEFAULT 1,
    step           JSONB NOT NULL DEFAULT '[]'::jsonb,
    output_schema    JSONB NOT NULL DEFAULT '{}'::jsonb,
    required_checks JSONB NOT NULL DEFAULT '[]'::jsonb,
    status            TEXT NOT NULL DEFAULT 'trial'
                      CHECK (status IN ('trial', 'verified', 'broken')),
    successful_runs      INTEGER NOT NULL DEFAULT 0,
    failed_runs       INTEGER NOT NULL DEFAULT 0,
    consecutive_failed_runs INTEGER NOT NULL DEFAULT 0,
    agent_ids          JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_event_id  UUID,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_prosedur_user_service ON procedures(user_id, service, status);

CREATE TABLE IF NOT EXISTS slice_grants (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL,
    agent_id      TEXT,
    subagent_id  TEXT,
    query        TEXT NOT NULL,
    kolom        TEXT[] NOT NULL DEFAULT '{}',
    token_ref    TEXT NOT NULL,
    event_id  UUID,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_irisan_user ON slice_grants(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS summaries (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL,
    agent_id       TEXT,
    period_start  TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    summary     TEXT NOT NULL,
    event_ids  UUID[] NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_summary_user ON summaries(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS style_examples (
    id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    text    TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_example_gaya_user ON style_examples(user_id, created_at ASC);

CREATE TABLE IF NOT EXISTS onboarding (
    user_id               UUID PRIMARY KEY,
    current_step      INTEGER NOT NULL DEFAULT 0,
    name                  TEXT,
    style                  TEXT,
    test_notification             TEXT,
    connector              TEXT NOT NULL DEFAULT 'nanti',
    first_task         TEXT,
    first_result_verified BOOLEAN,
    started_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at               TIMESTAMPTZ,
    duration_seconds          NUMERIC
);

CREATE TABLE IF NOT EXISTS data_consents (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL,
    text_version    TEXT NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_id   UUID
);
CREATE INDEX IF NOT EXISTS idx_approval_user ON data_consents(user_id, approved_at DESC);

CREATE TABLE IF NOT EXISTS used_email_links (
    token_hash  TEXT PRIMARY KEY,
    used_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON profile_facts TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON episodic TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON procedures TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON slice_grants TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON summaries TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON style_examples TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON onboarding TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON data_consents TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON used_email_links TO puspa_app;
    END IF;
END $$;

ALTER TABLE profile_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE episodic ENABLE ROW LEVEL SECURITY;
ALTER TABLE procedures ENABLE ROW LEVEL SECURITY;
ALTER TABLE slice_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE style_examples ENABLE ROW LEVEL SECURITY;
ALTER TABLE onboarding ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_consents ENABLE ROW LEVEL SECURITY;
ALTER TABLE used_email_links ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS fact_profile_own_own ON profile_facts;
CREATE POLICY fact_profile_own_own ON profile_facts FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS episodik_own_own ON episodic;
CREATE POLICY episodik_own_own ON episodic FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS prosedur_own_own ON procedures;
CREATE POLICY prosedur_own_own ON procedures FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS pemberian_irisan_own_own ON slice_grants;
CREATE POLICY pemberian_irisan_own_own ON slice_grants FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS summary_own_own ON summaries;
CREATE POLICY summary_own_own ON summaries FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS example_gaya_own_own ON style_examples;
CREATE POLICY example_gaya_own_own ON style_examples FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS onboarding_own_own ON onboarding;
CREATE POLICY onboarding_own_own ON onboarding FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS approval_data_own_own ON data_consents;
CREATE POLICY approval_data_own_own ON data_consents FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS used_email_links_deny_app ON used_email_links;
CREATE POLICY used_email_links_deny_app ON used_email_links FOR ALL
    USING (false) WITH CHECK (false);

-- migrate:down
DROP TABLE IF EXISTS used_email_links, data_consents, onboarding, style_examples, summaries, slice_grants, procedures, episodic, profile_facts CASCADE;
