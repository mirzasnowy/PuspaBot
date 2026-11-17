-- Skema Fase 4: alur kustom (§2.12) + modul kustom (§2.13) revisi 7.
-- Tabel fase 0/1/2/3 tidak disentuh. RLS mengikuti pola fase 0/1/2/3:
-- kebijakan `isolasi_<tabel>` memakai current_setting('app.current_user_id').

CREATE TABLE IF NOT EXISTS alur (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    agen_id TEXT NOT NULL,
    nama TEXT NOT NULL,
    pemicu JSONB NOT NULL DEFAULT '{}'::jsonb,
    langkah JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'draf',
    versi_aktif INTEGER NOT NULL DEFAULT 1,
    gagal_berturut INTEGER NOT NULL DEFAULT 0,
    jalan_sukses INTEGER NOT NULL DEFAULT 0,
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now(),
    diubah TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT alur_status_cek CHECK (status IN ('draf', 'aktif', 'dijeda', 'dihapus'))
);

CREATE TABLE IF NOT EXISTS alur_versi (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alur_id UUID NOT NULL REFERENCES alur(id) ON DELETE CASCADE,
    versi INTEGER NOT NULL,
    pemicu JSONB NOT NULL DEFAULT '{}'::jsonb,
    langkah JSONB NOT NULL DEFAULT '[]'::jsonb,
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (alur_id, versi)
);

CREATE TABLE IF NOT EXISTS jalan_alur (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alur_id UUID NOT NULL REFERENCES alur(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    tugas_id TEXT NOT NULL,
    pemicu_jenis TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'selesai',
    biaya_rp INTEGER NOT NULL DEFAULT 0,
    langkah_hasil JSONB NOT NULL DEFAULT '[]'::jsonb,
    alasan TEXT NOT NULL DEFAULT '',
    dimulai TIMESTAMPTZ NOT NULL DEFAULT now(),
    selesai TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS persetujuan_alur (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    jalan_id UUID NOT NULL REFERENCES jalan_alur(id) ON DELETE CASCADE,
    alur_id UUID NOT NULL REFERENCES alur(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    aksi JSONB NOT NULL,
    isi_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'menunggu',
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now(),
    kedaluwarsa TIMESTAMPTZ NOT NULL,
    CONSTRAINT persetujuan_alur_status_cek
        CHECK (status IN ('menunggu', 'diberikan', 'ditolak', 'kedaluwarsa', 'dibatalkan'))
);

CREATE TABLE IF NOT EXISTS modul (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    nama TEXT NOT NULL,
    manifes JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'draf',
    versi_aktif INTEGER NOT NULL DEFAULT 1,
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now(),
    diubah TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT modul_status_cek CHECK (status IN ('draf', 'aktif', 'dijeda', 'dihapus'))
);

CREATE TABLE IF NOT EXISTS modul_versi (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    modul_id UUID NOT NULL REFERENCES modul(id) ON DELETE CASCADE,
    versi INTEGER NOT NULL,
    manifes JSONB NOT NULL,
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (modul_id, versi)
);

CREATE TABLE IF NOT EXISTS tabel_modul (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    modul_id UUID NOT NULL REFERENCES modul(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    nama TEXT NOT NULL,
    kolom JSONB NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE (modul_id, nama)
);

CREATE TABLE IF NOT EXISTS baris_modul (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tabel_id UUID NOT NULL REFERENCES tabel_modul(id) ON DELETE CASCADE,
    modul_id UUID NOT NULL REFERENCES modul(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    dibuat TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_alur_user ON alur(user_id, status);
CREATE INDEX IF NOT EXISTS idx_jalan_alur_alur ON jalan_alur(alur_id, dimulai DESC);
CREATE INDEX IF NOT EXISTS idx_persetujuan_alur_jalan ON persetujuan_alur(jalan_id, status);
CREATE INDEX IF NOT EXISTS idx_modul_user ON modul(user_id, status);
CREATE INDEX IF NOT EXISTS idx_baris_modul_tabel ON baris_modul(tabel_id, dibuat DESC);

ALTER TABLE alur ENABLE ROW LEVEL SECURITY;
ALTER TABLE jalan_alur ENABLE ROW LEVEL SECURITY;
ALTER TABLE persetujuan_alur ENABLE ROW LEVEL SECURITY;
ALTER TABLE modul ENABLE ROW LEVEL SECURITY;
ALTER TABLE tabel_modul ENABLE ROW LEVEL SECURITY;
ALTER TABLE baris_modul ENABLE ROW LEVEL SECURITY;
-- alur_versi / modul_versi TIDAK memakai RLS (tidak punya user_id
-- sendiri): isolasinya lewat pemilik alur/modul, dicek kode aplikasi
-- di setiap baca/tulis versi (lihat puspa/workflow/runner.py dan
-- puspa/modules/manifest.py).

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['alur', 'jalan_alur', 'persetujuan_alur',
                             'modul', 'tabel_modul', 'baris_modul'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', 'isolasi_' || t, t);
        EXECUTE format(
            'CREATE POLICY %I ON %I USING (current_setting(%L, true) = user_id::text) WITH CHECK (current_setting(%L, true) = user_id::text)',
            'isolasi_' || t, t, 'app.current_user_id', 'app.current_user_id');
    END LOOP;
END $$;
