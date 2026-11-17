# NOTICE — Atribusi kode pihak ketiga di repo Puspa

Repo ini adalah repo standalone Puspa (mirzasnowy/puspa-ai), BUKAN fork
dari repo mana pun. Kode pihak ketiga yang dipakai ulang dicatat di sini,
dengan lisensi aslinya tetap disertakan. Lisensi non-komersial atau
copyleft (GPL, CC BY-NC-SA, PolyForm Noncommercial) tidak dipakai.

## nanobot (chassis) — MIT

- **Sumber:** https://github.com/HKUDS/nanobot
- **Versi yang dipakai sebagai referensi (clone lokal, belum di-vendor):** HEAD `e767174` (17 Nov 2026), paket `nanobot-ai` 0.4.1
- **Lokasi di repo ini:** direktori `nanobot/` (salinan penuh hasil clone,
  termasuk file `LICENSE` aslinya)
- **Lisensi:** MIT License
- **Copyright:** Copyright (c) 2025-present Xubin Ren and the nanobot contributors
- **Teks lisensi lengkap:** `nanobot/LICENSE`
- **Atribusi pihak ketiga yang ikut di dalam nanobot:** `nanobot/THIRD_PARTY_NOTICES.md`
  (mis. Tabler Icons, MIT). Kalau kode nanobot dipakai ulang di luar repo
  ini, atribusi di atas beserta file LICENSE wajib ikut serta.

## Aturan pemakaian ulang (perintah Nero)

- Jangan pernah membuat fork GitHub dari repo mana pun.
- Kode orang dipakai sebagai dependency pip, atau di-vendor dengan file
  LICENSE asli + atribusi di file ini dan di README.
- Setiap entri vendor baru wajib dicatat di file ini sebelum dipakai.


# NOTICE — Fase 1 Puspa

## Atribusi pola (bukan kode)

Fase 1 mempelajari POLA dari repo berikut lalu MENULIS ULANG sebagai
kode Puspa sendiri (tidak ada kode yang disalin plek-ketiplek, tidak
ada dependensi baru):

- **mem0ai/mem0** (Apache License 2.0) — pola: lapisan memori
  percakapan, fakta ber-asal, scoped per user/agent/app.
  Dipakai sebagai pola di `puspa/memory/profile.py`.
- **getzep/graphiti** (Apache License 2.0) — pola: graf pengetahuan
  temporal (fakta punya masa berlaku); prosedur sebagai templat tanpa
  data pribadi. Dipakai sebagai pola di `puspa/memory/profile.py`
  dan `puspa/memory/procedural.py`.
- **letta-ai/letta** (Apache License 2.0) — pola: memori berjenjang +
  sleep-time compute (merapikan memori saat idle). Dipakai sebagai
  pola di `puspa/memory/consolidate.py`.
- **elizaOS/eliza** (MIT License) — pola: character file (persona
  sebagai data: nama, sifat, gaya bicara). Dipakai sebagai pola di
  `puspa/persona/persona.py`.

Lisensi penuh masing-masing repo ada di repo sumbernya (tautan di
`../REUSE.md`). Tidak ada file LICENSE pihak ketiga yang perlu
disertakan di sini karena tidak ada kode pihak ketiga yang di-vendor.

## Lisensi Puspa

Kode di direktori ini adalah karya Puspa (Nero / Luna). Lisensi repo
Puspa mengikuti repo `mirzasnowy/puspa-ai` saat push.
