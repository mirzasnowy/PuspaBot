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
