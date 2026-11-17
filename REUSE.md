# REUSE — Log reuse lintas fase Puspa

Aturan (berlaku semua fase, perintah Nero): pelajari POLA/IDE besar
dari repo survei, lalu TULIS ULANG sebagai kode Puspa sendiri yang
disesuaikan dengan arsitektur revisi 7 + istilah domain Indonesia.
JANGAN PERNAH copas kode plek-ketiplek. JANGAN pakai kode berlisensi
GPL, AGPL, SSPL, PolyForm, CC BY-NC, atau lisensi kustom non-OSI.
JANGAN buat fork GitHub dari repo mana pun. Dependensi hanya via pip
dengan lisensi kompatibel (MIT/Apache-2.0/BSD) — catat lisensinya di
sini per fase.

Survei acuan: `~/workspace/match-hunt/repo-reuse-puspa-2026-11-17.md`
(81 repo) dan `~/workspace/match-hunt/chassis-tambahan-2026-11-17.md`
(76 entri).

## Fase 0 (selesai — ringkas, detail di STATUS.md Fase 0)

- **nanobot** (HKUDS/nanobot, MIT): chassis agen — dipakai sebagai
  dependency git pin (`nanobot-ai @ git+https://github.com/HKUDS/nanobot@e767174`),
  BUKAN fork; wiring hanya lewat titik ekstensi resmi nanobot; NOL
  baris kode nanobot diubah. Clone lokal hanya referensi baca.
- Dependensi baru Fase 0: psycopg, argon2-cffi, cryptography, pytest
  (+ dependensi nanobot).

## Fase 1 (Persona + Memori + Onboarding)

Tidak ada dependensi baru (semua dari venv Fase 0). Yang diadaptasi
hanya POLA (ditulis ulang, bukan disalin):

| Ide besar | Dari repo apa (lisensi) | Apa yang diadaptasi | Apa yang ditulis ulang dari nol |
| --- | --- | --- | --- |
| Fakta memori ber-asal, scoped per user; fakta disimpulkan tidak langsung masuk profil | mem0ai/mem0 (Apache-2.0) | Gagasan asal fakta + Usulan sebagai penampung fakta disimpulkan | `puspa/memory/profile.py`: asal tiga nilai revisi 7 (`diucapkan_user`, `dikonfirmasi_user`, `disimpulkan`), status `usulan`/`berlaku`/`ditolak`/`diganti`, merge UNION deterministik, konflik tidak ditimpa diam-diam |
| Fakta temporal (masa berlaku); prosedur = templat tanpa data pribadi | getzep/graphiti (Apache-2.0) | Gagasan `berlaku_dari`/`berlaku_sampai` + satu versi berlaku; prosedur tanpa nomor pelanggan | `profile.py` (versi fakta) + `puspa/memory/procedural.py` (siklus `percobaan`/`terverifikasi`/`rusak` revisi 7 §3.3) |
| Memori berjenjang + sleep-time compute (rapikan memori saat idle) | letta-ai/letta (Apache-2.0) | Gagasan konsolidasi saat sepi + ringkasan menunjuk ke kejadian asal | `puspa/memory/consolidate.py`: ringkasan ekstraktif DETERMINISTIK (tanpa model, Rp0), promosi pola berhasil ke prosedural |
| Persona sebagai data (character file: nama, sifat, gaya bicara) | elizaOS/eliza (MIT) | Gagasan gaya bicara sebagai data, bukan prompt keras | `puspa/persona/persona.py`: gaya sebagai fakta profil + contoh pesan (maks 20) + koreksi `dikonfirmasi_user` yang langsung berlaku; `render_reply` deterministik sementara |
| Index vektor untuk pencarian memori | pgvector (PostgreSQL, lisensi PostgreSQL — bebas dipakai) | Gagasan index kemiripan vektor | Skema `episodik.embedding vector(384)` + index HNSW; embedding `HashEmbedding` ditulis sendiri (hash token, Rp0) — seam pengganti disiapkan untuk model embedding lokal/berbayar murah |

