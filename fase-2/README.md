# README — Fase 2 MVP Puspa (Runtime tim + PWA + Konektor + Penjadwal)

Definisi: revisi 7 (disetujui Nero 17 Nov 2026 ~17:42 WIB).
Timeline diabaikan atas perintah Nero 21:53 WIB. Detail lengkap:
`STATUS_FASE2.md`. JANGAN push ke GitHub; JANGAN mulai Fase 3.

## Struktur

```
build/fase-2/
  puspa/                 # namespace Puspa (extend_path, bareng Fase 0/1)
    runtime/             # agen, handoff, anggaran, kolam sub-agen, siklus tugas
    evidence/            # Buku Bukti, Pemeriksa, renderer angka yatim, proyeksi
    router/              # router model bertingkat (di atas BYOK Fase 0)
    connectors/          # Gmail/Drive/Calendar + pemberian per agen
    scheduler/           # penjadwal + pemicu (backup harian, konsolidasi)
    push/                # Web Push VAPID (aes128gcm)
    cassette/            # uji kaset
    pwa/                 # aplikasi FastAPI (7 layar)
  apps/pwa/              # template Jinja + static (service worker, manifest)
  infra/schema_fase2.sql # tabel Fase 2 + RLS + trigger tolak-hapus-Puspa
  cassettes/             # 10 kaset acuan (generator: buat_kaset_acuan.py)
  tests/                 # suite Fase 2 (73 tes)
```

Pemetaan layout lama → baru (§7): lihat `STATUS_FASE2.md` §9.

## Menjalankan tes (SOLO — jangan paralel dengan pytest lain)

```bash
cd build/fase-2
NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
  ../fase-0/.venv/bin/python -m pytest tests -q
# 73 passed
```

Verifikasi ulang base (solo, berurutan):

```bash
cd ../fase-0 && ../fase-0/.venv/bin/python -m pytest tests -q  # 244 passed, 4 skipped
cd ../fase-1 && ../fase-0/.venv/bin/python -m pytest tests -q  # 58 passed
```

## Menjalankan PWA lokal

```bash
cd build/fase-2
NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
  ../fase-0/.venv/bin/python -c "
import sys; sys.path.insert(0, '../fase-0'); sys.path.insert(0, '../fase-1'); sys.path.insert(0, '.')
from puspa.pwa.app import jalankan
jalankan(host='127.0.0.1', port=8000)"
# Buka http://127.0.0.1:8000/chat (butuh login Google; mode demo:
# create_app(test_user_id='<uuid pengguna>') — lihat STATUS_FASE2.md)
```

## Kebijakan bahasa (perintah Nero 21:53 WIB)
Identifier Inggris, nilai domain Indonesia (revisi 7), prosa
Indonesia. Jangan buat istilah baru yang tidak ada di revisi 7 dan
bukan istilah teknis standar; kalau butuh, catat di `../REUSE.md`.

## Dependensi baru Fase 2
- `fastapi==0.163.1` (MIT) — PWA. Sisanya dari venv Fase 0.
