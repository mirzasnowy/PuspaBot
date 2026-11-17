-- Skema Postgres Fase 2 (idempotent; jalankan lewat puspa.runtime.create_schema).
-- Prasyarat: skema Fase 0 (pengguna, agen, kejadian) sudah ada.

CREATE TABLE IF NOT EXISTS agen_meta (
    agen_id TEXT PRIMARY KEY REFERENCES agen(agen_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    nama TEXT NOT NULL,
    peran TEXT NOT NULL DEFAULT '',
    tujuan TEXT NOT NULL DEFAULT '',
    wajib BOOLEAN NOT NULL DEFAULT FALSE,
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agen_meta_user_id ON agen_meta(user_id);

CREATE TABLE IF NOT EXISTS tugas (
    id TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    agen_id TEXT NOT NULL REFERENCES agen(agen_id) ON DELETE CASCADE,
    tujuan TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'diterima',
    biaya_rp INTEGER NOT NULL DEFAULT 0,
    langkah INTEGER NOT NULL DEFAULT 0,
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now(),
    selesai TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_tugas_user_id ON tugas(user_id);

CREATE TABLE IF NOT EXISTS handoff (
    id TEXT PRIMARY KEY,
    tugas_id TEXT NOT NULL REFERENCES tugas(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    dari_id TEXT NOT NULL,
    ke_id TEXT NOT NULL,
    tujuan TEXT NOT NULL,
    masukan JSONB NOT NULL DEFAULT '{}',
    keluaran_diharapkan JSONB NOT NULL DEFAULT '{}',
    batas_waktu_detik INTEGER NOT NULL DEFAULT 45,
    anggaran JSONB NOT NULL DEFAULT '{}',
    kelas_izin TEXT NOT NULL,
    definisi_selesai TEXT NOT NULL,
    kunci_idempotensi TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'diajukan',
    percobaan INTEGER NOT NULL DEFAULT 1,
    detak_terakhir TIMESTAMPTZ,
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now(),
    diserahkan TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_handoff_tugas_id ON handoff(tugas_id);

CREATE TABLE IF NOT EXISTS bukti (
    bukti_id TEXT PRIMARY KEY,
    tugas_id TEXT NOT NULL,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    klaim TEXT NOT NULL,
    nilai NUMERIC NOT NULL,
    satuan TEXT NOT NULL DEFAULT 'Rp',
    sumber_url TEXT NOT NULL,
    waktu_ambil TIMESTAMPTZ NOT NULL DEFAULT now(),
    potongan_teks TEXT NOT NULL DEFAULT '',
    hash_halaman TEXT NOT NULL DEFAULT '',
    tangkapan_layar TEXT,
    id_pembaca TEXT NOT NULL DEFAULT '',
    id_pemeriksa TEXT,
    vonis TEXT NOT NULL DEFAULT 'Belum pasti'
);
CREATE INDEX IF NOT EXISTS idx_bukti_tugas_id ON bukti(tugas_id);
CREATE INDEX IF NOT EXISTS idx_bukti_user_id ON bukti(user_id);

CREATE TABLE IF NOT EXISTS pemicu (
    id TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    jenis TEXT NOT NULL,
    jadwal TEXT NOT NULL DEFAULT '',
    alur TEXT NOT NULL DEFAULT '',
    pemicu_berikutnya TIMESTAMPTZ NOT NULL,
    aktif BOOLEAN NOT NULL DEFAULT TRUE,
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pemicu_berikutnya ON pemicu(pemicu_berikutnya) WHERE aktif;

CREATE TABLE IF NOT EXISTS pemicu_jalan (
    kunci_idempotensi TEXT PRIMARY KEY,
    pemicu_id TEXT NOT NULL REFERENCES pemicu(id) ON DELETE CASCADE,
    tugas_id TEXT NOT NULL,
    terlewat BOOLEAN NOT NULL DEFAULT FALSE,
    jalan_pada TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS konektor_agen (
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    agen_id TEXT NOT NULL REFERENCES agen(agen_id) ON DELETE CASCADE,
    konektor TEXT NOT NULL,
    scope TEXT[] NOT NULL DEFAULT '{}',
    terhubung BOOLEAN NOT NULL DEFAULT FALSE,
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, agen_id, konektor)
);

CREATE TABLE IF NOT EXISTS kaset (
    id TEXT PRIMARY KEY,
    tugas_id TEXT NOT NULL,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    nama TEXT NOT NULL,
    berkas TEXT NOT NULL,
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kaset_tugas_id ON kaset(tugas_id);

CREATE TABLE IF NOT EXISTS langganan_push (
    id TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    endpoint TEXT NOT NULL,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_push_user_id ON langganan_push(user_id);

CREATE TABLE IF NOT EXISTS detak_sistem (
    komponen TEXT PRIMARY KEY,
    waktu TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Penolakan hapus Puspa di level DATABASE (bukan hanya API/UI):
-- baris agen_meta dengan wajib=TRUE tidak bisa di-DELETE.
CREATE OR REPLACE FUNCTION tolak_hapus_puspa() RETURNS trigger AS $$
BEGIN
    IF OLD.wajib THEN
        RAISE EXCEPTION 'Puspa tidak bisa dihapus';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_tolak_hapus_puspa ON agen_meta;
CREATE TRIGGER trg_tolak_hapus_puspa BEFORE DELETE ON agen_meta
FOR EACH ROW EXECUTE FUNCTION tolak_hapus_puspa();

-- RLS (pola yang sama seperti Fase 0/1: app.current_user_id).
-- Penegakan nyata hanya untuk koneksi sebagai puspa_app.
ALTER TABLE agen_meta ENABLE ROW LEVEL SECURITY;
ALTER TABLE tugas ENABLE ROW LEVEL SECURITY;
ALTER TABLE handoff ENABLE ROW LEVEL SECURITY;
ALTER TABLE bukti ENABLE ROW LEVEL SECURITY;
ALTER TABLE pemicu ENABLE ROW LEVEL SECURITY;
ALTER TABLE konektor_agen ENABLE ROW LEVEL SECURITY;
ALTER TABLE kaset ENABLE ROW LEVEL SECURITY;
ALTER TABLE langganan_push ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['agen_meta','tugas','handoff','bukti','pemicu','konektor_agen','kaset','langganan_push'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', 'isolasi_' || t, t);
        EXECUTE format(
            'CREATE POLICY %I ON %I USING (current_setting(%L, true) = user_id::text) WITH CHECK (current_setting(%L, true) = user_id::text)',
            'isolasi_' || t, t, 'app.current_user_id', 'app.current_user_id');
    END LOOP;
END $$;