**Tidak dipakai di Fase 1 (dicatat supaya tidak diulang):**
- mem0 sebagai LIBRARY (tidak diinstal): Puspa butuh asal tiga nilai
  revisi 7 + Usulan + riwayat versi — perilaku mem0 tidak cocok
  langsung; polanya saja yang dipakai.
- Graphiti sebagai LIBRARY (butuh Neo4j/FalkorDB): terlalu berat
  untuk MVP 11 pengguna; pola temporalnya saja yang dipakai di atas
  Postgres + pgvector.
- Letta sebagai SERVER (tidak dipakai): hanya pola sleep-time
  compute-nya.
- supermemory / cognee / openmemory: dibaca di survei, tidak ada pola
  yang diambil di Fase 1 (supermemory dicatat sebagai alternatif
  kalau mem0 terasa berat — tidak terjadi).
- Zep Community Edition: sudah tidak dikembangkan sejak 2025 (catatan
  survei) — tidak dipakai.

## Fase 3 (Wedge ganda + Moat)

Satu dependensi baru: `playwright==1.65.2` (MIT) — browser read-only
§2.8 (paket terinstal; unduhan browser Chromium revisi 1255 gagal
berulang dari CDN Playwright saat build — lihat STATUS_FASE3.md).
Yang diadaptasi hanya POLA (ditulis ulang, bukan disalin):

| Ide besar | Dari repo apa (lisensi) | Apa yang diadaptasi | Apa yang ditulis ulang dari nol |
| --- | --- | --- | --- |
| Graf embedded: node + edge sebagai baris relasional, tanpa server graf terpisah | LadybugDB/ladybug (MIT) — fork hidup Kuzu | Gagasan graf embedded di dalam proses/host sendiri (node = produk kanonik, edge = tautan listing) | `puspa/graphs/identity.py`: skema Postgres (§7: produk_kanonik + tautan_listing + harga_seri), pencocokan bertingkat kunci model deterministik, status tautan revisi 7 (`otomatis`/`dikonfirmasi_user`/`ditolak_user`) |
| Mesin yang menyimpan langkah kerja sebagai data yang bisa dipakai ulang | infiniflow/ragflow (Apache-2.0) | Gagasan langkah kerja sebagai data (bukan kode yang ditulis ulang tiap kali) + pemakaian ulang lintas agen | `puspa/graphs/service.py`: prosedur layanan sebagai templat berparameter tanpa data pribadi, siklus percobaan→terverifikasi→rusak, canary harian, versi + rollback — sebagai komponen MOAT, bukan produk dokumen (topik dokumen dilarang untuk ide baru; RAGFlow dipakai sebagai komponen memori/moat) |
| Browser read-only untuk membaca halaman (bukan klik/isi form) | Playwright (MIT) | Gagasan browser otomatis sebagai pembaca read-only | `puspa/pricing/readers.py`: `PlaywrightReader` (fail-closed bila browser tidak tersedia) + `HttpReader` (httpx, parser generik JSON-LD/meta) — tanpa menyalin selector milik siapa pun |

**Tidak dipakai di Fase 3 (dicatat supaya tidak diulang):**
- LadybugDB sebagai LIBRARY (tidak diinstal): graf Puspa jalan di
  atas Postgres + pgvector (§7) — polanya saja yang dipakai.
- RAGFlow sebagai LIBRARY (tidak diinstal): terlalu berat untuk MVP
  11 pengguna; pola penyimpanan langkah sebagai data saja yang
  dipakai di atas Postgres + Buku Kerja.
- Langflow (builder visual alur) → UX builder alur/modul adalah
  FASE 4, bukan Fase 3 — tidak dibangun di sini.
- Pipecat / LiveKit (voice) → modul Suara TIDAK ada di definisi
  revisi 7; dicatat sebagai pasca-MVP, tidak dibangun.
