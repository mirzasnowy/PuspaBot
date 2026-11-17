# Fase 4 — Alur + Modul kustom (revisi 7 §2.12–2.13)

Pustaka blok, pemicu, penyusun alur lewat chat, manifes izin turunan,
uji kering, runner alur, persetujuan kedaluwarsa 24 jam, modul kustom
dengan gerbang manifes, dan 3 templat bawaan. Semua di `build/fase-4/`;
paket Fase 0/1/2/3 tidak diubah (dipakai lewat `sys.path`, pola
conftest fase 0/1/2/3).

## Struktur

| Modul | Isi |
| --- | --- |
| `puspa/workflow/blocks.py` | Pustaka 15 blok tetap + skema + batas MVP (10 langkah, 2 cabang, 20 item) |
| `puspa/workflow/triggers.py` | 4 pemicu (jadwal, jatuh tempo, harga turun, manual); email/webhook ditolak |
| `puspa/workflow/composer.py` | Penyusun alur lewat chat (deterministik, hanya blok pustaka) + Permintaan Blok |
| `puspa/workflow/manifest.py` | Manifes izin turunan (dihitung kode) + cek himpunan bagian vs izin user |
| `puspa/workflow/approvals.py` | Antrean persetujuan: terikat isi, kedaluwarsa 24 jam lalu dibatalkan |
| `puspa/workflow/runner.py` | CRUD + versi/rollback alur, uji kering, jalan alur, tick penjadwal |
| `puspa/workflow/templates.py` | 3 templat bawaan; tugas acuan 8 dan 9 sebagai alur |
| `puspa/workflow/panel.py` | Render kartu langkah + hasil jalan (HTML tanpa JavaScript) |
| `puspa/workflow/metrics.py` | Metrik alur §3.8 (pengguna aktif, jalan tanpa campur, Permintaan Blok) |
| `puspa/modules/manifest.py` | Manifes modul + gerbang manifes (5, oleh kode) + CRUD/versi/rollback/hapus |
| `puspa/modules/tables.py` | Tabel milik modul (kolom bertipe, isolasi per pengguna + per modul) |
| `puspa/modules/views.py` | Render 7 komponen tetap (termasuk grafik garis SVG, tanpa JavaScript) |
| `puspa/modules/notifications.py` | Aturan notif dari data modul |
| `puspa/modules/tagihan.py` | Modul bawaan Tagihan sebagai manifes |
| `infra/schema_fase4.sql` | Tabel alur, jalan_alur, persetujuan_alur, modul, tabel_modul, baris_modul (+ RLS) |

## Menjalankan tes

```bash
cd build/fase-4
NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
  ../fase-0/.venv/bin/python -m pytest tests -q
```

Tidak ada dependensi baru (lihat `requirements.txt`). JANGAN jalankan
pytest paralel (tabrakan TRUNCATE di DB `puspa_test` yang sama).

## Angka tes (run solo, 17 Nov 2026)

| Suite | Hasil |
| --- | --- |
| Fase 4 (baru) | 76 passed |
| Fase 0 | 248 passed, 4 skipped |
| Fase 1 | 58 passed |
| Fase 2 | 73 passed |
| Fase 3 (dengan browser) | 62 passed |

Detail, gerbang, dan blocker: `STATUS_FASE4.md`.
