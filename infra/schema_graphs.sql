-- Skema Postgres Fase 3 (idempotent; jalankan lewat puspa.graphs.create_schema).
-- Prasyarat: skema Fase 0 (pengguna, agen, kejadian), Fase 1 (prosedur),
-- Fase 2 (bukti, pemicu) sudah ada.
--
-- Nilai domain Indonesia (revisi 7): status tautan
-- otomatis / dikonfirmasi_user / ditolak_user; status prosedur
-- percobaan / terverifikasi / rusak (dipakai ulang dari tabel `prosedur`
-- Fase 1); vonis Terverifikasi / Belum pasti / Tidak bisa dipastikan.

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

-- Deret waktu harga: satu baris per pembacaan. TIDAK ADA angka tanpa
-- bukti_id (ditegakkan kode: catat_harga menolak bukti_id kosong).
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

-- Anggaran baca sampler: maks 20 baca/hari/pengguna (§2.14, §3.8).
CREATE TABLE IF NOT EXISTS read_budgets (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tanggal DATE NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, tanggal)
);

-- Cache bukti bersama: satu baris per (sumber, kunci). TTL 30 menit.
CREATE TABLE IF NOT EXISTS evidence_cache (
    key TEXT PRIMARY KEY,
    sumber TEXT NOT NULL,
    value JSONB NOT NULL,
    evidence_id TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    hit INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Pembacaan per sumber: bahan Skor Keandalan (§3.4) per
-- (marketplace x kategori) dan per prosedur.
CREATE TABLE IF NOT EXISTS source_readings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sumber TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    vonis TEXT NOT NULL CHECK (vonis IN ('Verified', 'Uncertain', 'Cannot be determined')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pembacaan_sumber ON source_readings(sumber, category, created_at DESC);

-- Versi prosedur (rollback, §3.3): snapshot langkah tiap versi baru.
CREATE TABLE IF NOT EXISTS procedure_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prosedur_id UUID NOT NULL REFERENCES procedures(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    versi INTEGER NOT NULL,
    step JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (prosedur_id, versi)
);

-- Pemakaian ulang prosedur lintas agen (§3.3, §3.6).
CREATE TABLE IF NOT EXISTS procedure_uses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prosedur_id UUID NOT NULL REFERENCES procedures(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    used_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pemakaian_prosedur ON procedure_uses(prosedur_id, used_at DESC);

-- Jalan canary harian (§3.3).
CREATE TABLE IF NOT EXISTS canary_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prosedur_id UUID NOT NULL REFERENCES procedures(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL,
    success BOOLEAN NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    jalan_pada TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Laporan salah dari panel Bukti (§3.8); kejadian `laporan_salah` di
-- Buku Kerja tetap dicatat lewat Ledger (jenis sudah ada, §2.3).
CREATE TABLE IF NOT EXISTS false_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_id TEXT NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason TEXT NOT NULL DEFAULT '',
    reread_requested BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_laporan_evidence ON false_reports(evidence_id);

-- Kredit bulanan: 15 kredit gratis/bulan (§4.1). Cek detail = 1 kredit.
CREATE TABLE IF NOT EXISTS monthly_credits (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    month TEXT NOT NULL,
    sisa INTEGER NOT NULL DEFAULT 15,
    PRIMARY KEY (user_id, month)
);

-- Token per tugas (bahan metrik roda gila §3.6: token/perbandingan
-- minggu terakhir vs minggu pertama).
CREATE TABLE IF NOT EXISTS token_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id TEXT NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    token INTEGER NOT NULL DEFAULT 0,
    dicatat TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_metrik_token_jenis ON token_metrics(type, dicatat DESC);

-- Pustaka alur + Permintaan Blok (§3.8; fondasi Fase 4, metrik alur
-- aktif TIDAK diklaim di Fase 3).
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

-- Hasil cek syarat layanan per sumber (keputusan 15, §10).
CREATE TABLE IF NOT EXISTS tos_checks (
    sumber TEXT PRIMARY KEY,
    result TEXT NOT NULL,
    pasal TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    checked_on DATE NOT NULL DEFAULT CURRENT_DATE,
    keputusan TEXT NOT NULL DEFAULT 'awaiting_nero_decision'
);

-- RLS: semua tabel ber-user_id memakai kebijakan yang sama
-- (app.current_user_id). Penegakan nyata hanya untuk koneksi sebagai
-- peran puspa_app (pola Fase 0/1/2). Tabel tanpa user_id
-- (cek_syarat_layanan) adalah data referensi bersama, tanpa RLS.
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

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['listing_links','price_series','watchlist','read_budgets','source_readings','procedure_versions','procedure_uses','canary_runs','false_reports','monthly_credits','token_metrics','workflow_library','block_requests'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', 'isolasi_' || t, t);
        EXECUTE format(
            'CREATE POLICY %I ON %I USING (current_setting(%L, true) = user_id::text) WITH CHECK (current_setting(%L, true) = user_id::text)',
            'isolasi_' || t, t, 'app.current_user_id', 'app.current_user_id');
    END LOOP;
END $$;

-- produk_kanonik dan cache_bukti adalah data bersama tanpa identitas
-- (graf bersama, §2.17): semua pengguna yang sudah login boleh baca;
-- tulis tetap lewat kode aplikasi. Kebijakan baca-terbuka:
DROP POLICY IF EXISTS read_shared_canonical_products ON canonical_products;
CREATE POLICY read_shared_canonical_products ON canonical_products
    FOR SELECT USING (current_setting('app.current_user_id', true) <> '');
DROP POLICY IF EXISTS read_shared_evidence_cache ON evidence_cache;
CREATE POLICY read_shared_evidence_cache ON evidence_cache
    FOR SELECT USING (current_setting('app.current_user_id', true) <> '');