- LangGraph → TIDAK dipakai dan TIDAK diklaim di mana pun di Fase 3
  (koreksi: kalimat survei "Puspa sudah memakai LangGraph untuk
  orkestrasi" itu salah — nol import LangGraph di kode; orkestrasi =
  AgentLoop nanobot + siklus tugas Puspa sendiri).
- Shopee Open Platform / API resmi marketplace → JALUR RESMI yang
  sesuai syarat layanan, tapi butuh akun partner + persetujuan —
  bukan bagian MVP; menunggu keputusan Nero (keputusan 15).

## Fase 4 (Alur + Modul kustom)

Tidak ada dependensi baru (semua dari venv Fase 0/2/3). Yang diadaptasi
hanya POLA (ditulis ulang, bukan disalin):

| Ide besar | Dari repo apa (lisensi) | Apa yang diadaptasi | Apa yang ditulis ulang dari nol |
| --- | --- | --- | --- |
| Builder visual: kanvas node → flow → API; flow sebagai JSON yang bisa dieksekusi | langflow-ai/langflow (MIT, 156.719 bintang) | Gagasan flow sebagai data deklaratif + penyusun dari blok + pratinjau sebelum aktif | `puspa/workflow/composer.py` (penyusun chat deterministik — hanya blok pustaka, bukan model), `puspa/workflow/blocks.py` (15 blok bersertifikat + batas MVP), `puspa/workflow/panel.py` (kartu langkah di panel Alur, BUKAN klon UI Langflow), `puspa/workflow/manifest.py` (manifes izin turunan + cek himpunan bagian), `puspa/modules/manifest.py` (gerbang manifes 5 lapis) — keamanan yang Langflow tidak punya (approval gate, token izin, uji kering) |
| Otomasi berbasis pemicu (jadwal / ambang / jatuh tempo) | Home Assistant (Apache-2.0) | Gagasan otomasi berbasis pemicu terjadwal + aksi yang menunggu persetujuan | `puspa/workflow/triggers.py` (4 pemicu MVP; email/webhook ditolak) + `puspa/workflow/runner.py:tick_alur` (pemicu jadwal Fase 2 → jalan alur, pemicu terlewat dijalankan sekali + dilaporkan) |
| Pola persetujuan + kedaluwarsa aksi terjadwal | agno-agi/agno — AgentOS (Apache-2.0, pola sudah dipakai Fase 2) | Gagasan antrean persetujuan sebagai gerbang sebelum aksi keluar | `puspa/workflow/approvals.py`: persetujuan terikat isi (hash) + kedaluwarsa 24 jam lalu DIBATALKAN (bukan dieksekusi terlambat) — aturan khusus §2.12, bukan bawaan agno |

**Tidak dipakai di Fase 4 (dicatat supaya tidak diulang):**
- Langflow sebagai LIBRARY (tidak diinstal): builder Puspa butuh
  blok bersertifikat + manifes izin turunan + approval gate —
  perilaku Langflow (builder untuk developer, tanpa approval gate)
  tidak cocok langsung; polanya saja yang dipakai.
- n8n (Sustainable Use License — fair code, BUKAN OSI, masuk daftar
  jebakan survei): TIDAK dipakai, polanya pun tidak diadaptasi.
- Flowise (diarsipkan 13 Agustus 2026, end of life 31 Agustus 2026):
  TIDAK dipakai — sudah mati; pelajarannya (jangan bergantung pada
  satu vendor builder) yang dipakai: manifes Puspa format sendiri.
- Pipecat / LiveKit (voice) → pasca-MVP, sengaja tidak dibangun.
- LangGraph → TIDAK dipakai dan TIDAK diklaim di mana pun di Fase 4
  (nol import LangGraph di kode; orkestrasi = siklus tugas Puspa +
  runner alur sendiri).

## Glosarium istilah baru Fase 4 (bukan dari revisi 7)

