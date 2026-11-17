-- Buku Kerja (definisi 2.3): tabel kejadian, append-only.
-- Dijalankan oleh Ledger.create_schema() sebagai peran pemilik DB (root di tes).
-- Peran aplikasi `puspa_app` dibuat sekali oleh superuser Postgres (di luar
-- skema ini, karena peran pemilik DB tidak punya CREATEROLE); blok DO di bawah
-- hanya memberi izin kalau peran itu sudah ada.

CREATE TABLE IF NOT EXISTS kejadian (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    waktu TIMESTAMPTZ NOT NULL DEFAULT now(),
    tugas_id TEXT NOT NULL,
    agen_id TEXT NOT NULL,
    sub_agen_id TEXT,
    jenis TEXT NOT NULL CHECK (jenis IN (
        'tugas_dibuat', 'rencana_dibuat', 'subagen_dibuat',
        'handoff_diajukan', 'handoff_diterima', 'handoff_diserahkan',
        'handoff_ditolak', 'handoff_kedaluwarsa', 'detak',
        'tool_dipanggil', 'tool_selesai', 'bukti_dicatat',
        'pemeriksaan_selesai', 'persetujuan_diminta', 'persetujuan_diberikan',
        'persetujuan_ditolak', 'pesan_user', 'pesan_agen', 'anggaran_habis',
        'tugas_selesai', 'tugas_gagal', 'tugas_dihentikan',
        'alur_diajukan', 'alur_diaktifkan', 'alur_dijeda',
        'pemicu_terpicu', 'pemicu_terlewat',
        'modul_diajukan', 'modul_diaktifkan',
        'laporan_salah', 'persetujuan_data'
    )),
    isi JSONB NOT NULL DEFAULT '{}',
    id_induk UUID REFERENCES kejadian(id),
    hash_sebelumnya TEXT NOT NULL,
    hash TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_kejadian_tugas_id_id ON kejadian (tugas_id, id);
CREATE INDEX IF NOT EXISTS idx_kejadian_jenis ON kejadian (jenis);
CREATE INDEX IF NOT EXISTS idx_kejadian_waktu ON kejadian (waktu);

-- Migrasi CHECK jenis: CREATE TABLE IF NOT EXISTS di atas tidak mengubah
-- constraint tabel yang sudah ada (mis. dibuat saat himpunan masih 22 jenis),
-- jadi constraint lama diganti dengan daftar 31 jenis revisi 7 di sini.
-- Idempotent: DROP IF EXISTS lalu ADD dengan nama constraint yang sama.
ALTER TABLE kejadian DROP CONSTRAINT IF EXISTS kejadian_jenis_check;
ALTER TABLE kejadian ADD CONSTRAINT kejadian_jenis_check CHECK (jenis IN (
    'tugas_dibuat', 'rencana_dibuat', 'subagen_dibuat',
    'handoff_diajukan', 'handoff_diterima', 'handoff_diserahkan',
    'handoff_ditolak', 'handoff_kedaluwarsa', 'detak',
    'tool_dipanggil', 'tool_selesai', 'bukti_dicatat',
    'pemeriksaan_selesai', 'persetujuan_diminta', 'persetujuan_diberikan',
    'persetujuan_ditolak', 'pesan_user', 'pesan_agen', 'anggaran_habis',
    'tugas_selesai', 'tugas_gagal', 'tugas_dihentikan',
    'alur_diajukan', 'alur_diaktifkan', 'alur_dijeda',
    'pemicu_terpicu', 'pemicu_terlewat',
    'modul_diajukan', 'modul_diaktifkan',
    'laporan_salah', 'persetujuan_data'
));

-- Izin: peran aplikasi hanya INSERT + SELECT. Tanpa UPDATE/DELETE.
REVOKE UPDATE, DELETE ON kejadian FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT ON kejadian TO puspa_app;
        REVOKE UPDATE, DELETE ON kejadian FROM puspa_app;
    END IF;
END $$;
