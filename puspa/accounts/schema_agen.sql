-- Registry agen -> pemilik (definisi 2.3/2.6, jalur serangan ke-8).
-- Pemetaan agen_id -> user_id pemilik agen itu. Tabel `kejadian` (Buku
-- Kerja) tidak punya kolom user_id; isolasi pengguna lewat agen_id, dan
-- tabel inilah yang memegang pemetaannya.
--
-- File ini SENGAJA terpisah dari schema.sql (milik worker RLS): tabel agen
-- dibuat dan dikelola oleh puspa.accounts.access.create_agent_schema().
-- Dibuat SETELAH tabel `pengguna` ada (FK ke pengguna(id)).
-- Hapus pengguna menghapus baris agennya (ON DELETE CASCADE).

CREATE TABLE IF NOT EXISTS agen (
    agen_id TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    dibuat  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agen_user_id ON agen(user_id);
