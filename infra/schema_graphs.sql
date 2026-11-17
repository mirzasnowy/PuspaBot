-- Skema Postgres Fase 3 (idempotent; jalankan lewat puspa.graphs.create_schema).
-- Prasyarat: skema Fase 0 (pengguna, agen, kejadian), Fase 1 (prosedur),
-- Fase 2 (bukti, pemicu) sudah ada.
--
-- Nilai domain Indonesia (revisi 7): status tautan
-- otomatis / dikonfirmasi_user / ditolak_user; status prosedur
-- percobaan / terverifikasi / rusak (dipakai ulang dari tabel `prosedur`
-- Fase 1); vonis Terverifikasi / Belum pasti / Tidak bisa dipastikan.

CREATE TABLE IF NOT EXISTS produk_kanonik (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merek TEXT NOT NULL,
    seri TEXT NOT NULL DEFAULT '',
    nomor_model TEXT NOT NULL,
    kategori TEXT NOT NULL CHECK (kategori IN (
        'HP', 'laptop', 'tablet', 'smartwatch', 'earphone/TWS', 'TV',
        'konsol game', 'kamera', 'peralatan rumah tangga bermerek')),
    varian JSONB NOT NULL DEFAULT '{}'::jsonb,
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (merek, nomor_model)
);
CREATE INDEX IF NOT EXISTS idx_produk_nomor_model ON produk_kanonik(nomor_model);
CREATE INDEX IF NOT EXISTS idx_produk_kategori ON produk_kanonik(kategori);

