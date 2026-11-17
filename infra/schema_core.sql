-- Setup database Puspa (idempotent, bisa dijalankan ulang kapan saja).
--
-- Dijalankan sebagai SUPERUSER Postgres, dari direktori build/fase-0.
-- Varian utama (peer auth sebagai root — role root sudah superuser):
--
--     psql "dbname=postgres user=root host=/var/run/postgresql" -f infra/setup_db.sql
--
-- Varian lain (sebagai user OS postgres; perlu file bisa dibaca user
-- postgres — salin dulu ke /tmp kalau permission workspace menolak):
--
--     cp infra/setup_db.sql /tmp/setup_db.sql
--     sudo -u postgres psql -f /tmp/setup_db.sql
--
-- Yang dilakukan, berurutan:
--   1. Buat role `root` (LOGIN SUPERUSER) kalau belum ada — peran yang
--      dipakai tes/dev lewat peer auth (STATUS.md keputusan 14).
--   2. Buat role aplikasi `puspa_app` (NOLOGIN) kalau belum ada — peran
--      yang dikenai RLS; server aplikasi hanya memakai peran ini
--      (NOLOGIN = tidak bisa login langsung; koneksi jalan sebagai
--      pemilik lalu SET ROLE puspa_app — lihat tests/test_rls.py.
--      Memberi LOGIN + password ke puspa_app adalah keputusan fase 2).
--   3. Buat database `puspa_test` (pemilik root) kalau belum ada.
--   4. Terapkan skema ledger (tabel `kejadian`) + accounts (tabel
--      pengguna/sesi/vault + policy RLS) ke `puspa_test`.
--
-- TANPA PASSWORD di mana pun di file ini: auth memakai peer auth
-- (socket lokal, user OS = role Postgres). Jangan tambahkan password
-- ke file ini.
--
-- Dipakai juga untuk PEMULIHAN setelah VM restart: Postgres di VM ini
-- tidak tahan restart — role + database bisa hilang, bahkan paket apt
-- Postgresnya pun bisa hilang total (terjadi 17 Nov 2026: tidak ada
-- jejak di dpkg, cluster ikut hilang). Pemulihan penuh, berurutan:
--   a. Instal ulang paket bila `pg_lsclusters` tidak menemukan apa pun:
--      `apt-get update` (lama, ~10 menit lewat proxy) lalu
--      `DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql-16`.
--   b. Start cluster: `pg_ctlcluster 16 main start`.
--   c. Jalankan lagi setup ini (membuat ulang role + DB + skema).
-- Kalau cluster sistem tidak bisa dipakai (mis. apt/proxy bermasalah),
-- CADANGAN: Postgres sementara via pgserver (pip, cluster di
-- /tmp/pgdata-puspa-test, socket di direktori yang sama) — jalankan
-- skema yang sama ke sana, lalu tes dengan
-- PUSPA_TEST_DSN="dbname=puspa_test user=root host=/tmp/pgdata-puspa-test".
-- Perintah start pgserver yang pernah dipakai tersimpan di
-- /tmp/pgserver-start.py (dibuat worker D, 17 Nov 2026); cluster
-- pgserver TIDAK tahan restart — setelah restart, ulangi langkah
-- a-c di atas (sistem) atau nyalakan ulang pgserver.
-- (\ir = include relatif terhadap lokasi file ini, jadi harus dijalankan
--  dengan -f, bukan lewat stdin.)

-- 1. Role root (superuser, peer auth) ---------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'root') THEN
        CREATE ROLE root LOGIN SUPERUSER;
    END IF;
END
$$;

-- 2. Role aplikasi (dikenai RLS; NOLOGIN, lihat catatan di atas) ------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        CREATE ROLE puspa_app NOLOGIN;
    END IF;
END
$$;

-- 3. Database tes ------------------------------------------------------------
-- CREATE DATABASE tidak bisa di dalam blok DO / transaksi, jadi pakai
-- trik psql \gexec: jalankan CREATE hanya kalau DB belum ada.
SELECT 'CREATE DATABASE puspa_test OWNER root'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'puspa_test')
\gexec

-- 4. Skema -------------------------------------------------------------------
-- Urutan penting: peran puspa_app harus sudah ada SEBELUM skema
-- diterapkan, karena blok DO di skema memberi GRANT ke peran itu
-- hanya kalau peran sudah ada.
\c puspa_test
\ir ../puspa/ledger/schema.sql
\ir ../puspa/accounts/schema.sql

-- Verifikasi singkat (gagal kalau ada yang tidak terbentuk).
SELECT 'pengguna RLS: ' || (relrowsecurity::text) FROM pg_class WHERE relname = 'users';
SELECT 'sesi RLS: ' || (relrowsecurity::text) FROM pg_class WHERE relname = 'sessions';
SELECT 'vault RLS: ' || (relrowsecurity::text) FROM pg_class WHERE relname = 'vault';