- `eksekutor_fixture` — NAMA TEKNIS internal (eksekutor data uji:
  cek tagihan dari DATA_UJI Fase 3, harga live ditahan gerbang ToS).
  Istilah teknis standar ("fixture", "executor") — bukan istilah
  domain baru.
- `tick_alur` — NAMA TEKNIS internal (satu putaran penjadwal untuk
  alur, §2.14). "Tick" istilah teknis standar.
- `jalan_alur` — tabel jalan alur (§2.12: "tiap jalan alur adalah
  tugas biasa"). Nama tabel teknis, bukan istilah domain.
- `persetujuan_alur` — tabel antrean persetujuan alur (§2.12:
  "aksi keluar masuk antrean persetujuan"). Nama tabel teknis.
- `manifes_izin` — dari revisi 7 §2.12 ("manifes izin turunan").
- Tidak ada istilah domain baru yang diperkenalkan ke definisi.

## Glosarium istilah baru Fase 3 (bukan dari revisi 7)

- `SitusUji` — NAMA TEKNIS internal (situs uji lokal tagihan,
  `puspa/billing/fixture.py`). Istilah teknis standar ("fixture") —
  bukan istilah domain baru.
- `baca_ulang` — callable Pemeriksa (§2.5: "membaca ulang sumber").
  Nama teknis, bukan istilah domain.
- `pola:<nama>` — layanan prosedur untuk pola sub-agen (§3.3:
  "Pola sub-agen juga disimpan di sini"). Awalan teknis untuk
  membedakan pola dari prosedur layanan; bukan istilah domain baru.
- Tidak ada istilah domain baru yang diperkenalkan ke definisi.

## Fase 2 (Runtime tim + PWA + Konektor + Penjadwal)

Satu dependensi baru: `fastapi==0.163.1` (MIT) — PWA. Yang
diadaptasi hanya POLA (ditulis ulang, bukan disalin):

| Ide besar | Dari repo apa (lisensi) | Apa yang diadaptasi | Apa yang ditulis ulang dari nol |
| --- | --- | --- | --- |
| Router model bertingkat (pilih provider/model per peran, BYO key, fallback) | LiteLLM / BerriAI/litellm (MIT) | Gagasan router + fallback antar provider + harga per model sebagai konfigurasi | `puspa/router/router.py`: peran revisi 7 (perencana/penyaji/pembaca/pencari/pemeriksa/vision), aturan keluarga berbeda untuk Pemeriksa, tarif Rp/1.000 token; dibangun DI ATAS factory BYOK Fase 0 (`demo/byok_provider.py`), bukan client baru |
| Eksekusi durable, approval, scheduling, observability | agno-agi/agno — AgentOS (Apache-2.0) | Gagasan tugas sebagai unit durable yang bisa dilanjutkan dari log + antrean persetujuan + penjadwal sebagai pemicu tugas | `puspa/runtime/tasks.py` (siklus 5 tahap, antrean SKIP LOCKED), `puspa/scheduler/scheduler.py` (pemicu + idempotensi + terlewat), proyeksi `puspa/evidence/projections.py` |
| Pengerasan sandbox (fail-closed, allowlist jaringan) | OpenHands (MIT) | Gagasan sandbox yang berhenti bila tidak tersedia + allowlist domain | Dipakai ulang Fase 0 (`puspa/sandbox/allowlist.py`); Fase 2 menegakkannya di konektor (baca konten hanya lewat `QuarantinedReader` + domain allowlist) — tidak ada kode sandbox baru |
| UX agen: chat + daftar tugas + antrean approval | opencode (MIT) | Gagasan layar chat/task/approval sebagai satu alur kerja agen | `puspa/pwa/app.py` + `apps/pwa/templates/` (7 layar revisi 7, SSE dari proyeksi Buku Kerja, bukan WebSocket) |
| Konektor + otomasi terjadwal (integrasi sebagai data, bukan kode) | Home Assistant (Apache-2.0) | Gagasan konektor per entitas dengan scope minimal + otomasi berbasis pemicu terjadwal | `puspa/connectors/google.py` (Gmail/Drive/Calendar di atas OAuth Fase 1) + `puspa/connectors/grants.py` (pemberian per agen) + `puspa/scheduler/` |
| Pola cloud: RLS + auth + realtime | Supabase (Apache-2.0) | Gagasan isolasi baris per pengguna + langganan perubahan | RLS Fase 2 (`infra/schema_fase2.sql`, kebijakan `current_setting('app.current_user_id')`) + SSE (`/sse`) sebagai ganti realtime — Supabase sebagai LIBRARY tidak dipakai |

**Tidak dipakai di Fase 2 (dicatat supaya tidak diulang):**
- LiteLLM sebagai LIBRARY (tidak diinstal): router Puspa butuh
  peran revisi 7 + aturan keluarga Pemeriksa + tarif Rupiah —
  perilaku LiteLLM tidak cocok langsung; polanya saja yang dipakai.
- agno/AgentOS sebagai LIBRARY (tidak diinstal): terlalu berat
  untuk MVP 11 pengguna; pola durable + approval + scheduling-nya
  saja yang dipakai di atas Postgres + Buku Kerja.
- Langflow (builder visual alur) → UX builder alur/modul adalah
  FASE 4, bukan Fase 2 — tidak dibangun di sini.
- Pipecat / LiveKit (voice) → modul Suara TIDAK ada di definisi
  revisi 7; dicatat sebagai pasca-MVP, tidak dibangun.
- Supabase sebagai LIBRARY (tidak dipakai): hanya polanya
  (RLS + SSE).
- OpenHands sebagai LIBRARY (tidak dipakai): hanya pola
  pengerasan sandbox-nya; sandbox Fase 0 dipakai ulang.

## Glosarium istilah baru Fase 2 (bukan dari revisi 7)

- `Pemicu` / `pemicu_jalan` — dari revisi 7 §2.14 (tabel pemicu +
  kunci idempotensi). Nama tabel teknis, bukan istilah domain.
- `CassettePlayer` — NAMA TEKNIS internal (pemutar kaset, §2.11).
  Istilah teknis standar ("cassette" = kaset uji) — bukan istilah
  domain baru.
- `BudgetTracker` — NAMA TEKNIS internal (pelacak anggaran §2.4).
- `SubagentPool` — NAMA TEKNIS internal (kolam sub-agen §2.2).
- `EvidenceRecord` — NAMA TEKNIS internal (rekaman bukti §2.5).
- Tidak ada istilah domain baru yang diperkenalkan ke definisi.

## Glosarium istilah baru Fase 1 (bukan dari revisi 7)

Sesuai perintah Nero 21:53 WIB: jangan buat istilah baru yang tidak
ada di revisi 7 dan bukan istilah teknis standar; kalau butuh, catat
di sini.

- `HashEmbedding` — NAMA TEKNIS internal (bukan istilah domain):
  embedding deterministik dari hash token. Istilah teknis standar
  ("hash", "embedding") — bukan istilah domain baru.
- `StubNotifier` — NAMA TEKNIS internal: pemberi notif pengganti
  sampai Web Push Fase 2 ada. Bukan istilah domain.
- `Usulan` — dari revisi 7 §2.7 (fakta disimpulkan "muncul di Usulan").
- `contoh_gaya` — tabel untuk contoh pesan persona (§2.16: "tempel
  10 sampai 20 pesan contoh"). Nama tabel teknis, bukan istilah domain.
- `pemberian_irisan` — tabel audit pemberian irisan (§2.7: "pemberian
  irisan dicatat di Buku Kerja"). Nama tabel teknis.
- `ringkasan` — hasil konsolidasi (§2.7: "job meringkas episodik
  lama"). Kata umum Indonesia.
- Tidak ada istilah domain baru yang diperkenalkan ke definisi.