CREATE TABLE IF NOT EXISTS tautan_listing (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    produk_id UUID NOT NULL REFERENCES produk_kanonik(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    marketplace TEXT NOT NULL CHECK (marketplace IN ('Tokopedia', 'Shopee', 'Blibli')),
    listing_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'otomatis'
        CHECK (status IN ('otomatis', 'dikonfirmasi_user', 'ditolak_user')),
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (marketplace, listing_url)
);
CREATE INDEX IF NOT EXISTS idx_tautan_produk ON tautan_listing(produk_id);

-- Deret waktu harga: satu baris per pembacaan. TIDAK ADA angka tanpa
-- bukti_id (ditegakkan kode: catat_harga menolak bukti_id kosong).
CREATE TABLE IF NOT EXISTS harga_seri (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tautan_id UUID NOT NULL REFERENCES tautan_listing(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    waktu TIMESTAMPTZ NOT NULL DEFAULT now(),
    harga NUMERIC NOT NULL,
    ongkir NUMERIC NOT NULL DEFAULT 0,
    rating_toko NUMERIC,
    stok TEXT NOT NULL DEFAULT 'tidak_diketahui',
    bukti_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_harga_tautan_waktu ON harga_seri(tautan_id, waktu DESC);

CREATE TABLE IF NOT EXISTS watchlist (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    produk_id UUID NOT NULL REFERENCES produk_kanonik(id) ON DELETE CASCADE,
    ambang_harga NUMERIC,
    aktif BOOLEAN NOT NULL DEFAULT TRUE,
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, produk_id)
);

-- Anggaran baca sampler: maks 20 baca/hari/pengguna (§2.14, §3.8).
CREATE TABLE IF NOT EXISTS anggaran_baca (
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    tanggal DATE NOT NULL,
    jumlah INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, tanggal)
);

-- Cache bukti bersama: satu baris per (sumber, kunci). TTL 30 menit.
CREATE TABLE IF NOT EXISTS cache_bukti (
    kunci TEXT PRIMARY KEY,
    sumber TEXT NOT NULL,
    nilai JSONB NOT NULL,
    bukti_id TEXT NOT NULL,
    kedaluwarsa TIMESTAMPTZ NOT NULL,
    hit INTEGER NOT NULL DEFAULT 0,
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Pembacaan per sumber: bahan Skor Keandalan (§3.4) per
-- (marketplace x kategori) dan per prosedur.
CREATE TABLE IF NOT EXISTS pembacaan_sumber (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    sumber TEXT NOT NULL,
    kategori TEXT NOT NULL DEFAULT '',
    vonis TEXT NOT NULL CHECK (vonis IN ('Terverifikasi', 'Belum pasti', 'Tidak bisa dipastikan')),
    waktu TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pembacaan_sumber ON pembacaan_sumber(sumber, kategori, waktu DESC);

-- Versi prosedur (rollback, §3.3): snapshot langkah tiap versi baru.
CREATE TABLE IF NOT EXISTS prosedur_versi (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prosedur_id UUID NOT NULL REFERENCES prosedur(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    versi INTEGER NOT NULL,
    langkah JSONB NOT NULL,
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (prosedur_id, versi)
);

-- Pemakaian ulang prosedur lintas agen (§3.3, §3.6).
CREATE TABLE IF NOT EXISTS pemakaian_prosedur (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prosedur_id UUID NOT NULL REFERENCES prosedur(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    agen_id TEXT NOT NULL,
    tugas_id TEXT NOT NULL,
    dipakai_pada TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pemakaian_prosedur ON pemakaian_prosedur(prosedur_id, dipakai_pada DESC);

-- Jalan canary harian (§3.3).
CREATE TABLE IF NOT EXISTS canary_jalan (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prosedur_id UUID NOT NULL REFERENCES prosedur(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    agen_id TEXT NOT NULL,
    sukses BOOLEAN NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    jalan_pada TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Laporan salah dari panel Bukti (§3.8); kejadian `laporan_salah` di
-- Buku Kerja tetap dicatat lewat Ledger (jenis sudah ada, §2.3).
CREATE TABLE IF NOT EXISTS laporan_salah (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bukti_id TEXT NOT NULL,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    alasan TEXT NOT NULL DEFAULT '',
    baca_ulang_diminta BOOLEAN NOT NULL DEFAULT TRUE,
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_laporan_bukti ON laporan_salah(bukti_id);

-- Kredit bulanan: 15 kredit gratis/bulan (§4.1). Cek detail = 1 kredit.
CREATE TABLE IF NOT EXISTS kredit_bulanan (
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    bulan TEXT NOT NULL,
    sisa INTEGER NOT NULL DEFAULT 15,
    PRIMARY KEY (user_id, bulan)
);

-- Token per tugas (bahan metrik roda gila §3.6: token/perbandingan
-- minggu terakhir vs minggu pertama).
CREATE TABLE IF NOT EXISTS metrik_token (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tugas_id TEXT NOT NULL,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    jenis TEXT NOT NULL,
    token INTEGER NOT NULL DEFAULT 0,
    dicatat TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_metrik_token_jenis ON metrik_token(jenis, dicatat DESC);

-- Pustaka alur + Permintaan Blok (§3.8; fondasi Fase 4, metrik alur
-- aktif TIDAK diklaim di Fase 3).
CREATE TABLE IF NOT EXISTS pustaka_alur (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    nama TEXT NOT NULL,
    blok JSONB NOT NULL DEFAULT '[]'::jsonb,
    jalan_sukses INTEGER NOT NULL DEFAULT 0,
    aktif BOOLEAN NOT NULL DEFAULT FALSE,
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS permintaan_blok (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    blok TEXT NOT NULL,
    alasan TEXT NOT NULL DEFAULT '',
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Hasil cek syarat layanan per sumber (keputusan 15, §10).
CREATE TABLE IF NOT EXISTS cek_syarat_layanan (
    sumber TEXT PRIMARY KEY,
    hasil TEXT NOT NULL,
    pasal TEXT NOT NULL DEFAULT '',
    url_sumber TEXT NOT NULL DEFAULT '',
    tanggal_cek DATE NOT NULL DEFAULT CURRENT_DATE,
    keputusan TEXT NOT NULL DEFAULT 'menunggu_keputusan_nero'
);

-- RLS: semua tabel ber-user_id memakai kebijakan yang sama
-- (app.current_user_id). Penegakan nyata hanya untuk koneksi sebagai
-- peran puspa_app (pola Fase 0/1/2). Tabel tanpa user_id
-- (cek_syarat_layanan) adalah data referensi bersama, tanpa RLS.
ALTER TABLE produk_kanonik ENABLE ROW LEVEL SECURITY;
ALTER TABLE tautan_listing ENABLE ROW LEVEL SECURITY;
ALTER TABLE harga_seri ENABLE ROW LEVEL SECURITY;
ALTER TABLE watchlist ENABLE ROW LEVEL SECURITY;
ALTER TABLE anggaran_baca ENABLE ROW LEVEL SECURITY;
ALTER TABLE cache_bukti ENABLE ROW LEVEL SECURITY;
ALTER TABLE pembacaan_sumber ENABLE ROW LEVEL SECURITY;
ALTER TABLE prosedur_versi ENABLE ROW LEVEL SECURITY;
ALTER TABLE pemakaian_prosedur ENABLE ROW LEVEL SECURITY;
ALTER TABLE canary_jalan ENABLE ROW LEVEL SECURITY;
ALTER TABLE laporan_salah ENABLE ROW LEVEL SECURITY;
ALTER TABLE kredit_bulanan ENABLE ROW LEVEL SECURITY;
ALTER TABLE metrik_token ENABLE ROW LEVEL SECURITY;
ALTER TABLE pustaka_alur ENABLE ROW LEVEL SECURITY;
ALTER TABLE permintaan_blok ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['tautan_listing','harga_seri','watchlist','anggaran_baca','pembacaan_sumber','prosedur_versi','pemakaian_prosedur','canary_jalan','laporan_salah','kredit_bulanan','metrik_token','pustaka_alur','permintaan_blok'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', 'isolasi_' || t, t);
        EXECUTE format(
            'CREATE POLICY %I ON %I USING (current_setting(%L, true) = user_id::text) WITH CHECK (current_setting(%L, true) = user_id::text)',
            'isolasi_' || t, t, 'app.current_user_id', 'app.current_user_id');
    END LOOP;
END $$;

-- produk_kanonik dan cache_bukti adalah data bersama tanpa identitas
-- (graf bersama, §2.17): semua pengguna yang sudah login boleh baca;
-- tulis tetap lewat kode aplikasi. Kebijakan baca-terbuka:
DROP POLICY IF EXISTS baca_bersama_produk_kanonik ON produk_kanonik;
CREATE POLICY baca_bersama_produk_kanonik ON produk_kanonik
    FOR SELECT USING (current_setting('app.current_user_id', true) <> '');
DROP POLICY IF EXISTS baca_bersama_cache_bukti ON cache_bukti;
CREATE POLICY baca_bersama_cache_bukti ON cache_bukti
    FOR SELECT USING (current_setting('app.current_user_id', true) <> '');
