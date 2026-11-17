-- Skema memori Fase 1 Puspa (definisi 2.7, 2.16, 2.17).
-- Idempotent: jalankan ulang aman (CREATE TABLE IF NOT EXISTS +
-- DROP POLICY IF EXISTS). Nilai domain (asal, status) berbahasa
-- Indonesia sesuai revisi 7; kolom SQL mengikuti konvensi yang sama
-- dengan skema Fase 0 (campuran yang sudah ada di sana).
--
-- Tabel:
--   fakta_profil    — Profil (lapis 1): fakta + Usulan + riwayat versi.
--   episodik        — Episodik (lapis 2): riwayat chat/tugas + vektor.
--   prosedur        — Prosedural (lapis 3): prosedur percobaan dari
--                     tugas yang berhasil (Graf Alur Layanan penuh
--                     dibangun Fase 3; di sini jalur tulis + simpan).
--   pemberian_irisan — audit pemberian irisan memori ke sub-agen.
--   ringkasan       — hasil konsolidasi saat sepi.
--   contoh_gaya     — contoh pesan user untuk persona (maks 20).
--   onboarding      — status onboarding per pengguna.
--   persetujuan_data — persetujuan data pribadi per pengguna.
--   tautan_email_digunakan — token tautan email yang sudah dipakai.
--
-- RLS: pola yang sama dengan puspa/accounts/schema.sql — policy FOR ALL
-- membandingkan user_id dengan app.current_user_id; penegakan nyata
-- hanya untuk peran puspa_app (pemilik tabel melewati RLS, sifat
-- Postgres). Semua tabel punya kolom user_id.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS fakta_profil (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL,
    agen_id        UUID,
    kunci          TEXT NOT NULL,
    nilai          TEXT NOT NULL,
    asal           TEXT NOT NULL CHECK (asal IN ('diucapkan_user', 'dikonfirmasi_user', 'disimpulkan')),
    status         TEXT NOT NULL DEFAULT 'berlaku'
                   CHECK (status IN ('usulan', 'berlaku', 'ditolak', 'diganti')),
    berlaku_dari   TIMESTAMPTZ NOT NULL DEFAULT now(),
    berlaku_sampai TIMESTAMPTZ,
    kejadian_id    UUID,
    dibuat         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fakta_user_kunci ON fakta_profil(user_id, kunci, status);

CREATE TABLE IF NOT EXISTS episodik (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL,
    agen_id     UUID,
    tugas_id    UUID,
    jenis       TEXT NOT NULL,
    isi         TEXT NOT NULL,
    embedding   vector(384),
    kejadian_id UUID,
    dibuat      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_episodik_user ON episodik(user_id, dibuat DESC);
CREATE INDEX IF NOT EXISTS idx_episodik_embedding
    ON episodik USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS prosedur (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL,
    layanan           TEXT NOT NULL,
    versi             INTEGER NOT NULL DEFAULT 1,
    langkah           JSONB NOT NULL DEFAULT '[]'::jsonb,
    skema_keluaran    JSONB NOT NULL DEFAULT '{}'::jsonb,
    pemeriksaan_wajib JSONB NOT NULL DEFAULT '[]'::jsonb,
    status            TEXT NOT NULL DEFAULT 'percobaan'
                      CHECK (status IN ('percobaan', 'terverifikasi', 'rusak')),
    jalan_sukses      INTEGER NOT NULL DEFAULT 0,
    jalan_gagal       INTEGER NOT NULL DEFAULT 0,
    jalan_gagal_berturut INTEGER NOT NULL DEFAULT 0,
    agen_ids          JSONB NOT NULL DEFAULT '[]'::jsonb,
    kejadian_asal_id  UUID,
    dibuat            TIMESTAMPTZ NOT NULL DEFAULT now(),
    diubah            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_prosedur_user_layanan ON prosedur(user_id, layanan, status);

CREATE TABLE IF NOT EXISTS pemberian_irisan (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL,
    agen_id      UUID,
    sub_agen_id  UUID,
    query        TEXT NOT NULL,
    kolom        TEXT[] NOT NULL DEFAULT '{}',
    token_ref    TEXT NOT NULL,
    kejadian_id  UUID,
    dibuat       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_irisan_user ON pemberian_irisan(user_id, dibuat DESC);

CREATE TABLE IF NOT EXISTS ringkasan (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL,
    agen_id       UUID,
    periode_awal  TIMESTAMPTZ NOT NULL,
    periode_akhir TIMESTAMPTZ NOT NULL,
    ringkasan     TEXT NOT NULL,
    kejadian_ids  UUID[] NOT NULL DEFAULT '{}',
    dibuat        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ringkasan_user ON ringkasan(user_id, dibuat DESC);

CREATE TABLE IF NOT EXISTS contoh_gaya (
    id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    teks    TEXT NOT NULL,
    dibuat  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_contoh_gaya_user ON contoh_gaya(user_id, dibuat ASC);

CREATE TABLE IF NOT EXISTS onboarding (
    user_id               UUID PRIMARY KEY,
    langkah_saat_ini      INTEGER NOT NULL DEFAULT 0,
    nama                  TEXT,
    gaya                  TEXT,
    notif_uji             TEXT,
    konektor              TEXT NOT NULL DEFAULT 'nanti',
    tugas_pertama         TEXT,
    hasil_pertama_terbukti BOOLEAN,
    dimulai               TIMESTAMPTZ NOT NULL DEFAULT now(),
    selesai               TIMESTAMPTZ,
    durasi_detik          NUMERIC
);

CREATE TABLE IF NOT EXISTS persetujuan_data (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL,
    versi_teks    TEXT NOT NULL,
    disetujui_pada TIMESTAMPTZ NOT NULL DEFAULT now(),
    kejadian_id   UUID
);
CREATE INDEX IF NOT EXISTS idx_persetujuan_user ON persetujuan_data(user_id, disetujui_pada DESC);

CREATE TABLE IF NOT EXISTS tautan_email_digunakan (
    token_hash  TEXT PRIMARY KEY,
    dipakai_pada TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Izin peran aplikasi (pola Fase 0): puspa_app boleh baca/tulis.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON fakta_profil TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON episodik TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON prosedur TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON pemberian_irisan TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ringkasan TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON contoh_gaya TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON onboarding TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON persetujuan_data TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON tautan_email_digunakan TO puspa_app;
    END IF;
END $$;

-- Row Level Security (pola puspa/accounts/schema.sql).
ALTER TABLE fakta_profil ENABLE ROW LEVEL SECURITY;
ALTER TABLE episodik ENABLE ROW LEVEL SECURITY;
ALTER TABLE prosedur ENABLE ROW LEVEL SECURITY;
ALTER TABLE pemberian_irisan ENABLE ROW LEVEL SECURITY;
ALTER TABLE ringkasan ENABLE ROW LEVEL SECURITY;
ALTER TABLE contoh_gaya ENABLE ROW LEVEL SECURITY;
ALTER TABLE onboarding ENABLE ROW LEVEL SECURITY;
ALTER TABLE persetujuan_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE tautan_email_digunakan ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS fakta_profil_milik_sendiri ON fakta_profil;
CREATE POLICY fakta_profil_milik_sendiri ON fakta_profil FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS episodik_milik_sendiri ON episodik;
CREATE POLICY episodik_milik_sendiri ON episodik FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS prosedur_milik_sendiri ON prosedur;
CREATE POLICY prosedur_milik_sendiri ON prosedur FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS pemberian_irisan_milik_sendiri ON pemberian_irisan;
CREATE POLICY pemberian_irisan_milik_sendiri ON pemberian_irisan FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS ringkasan_milik_sendiri ON ringkasan;
CREATE POLICY ringkasan_milik_sendiri ON ringkasan FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS contoh_gaya_milik_sendiri ON contoh_gaya;
CREATE POLICY contoh_gaya_milik_sendiri ON contoh_gaya FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS onboarding_milik_sendiri ON onboarding;
CREATE POLICY onboarding_milik_sendiri ON onboarding FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS persetujuan_data_milik_sendiri ON persetujuan_data;
CREATE POLICY persetujuan_data_milik_sendiri ON persetujuan_data FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

-- tautan_email_digunakan tidak punya user_id: hanya server yang
-- menulis/membaca (cek sekali pakai). Policy menolak semua akses
-- peran app; penegakan sekali-pakai ada di kode (email_link.py).
DROP POLICY IF EXISTS tautan_email_tolak_app ON tautan_email_digunakan;
CREATE POLICY tautan_email_tolak_app ON tautan_email_digunakan FOR ALL
    USING (false) WITH CHECK (false);
