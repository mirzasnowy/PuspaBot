-- Graphs: products, price series, shared graphs (E-08 migration 0004_graphs.sql).
--
-- Creates the price-comparison graph tables (canonical_products, listing_links, price_series, watchlist, read_budgets, evidence_cache, source_readings, procedure_versions, procedure_uses, canary_runs, false_reports, monthly_credits, token_metrics, workflow_library, block_requests, tos_checks) and the shared-graph tables (shared_seller_flags, shared_price_observations, shared_graph_private_records) with RLS.
--
-- Source: infra/schema_graphs.sql (already contains the shared tables).
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
CREATE TABLE IF NOT EXISTS canonical_products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand TEXT NOT NULL,
    series TEXT NOT NULL DEFAULT '',
    model_number TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN (
        'phone', 'laptop', 'tablet', 'smartwatch', 'earphones/TWS', 'TV',
        'game console', 'camera', 'branded home appliances')),
    variant JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (brand, model_number)
);
CREATE INDEX IF NOT EXISTS idx_product_number_model ON canonical_products(model_number);
CREATE INDEX IF NOT EXISTS idx_product_category ON canonical_products(category);

CREATE TABLE IF NOT EXISTS listing_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES canonical_products(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    marketplace TEXT NOT NULL CHECK (marketplace IN ('Tokopedia', 'Shopee', 'Blibli')),
    listing_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'automatic'
        CHECK (status IN ('automatic', 'user_confirmed', 'user_rejected')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (marketplace, listing_url)
);
CREATE INDEX IF NOT EXISTS idx_tautan_product ON listing_links(product_id);

CREATE TABLE IF NOT EXISTS price_series (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tautan_id UUID NOT NULL REFERENCES listing_links(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    price NUMERIC NOT NULL,
    shipping NUMERIC NOT NULL DEFAULT 0,
    store_rating NUMERIC,
    stock TEXT NOT NULL DEFAULT 'unknown',
    evidence_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_tautan_time ON price_series(tautan_id, created_at DESC);

CREATE TABLE IF NOT EXISTS watchlist (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id UUID NOT NULL REFERENCES canonical_products(id) ON DELETE CASCADE,
    price_threshold NUMERIC,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, product_id)
);

CREATE TABLE IF NOT EXISTS read_budgets (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, date)
);

CREATE TABLE IF NOT EXISTS evidence_cache (
    key TEXT PRIMARY KEY,
    sumber TEXT NOT NULL,
    value JSONB NOT NULL,
    evidence_id TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    hit INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS source_readings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sumber TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    vonis TEXT NOT NULL CHECK (vonis IN ('Verified', 'Uncertain', 'Cannot be determined')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pembacaan_sumber ON source_readings(sumber, category, created_at DESC);

CREATE TABLE IF NOT EXISTS procedure_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prosedur_id UUID NOT NULL REFERENCES procedures(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    versi INTEGER NOT NULL,
    step JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (prosedur_id, versi)
);

CREATE TABLE IF NOT EXISTS procedure_uses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prosedur_id UUID NOT NULL REFERENCES procedures(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    used_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pemakaian_prosedur ON procedure_uses(prosedur_id, used_at DESC);

CREATE TABLE IF NOT EXISTS canary_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prosedur_id UUID NOT NULL REFERENCES procedures(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL,
    success BOOLEAN NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    jalan_pada TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS false_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_id TEXT NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason TEXT NOT NULL DEFAULT '',
    reread_requested BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_laporan_evidence ON false_reports(evidence_id);

CREATE TABLE IF NOT EXISTS monthly_credits (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    month TEXT NOT NULL,
    sisa INTEGER NOT NULL DEFAULT 15,
    PRIMARY KEY (user_id, month)
);

CREATE TABLE IF NOT EXISTS token_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id TEXT NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    token INTEGER NOT NULL DEFAULT 0,
    dicatat TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_metrik_token_jenis ON token_metrics(type, dicatat DESC);

CREATE TABLE IF NOT EXISTS workflow_library (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    block JSONB NOT NULL DEFAULT '[]'::jsonb,
    successful_runs INTEGER NOT NULL DEFAULT 0,
    active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS block_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    block TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tos_checks (
    sumber TEXT PRIMARY KEY,
    result TEXT NOT NULL,
    pasal TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    checked_on DATE NOT NULL DEFAULT CURRENT_DATE,
    keputusan TEXT NOT NULL DEFAULT 'awaiting_nero_decision'
);

CREATE TABLE IF NOT EXISTS shared_seller_flags (
    seller_key TEXT NOT NULL,
    reporter_hash TEXT NOT NULL,
    flag_reason TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (seller_key, reporter_hash)
);
CREATE TABLE IF NOT EXISTS shared_price_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_key TEXT NOT NULL,
    source TEXT NOT NULL,
    price NUMERIC NOT NULL,
    observer_hash TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (product_key, source, observer_hash)
);
CREATE TABLE IF NOT EXISTS shared_graph_private_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_private_records_user
    ON shared_graph_private_records(user_id, kind, created_at DESC);

ALTER TABLE canonical_products ENABLE ROW LEVEL SECURITY;
ALTER TABLE listing_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_series ENABLE ROW LEVEL SECURITY;
ALTER TABLE watchlist ENABLE ROW LEVEL SECURITY;
ALTER TABLE read_budgets ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE source_readings ENABLE ROW LEVEL SECURITY;
ALTER TABLE procedure_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE procedure_uses ENABLE ROW LEVEL SECURITY;
ALTER TABLE canary_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE false_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE monthly_credits ENABLE ROW LEVEL SECURITY;
ALTER TABLE token_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_library ENABLE ROW LEVEL SECURITY;
ALTER TABLE block_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE shared_graph_private_records ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['listing_links','price_series','watchlist','read_budgets','source_readings','procedure_versions','procedure_uses','canary_runs','false_reports','monthly_credits','token_metrics','workflow_library','block_requests','shared_graph_private_records'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', 'isolasi_' || t, t);
        EXECUTE format(
            'CREATE POLICY %I ON %I USING (current_setting(%L, true) = user_id::text) WITH CHECK (current_setting(%L, true) = user_id::text)',
            'isolasi_' || t, t, 'app.current_user_id', 'app.current_user_id');
    END LOOP;
END $$;

DROP POLICY IF EXISTS read_shared_canonical_products ON canonical_products;
CREATE POLICY read_shared_canonical_products ON canonical_products
    FOR SELECT USING (current_setting('app.current_user_id', true) <> '');
DROP POLICY IF EXISTS read_shared_evidence_cache ON evidence_cache;
CREATE POLICY read_shared_evidence_cache ON evidence_cache
    FOR SELECT USING (current_setting('app.current_user_id', true) <> '');

-- migrate:down
DROP TABLE IF EXISTS tos_checks, block_requests, workflow_library, token_metrics, monthly_credits, false_reports, canary_runs, procedure_uses, procedure_versions, source_readings, evidence_cache, read_budgets, watchlist, price_series, listing_links, canonical_products, shared_graph_private_records, shared_price_observations, shared_seller_flags CASCADE;
