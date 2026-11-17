# Fase 1 MVP Puspa — Persona + Memori + Onboarding

Definisi: revisi 7 (disetujui Nero 17 Nov 2026 ~17:42 WIB).
Periode kerja: 17 Nov 2026 (timeline 17 minggu diabaikan atas perintah
Nero 21:53 WIB — tanggal hanya urutan, bukan deadline).

## Yang dibangun

| Paket | Isi |
| --- | --- |
| `puspa/memory/` | Memori 3 lapis: Profil, Episodik (pgvector), Prosedural; irisan memori; konsolidasi saat sepi |
| `puspa/persona/` | Gaya bicara dari contoh + koreksi; harness uji buta gerbang 3 |
| `puspa/onboarding/` | Onboarding 4 langkah, target 5 menit, terukur |
| `puspa/auth/` | Login Google (OAuth 2.0 + PKCE) + tautan email cadangan |
| `puspa/backup/` | Cadangan Postgres terenkripsi + restore |
| `puspa/consent/` | Persetujuan data + ekspor/hapus akun |

Base Fase 0 dipakai ulang tanpa diubah: `puspa/ledger` (Buku Kerja),
`puspa/accounts` (users/sessions/vault/rls/access), `puspa/policy`,
`puspa/quarantine`, `puspa/sandbox`, `puspa/chassis`. Impor base lewat
`extend_path` di `puspa/__init__.py` (lihat `tests/conftest.py`).

## Cara jalan tes (SOLO — jangan pernah dua pytest bersamaan)

```bash
cd build/fase-1
NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
  ../fase-0/.venv/bin/python -m pytest tests -q
```

Tidak ada dependensi baru (lihat `requirements.txt`): semua yang
dipakai Fase 1 sudah ada di venv Fase 0 (psycopg, cryptography,
argon2-cffi, httpx, pytest). pgvector 0.6.0 diinstal via apt
(`postgresql-16-pgvector`).

## Angka

- Suite Fase 1: **58 passed** (17 Nov 2026, pasca-pemulihan Postgres).
- Suite Fase 0 (verifikasi ulang): **244 passed, 4 skipped**
  (4 skip = tes live-container Docker, blokir lingkungan yang sama
  seperti Fase 0).
- Onboarding (sisi sistem, 4 langkah): **0,29 detik** (target 5 menit).
- Backup + restore ke DB bersih: **0,3 detik**, 14 tabel pulih,
  rantai hash Buku Kerja utuh.

## Yang tertunda (butuh Nero / manusia — bukan blokir kode)

1. **Publikasi aplikasi Google OAuth** ke "In production" (butuh
   browser Nero; status Testing = refresh token kedaluwarsa 7 hari).
2. **Uji buta gerbang 3**: harness + 20 item contoh jadi; 5 teman juri
   belum ada yang menjawab.
3. **Tinjauan hukum** teks persetujuan (keputusan 20) sebelum pengguna
   di luar lingkaran dekat.
4. **Keputusan 5** (nomor pelanggan PLN/PDAM/HP) — dibutuhkan Fase 2.

Detail lengkap + konflik definisi vs build: `STATUS_FASE1.md`.
Log reuse lintas fase: `../REUSE.md`.
