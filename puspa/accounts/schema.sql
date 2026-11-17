-- Skema akun dasar Puspa (definisi 2.15, CONTRACTS.md).
-- Tabel: pengguna, sesi, vault. Semua query aplikasi berfilter user_id.
--
-- ROW LEVEL SECURITY (RLS) — revisi 7, bagian 2.15:
--   "Semua tabel pengguna memakai kolom user_id dan Row Level Security
--    Postgres. Tiap agen, alur, modul, dan memori terikat ke satu pengguna."
--
-- Cara kerja:
--   - Aplikasi memasang pengguna saat ini per transaksi dengan
--     SET LOCAL app.current_user_id = '<uuid pengguna>' (lihat
--     puspa/accounts/rls.py — JANGAN menulis SQL mentah di kode aplikasi).
--   - Tiap policy membandingkan kolom pemilik baris dengan nilai itu,
--     dibaca lewat coalesce(current_setting('app.current_user_id', true), '').
--     PERINGATAN: current_setting(..., true) mengembalikan NULL (bukan
--     '') kalau variabel belum diset — karena itu SEMUA policy memakai
--     coalesce(..., ''): tanpa coalesce, perbandingan `= ''` bernilai
--     NULL dan INSERT registrasi (yang justru harus lolos saat belum
--     ada sesi) ikut ditolak. Dengan coalesce, tanpa pengguna yang
--     diset semua policy bernilai salah -> fail-closed (tidak ada baris
--     terlihat, tulis ditolak), kecuali INSERT pengguna (registrasi,
--     lihat bawah).
--
-- KEPUTUSAN: RLS diaktifkan TANPA "FORCE ROW LEVEL SECURITY".
--   - Postgres tidak menerapkan RLS ke pemilik tabel dan superuser.
--     Pemilik tabel di sini adalah peran yang menjalankan skema ini
--     (root/postgres di dev/tes); ia tetap bisa melakukan operasi
--     istimewa yang MEMANG tidak bisa lewat RLS: registrasi pengguna
--     baru (belum ada sesi), validate_session (mencari user_id dari
--     token SEBELUM pengguna diketahui — ayam-telur), delete_user
--     (bersih-bersih sesi + vault pengguna yang dihapus), dan TRUNCATE
--     di fixture tes.
--   - Batas penegakan RLS adalah peran aplikasi `puspa_app`: itulah
--     satu-satunya peran yang dipakai server aplikasi saat runtime.
--     Semua tes RLS (tests/test_rls.py) berjalan sebagai puspa_app
--     lewat SET ROLE, bukan sebagai pemilik.
--   - Alternatif yang TIDAK dipilih: FORCE RLS + peran admin BYPASSRLS
--     + fungsi SECURITY DEFINER untuk login/registrasi. Itu lebih ketat
--     tapi butuh pemisahan peran yang belum ada di fase 0; dicatat
--     sebagai pertanyaan fase 2 (lihat laporan worker RLS).
--
-- Policy per tabel:
--   - pengguna: baris terlihat/diubah/dihapus hanya kalau id = pengguna
--     saat ini. INSERT diizinkan kalau pengguna belum diset (registrasi:
--     belum ada sesi) ATAU id baris = pengguna saat ini; pengguna yang
--     sudah login tidak bisa membuat akun pengguna lain lewat peran app.
--   - sesi, vault: semua perintah (SELECT/INSERT/UPDATE/DELETE) hanya
--     untuk baris dengan user_id = pengguna saat ini.
--
-- Tabel kejadian (Buku Kerja) SENGAJA tidak di sini: ia tidak punya
-- kolom user_id (definisi 2.3) — isolasinya lewat agen_id (pemetaan
-- agen -> pemilik). RLS untuk kejadian butuh pemetaan itu di DB;
-- dicatat sebagai pertanyaan fase 2.

CREATE TABLE IF NOT EXISTS pengguna (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    dibuat        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sesi (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL UNIQUE,
    device_label    TEXT,
    dibuat          TIMESTAMPTZ NOT NULL DEFAULT now(),
    terakhir_dilihat TIMESTAMPTZ NOT NULL DEFAULT now(),
    dicabut         BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_sesi_user_id ON sesi(user_id);

CREATE TABLE IF NOT EXISTS vault (
    user_id    UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    ciphertext TEXT NOT NULL,
    diubah     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, name)
);

-- Izin peran aplikasi (pola yang sama dengan puspa/ledger/schema.sql):
-- puspa_app boleh baca/tulis ketiga tabel pengguna; tanpa UPDATE/DELETE
-- di sini tidak mungkin karena sesi/vault memang butuh keduanya
-- (cabut sesi, timpa entri vault, hapus akun). Isolasi antar pengguna
-- ditegakkan oleh policy RLS di bawah, bukan oleh pencabutan izin.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON pengguna TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON sesi TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON vault TO puspa_app;
    END IF;
END $$;

-- Row Level Security -------------------------------------------------------
-- Idempotent: jalankan ulang skema ini aman (DROP POLICY IF EXISTS).

ALTER TABLE pengguna ENABLE ROW LEVEL SECURITY;
ALTER TABLE sesi ENABLE ROW LEVEL SECURITY;
ALTER TABLE vault ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS pengguna_lihat_milik_sendiri ON pengguna;
CREATE POLICY pengguna_lihat_milik_sendiri ON pengguna
    FOR SELECT
    USING (id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS pengguna_daftar_atau_milik_sendiri ON pengguna;
CREATE POLICY pengguna_daftar_atau_milik_sendiri ON pengguna
    FOR INSERT
    WITH CHECK (
        coalesce(current_setting('app.current_user_id', true), '') = ''
        OR id::text = coalesce(current_setting('app.current_user_id', true), '')
    );

DROP POLICY IF EXISTS pengguna_ubah_milik_sendiri ON pengguna;
CREATE POLICY pengguna_ubah_milik_sendiri ON pengguna
    FOR UPDATE
    USING (id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS pengguna_hapus_milik_sendiri ON pengguna;
CREATE POLICY pengguna_hapus_milik_sendiri ON pengguna
    FOR DELETE
    USING (id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS sesi_milik_sendiri ON sesi;
CREATE POLICY sesi_milik_sendiri ON sesi
    FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));

DROP POLICY IF EXISTS vault_milik_sendiri ON vault;
CREATE POLICY vault_milik_sendiri ON vault
    FOR ALL
    USING (user_id::text = coalesce(current_setting('app.current_user_id', true), ''))
    WITH CHECK (user_id::text = coalesce(current_setting('app.current_user_id', true), ''));
