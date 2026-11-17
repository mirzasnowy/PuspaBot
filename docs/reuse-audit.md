# Audit Reuse Puspa — apa yang dicopy dari chassis

Tanggal audit: 17 November 2026 ~22:40 WIB. Auditor hanya baca
(tidak mengubah kode apa pun). Cakupan: pohon `build/` lokal
(fase 0, 1, 2, 3 yang sedang berjalan, `cli/`, `web/`).
Repo GitHub `mirzasnowy/puspa-ai` yang sudah di-push baru berisi
fase 0 (3 commit) — fase 1–3 belum di-push.

## Jawaban singkat untuk Nero

Yang benar-benar "dicopy" (dipakai kodenya) hanya **satu**: nanobot,
sebagai **dependensi pip** (git pin), bukan salinan kode — clone
lokalnya bersih, 0 baris diubah, dan paket Puspa hanya membungkusnya
lewat titik ekstensi resmi (subclass + `super()`).

Yang lain: **pola yang dipelajari lalu ditulis ulang** sebagai kode
Puspa sendiri (mem0, Graphiti, Letta, elizaOS, LiteLLM, agno,
OpenHands, opencode, Home Assistant, Supabase, LadybugDB, RAGFlow)
atau **hanya dipelajari, tidak ada yang diambil** (LangGraph,
LangChain, Agent-Reach, hermes-agent, ECC, OpenBot, E2B/Firecracker,
Langflow, Pipecat/LiveKit, superpowers, dusk.bot — desain saja).

**Nol salinan verbatim ditemukan** di seluruh kode Puspa
(lihat "Cara memeriksa" di bawah untuk metode + batasnya).

## Cara memeriksa (dan batasnya)

1. Grep semua `import`/`from` di `fase-0/puspa`, `fase-0/demo`,
   `fase-1/puspa`, `fase-2/puspa`, `fase-3/puspa`, `cli/puspa_cli`:
   tidak ada satu pun import `mem0`, `langchain`, `langgraph`,
   `graphiti`, `litellm`, `agno`, `letta`, `eliza`, `opencode`,
   `hermes`, `agent_reach`, `pipecat`, `livekit`, `langflow`,
   `supabase`, `homeassistant`.
2. Grep nama API khas sumber (`add_memory`, `StateGraph`,
   `add_node`, `add_edge`, `MemoryClient`, `add_episode`,
   `add_triplet`, `add_messages`, dsb.): hanya ketemu sebutan
   "Graphiti" di docstring (kutipan pola) dan satu false positive
   ("END" di dalam kata "SENDIRI"). Tidak ada permukaan API asing.
3. Grep `Copyright` di semua kode Puspa: kosong. `find` file
   `LICENSE*` dan `SKILL.md` di luar `nanobot/`: kosong.
4. `git -C fase-0/nanobot status --porcelain`: 0 baris (dicek 2x,
   terakhir ~22:40 WIB). `git diff --stat HEAD`: kosong.
5. Baca header/docstring tiap file yang mengutip pola, lalu
   bandingkan desain Puspa dengan sumbernya — perbedaannya
   tercatat di tabel (mis. mem0 menyimpan fakta disimpulkan;
   Puspa justru TIDAK menyimpannya di profil, melainkan di Usulan).
6. Batas kejujuran: tanpa membandingkan baris demi baris dengan
   semua repo sumber (tidak di-clone), nol mutlak tidak bisa
   dibuktikan secara matematis. Tapi bukti 1–5 di atas, ditambah
   kode Puspa yang hampir seluruhnya ber-identifier Indonesia
   dengan desain yang menyimpang dari sumbernya, adalah bukti kuat.

Log reuse resmi ada di `build/REUSE.md` (8.617 byte, 17 Nov 2026
15:25) — BUKAN di `fase-0/REUSE.md` seperti diasumsikan brief
awal. Referensi `../REUSE.md` dari `fase-1/` dan `fase-2/` VALID
(menunjuk ke `build/REUSE.md`). Atribusi nanobot ada di
`build/fase-0/NOTICE.md`; atribusi pola fase 1 ada di
`build/fase-1/NOTICE.md`.

## Tabel per repo

| Repo (lisensi) | Yang diambil | Bukti file |
|---|---|---|
| **nanobot** — HKUDS/nanobot (MIT) | **Dependensi pip saja** (git pin `e767174`, paket `nanobot-ai`), BUKAN salinan kode. Clone lokal hanya referensi baca. Wiring hanya lewat titik ekstensi resmi: subclass `ToolRegistry`/`SkillsLoader`/`AgentHook` + `super()`. 0 baris nanobot diubah (git status bersih). | `fase-0/requirements.txt`; `fase-0/NOTICE.md`; `fase-0/puspa/chassis/*.py` (import `nanobot.agent.*`, 942 baris); `git -C fase-0/nanobot status` = 0 |
| **opencode** — anomalyco/opencode (MIT, TypeScript) | **Tidak ada kode yang diambil.** Pola UX (layar chat/task/approval sebagai satu alur) dipakai sebagai referensi desain di PWA fase 2, ditulis ulang sebagai FastAPI + Jinja + HTMX + SSE. Catatan: kata "opencode" di `demo/MATRIKS_BYOK.md:169` adalah nama provider di registry nanobot sendiri (`fase-0/nanobot/nanobot/providers/registry.py:334-346`), BUKAN repo ini. | `fase-2/puspa/pwa/app.py` (tidak ada import opencode); `build/REUSE.md` (Fase 2); `fase-2/STATUS_FASE2.md:297` |
| **langgraph** — langchain-ai/langgraph (MIT) | **Tidak ada yang diambil.** Nol import, nol kode. Orkestrasi Puspa = `AgentLoop` nanobot + siklus tugas 5 tahap Puspa sendiri di atas Postgres + Buku Kerja. | `fase-2/puspa/runtime/tasks.py` (header §2.4 revisi 7, tanpa sebutan LangGraph); grep `langgraph` di semua kode = kosong |
| **langchain** — langchain-ai/langchain (MIT) | **Tidak ada yang diambil.** Nol import, nol kode. | grep `langchain` di semua kode = kosong |
| **mem0** — mem0ai/mem0 (Apache-2.0) | **Pola saja, ditulis ulang** (fase 1): fakta ber-asal, scoped per user, fakta disimpulkan TIDAK langsung masuk profil (ditampung di Usulan). Library TIDAK diinstal. Beda dari aslinya: asal tiga nilai revisi 7 (`diucapkan_user`/`dikonfirmasi_user`/`disimpulkan`), status `usulan`/`berlaku`/`ditolak`/`diganti`, merge UNION deterministik. | `fase-1/puspa/memory/profile.py:3-9`; `fase-1/NOTICE.md:9-11`; `build/REUSE.md` (Fase 1); `fase-1/STATUS_FASE1.md:200` |
| **agent-reach** — (repo kontribusi Nero) | **Tidak ada yang diambil.** Nol sebutan di seluruh kode + dokumen Puspa. PR #880 adalah kontribusi Nero KE repo itu, bukan sumber buat Puspa. | grep `agent.?reach` di `fase-0/`, `fase-1/`, `fase-2/`, `fase-3/`, `cli/`, `web/` = kosong |
| **hermes-agent** — NousResearch/hermes-agent (MIT) | **Tidak ada yang diambil.** Nol sebutan di kode. Learning loop + `hermes claw migrate` hanya referensi desain di survei 1 untuk modul self-develop (fase 6, belum dibangun). | grep `hermes` di semua kode + dokumen = kosong; survei 1 `repo-reuse-puspa-2026-11-17.md:45-48` |
| **agno** — agno-agi/agno, AgentOS (Apache-2.0) | **Pola saja, ditulis ulang** (fase 2): eksekusi durable, approval, scheduling, observability → tugas sebagai unit durable di Postgres (SKIP LOCKED), penjadwal, proyeksi bukti. Library TIDAK diinstal. | `fase-2/puspa/runtime/tasks.py`; `fase-2/puspa/scheduler/scheduler.py`; `fase-2/puspa/evidence/projections.py`; `build/REUSE.md` (Fase 2); `fase-2/STATUS_FASE2.md:293-294` |
| **langflow** — langflow-ai/langflow (MIT) | **Belum ada yang diambil.** Builder visual alur/modul = fase 4, belum dibangun. | `build/REUSE.md` (Fase 2, "Tidak dipakai"); `fase-2/STATUS_FASE2.md:306` |
| **LiteLLM** — BerriAI/litellm (MIT) | **Pola saja, ditulis ulang** (fase 2): router multi-provider + spend tracking → `puspa/router/router.py`, dibangun DI ATAS factory BYOK fase 0 (`demo/byok_provider.py`), bukan di atas litellm. Library TIDAK diinstal; nol sebutan "litellm" di `demo/`. Beda dari aslinya: peran revisi 7 + aturan keluarga Pemeriksa + tarif Rupiah. | `fase-2/puspa/router/router.py:3-4,106-108`; `build/REUSE.md` (Fase 2); grep `litellm` di `fase-0/demo/` = kosong |
| **pipecat** (BSD-2-Clause) / **livekit/agents** (Apache-2.0) | **Belum ada yang diambil.** Modul Suara TIDAK ada di definisi revisi 7; pasca-MVP. | `build/REUSE.md` (Fase 2, "Tidak dipakai") |
| **home-assistant** — home-assistant/core (Apache-2.0) | **Pola saja, ditulis ulang** (fase 2): konektor per entitas dengan scope minimal + otomasi berbasis pemicu → konektor Google (Gmail/Drive/Calendar di atas OAuth fase 1) + pemberian per agen + penjadwal. Library TIDAK dipakai. | `fase-2/puspa/connectors/google.py` (header); `fase-2/puspa/connectors/grants.py`; `build/REUSE.md` (Fase 2) |
| **ladybugdb** — LadybugDB/ladybug (MIT) | **Pola saja, ditulis ulang** (fase 3, sedang berjalan): graf embedded — node dan edge sebagai baris relasional, bukan server graf → graf identitas + harga di atas Postgres. Library TIDAK dipakai. | `fase-3/puspa/graphs/identity.py` (header); grep import `ladybug`/`kuzu`/`networkx` = kosong |
| **supabase** — supabase/supabase (Apache-2.0) | **Pola saja, ditulis ulang** (fase 2): RLS + realtime → RLS Postgres (`app.current_user_id`) + SSE sebagai ganti realtime. Library TIDAK dipakai. | `fase-2/infra/schema_fase2.sql:138-157`; `build/REUSE.md` (Fase 2); `fase-2/STATUS_FASE2.md:300-301` |
| **openhands** — OpenHands/OpenHands (MIT) | **Pola saja, ditulis ulang** (fase 2): pengerasan sandbox (fail-closed + allowlist domain) → dipakai ulang allowlist fase 0, ditegakkan di konektor. Library TIDAK dipakai. Sandbox Puspa = Docker (`docker run`), bukan microVM. | `fase-0/puspa/sandbox/runner.py` (`docker run`, bukan Firecracker); `fase-2/STATUS_FASE2.md:295-296`; `build/REUSE.md` (Fase 2) |
| **dusk.bot** — DuskBotAI/duskbot (PolyForm Noncommercial 1.0.0) | **Desain saja, bukan kode.** Studi di `web/STUDI_DUSK.md`: ritme seksi, panel mock, kartu harga. Tidak ada kode, teks, atau gambar yang disalin, diunduh, atau di-hotlink. Tidak ada URL eksternal di file web. `web/assets/` hanya berisi logo Puspa + ikon hasil generate. | `web/STUDI_DUSK.md`; grep `https?://` di `web/` (di luar w3.org/localhost) = kosong; `ls web/assets/` |
| **ECC** — affaan-m/ECC (MIT) | **Tidak ada yang diambil** (dipelajari saja). Nol sebutan di kode Puspa. Pola skill SKILL.md + hooks + continuous learning dicatat di survei, tapi implementasi Puspa mengutip agentskills/Graphiti/Letta, bukan ECC. AgentShield: nol sebutan. | grep `ECC`/`AgentShield`/`shield` di semua kode = kosong (hanya disebut di `fase-0/STATUS.md:10` sebagai aturan tanpa-fork) |
| **OpenBot** — CopilotKit/OpenBot (MIT) | **Pola desain saja, tidak ada kode.** Konsep gateway fail-closed menginformasikan desain gerbang Puspa, tapi `gate.py` ditulis sendiri (token + batas belanja + persetujuan + idempotensi) TANPA CEL (grep `cel` = kosong). | `fase-0/puspa/policy/gate.py` (header + import: hanya `hashlib`/`time`/`uuid`/`dataclasses` + `puspa.policy.tokens`); survei 1 baris 52-57 |
| **E2B / Firecracker** (Apache-2.0) | **Tidak ada yang diambil.** Konsep "komputer per bot" hanya pengaruh desain. Sandbox Puspa = Docker, fail-closed. | `fase-0/puspa/sandbox/runner.py`; survei 1 baris 102-148 |
| **agentskills** — agentskills/agentskills (Apache-2.0) | **Format standar saja, bukan kode.** SKILL.md + frontmatter dipakai sebagai format skill Puspa (supaya kompatibel dengan ekosistem). Validator `skills-ref` TIDAK dipakai. Tidak ada kode yang di-vendor, jadi tidak ada file LICENSE yang wajib disertakan. | `fase-0/puspa/chassis/skills.py:22` ("frontmatter agentskills"); grep `skills-ref` = kosong; survei 1 baris 192-195 |
| **superpowers / mattpocock/skills / anthropics/skills** (MIT) | **Tidak ada yang diambil.** Tidak ada file SKILL.md dari koleksi ini di Puspa. | `find . -name SKILL.md` di luar `nanobot/` = kosong |
| **Graphiti** — getzep/graphiti (Apache-2.0) | **Pola saja, ditulis ulang** (fase 1 + fase 3): fakta temporal (`berlaku_dari`/`berlaku_sampai`, satu versi berlaku) → `profile.py`; prosedur sebagai templat tanpa data pribadi → `procedural.py` + `graphs/service.py`. Library TIDAK diinstal (butuh Neo4j/FalkorDB — terlalu berat untuk MVP; dicatat di REUSE.md). | `fase-1/puspa/memory/profile.py:7-9`; `fase-1/puspa/memory/procedural.py:13-14`; `fase-3/puspa/graphs/service.py:8`; `build/REUSE.md` (Fase 1, "Tidak dipakai") |
| **Letta** — letta-ai/letta (Apache-2.0) | **Pola saja, ditulis ulang** (fase 1): sleep-time compute / konsolidasi saat idle → `consolidate.py`. Beda dari aslinya: ringkasan Puspa DETERMINISTIK dan ekstraktif (tanpa model, Rp0); Letta memakai model. | `fase-1/puspa/memory/consolidate.py` (header); `fase-1/NOTICE.md:16-18`; `build/REUSE.md` (Fase 1) |
| **elizaOS** — elizaOS/eliza (MIT) | **Pola saja, ditulis ulang** (fase 1): character file (persona sebagai data) → gaya bicara sebagai fakta profil + contoh pesan (maks 20) + koreksi yang langsung berlaku. Bukan file karakter JSON. | `fase-1/puspa/persona/persona.py:3-7`; `fase-1/NOTICE.md:19-21`; `build/REUSE.md` (Fase 1) |
| **RAGFlow** — infiniflow/ragflow (Apache-2.0) | **Pola saja, ditulis ulang** (fase 3, sedang berjalan): langkah kerja sebagai data yang bisa dipakai ulang → prosedur layanan di atas Postgres. Library TIDAK dipakai. | `fase-3/puspa/graphs/service.py` (header) |
| **pgvector** (lisensi PostgreSQL) | **Ekstensi database, bukan kode.** Kolom `vector(384)` + index HNSW; embedding `HashEmbedding` ditulis sendiri (hash token, Rp0). | `fase-1/puspa/memory/embeddings.py` (import: `hashlib`/`math`/`re`); `fase-1/puspa/memory/episodic.py` (`::vector`, cosine pgvector); `build/REUSE.md` (Fase 1) |

## Dependensi pip (dipakai sebagai pustaka, BUKAN penyalinan kode)

`nanobot-ai` (git pin `e767174`, MIT) · `psycopg[binary]` · `argon2-cffi` ·
`cryptography` · `pytest` · `pydantic` · `pydantic-settings` · `loguru` ·
`pyyaml` · `json_repair` · `tiktoken` · `jinja2` · `regex` · `jsonschema` ·
`filelock` · `httpx` · `rich` · `pillow` · `fastapi==0.163.1` (MIT, fase 2) ·
`uvicorn` · `playwright==1.65.2` (MIT, fase 3).
Bukti: `fase-0/requirements.txt`, `fase-1/requirements.txt`,
`fase-2/requirements.txt`, `fase-3/requirements.txt`,
`cli/requirements.txt`.

## Temuan — yang belum beres

1. **REUSE.md belum punya bagian Fase 3.** Dependensi baru
   `playwright==1.65.2` dan pola RAGFlow + LadybugDB baru tercatat
   di header kode (`fase-3/puspa/graphs/service.py`,
   `fase-3/puspa/graphs/identity.py`, `fase-3/requirements.txt`),
   belum di `build/REUSE.md`. Fase 3 juga belum punya STATUS file.
2. **REUSE.md bagian Fase 0 tidak mencatat pengaruh desain.**
   Bagian Fase 1 dan Fase 2 mencatat tiap pola + asalnya, tapi
   bagian Fase 0 hanya mencantumkan nanobot. Pengaruh desain fase 0
   yang tidak tercatat di log: pola gateway fail-closed OpenBot,
   konsep komputer-per-bot E2B/Firecracker, format agentskills,
   pola skill/learning ECC, dan learning loop hermes-agent.
   Ini celah LOG, bukan pelanggaran — tidak ada kode dari repo-repo
   itu di fase 0.
3. **Kalimat survei 2 soal LangGraph salah.** Entri langchain di
   `chassis-tambahan-2026-11-17.md` menulis "Puspa sudah memakai
   LangGraph untuk orkestrasi" — tidak benar: nol import dan nol
   kode LangGraph di seluruh pohon. Orkestrasi Puspa = nanobot
   `AgentLoop` + siklus tugas Puspa sendiri. Kalimat itu perlu
   dikoreksi di survei (atau dicatat sebagai kesalahan).
4. **Celah `get_definitions` di `PuspaRegistry` masih terbuka saat
   audit dibaca.** `fase-0/puspa/chassis/registry.py` (dibaca ulang
   ~22:40 WIB, belum berubah) masih mendokumentasikan
   "`get_definitions` tidak diperiksa — skema tool yang dilihat
   model bukan eksekusi." Model melihat 18 tool bawaan nanobot
   padahal token hanya mengizinkan 5 (dibuktikan run live-1 CLI).
   Perbaikan sedang dikerjakan agen lain; perbaikan lokal CLI sudah
   ada (`cli/puspa_cli/agent.py:460-464`, `unregister` tool di luar
   token). Ini BUKAN masalah reuse — tapi dicatat karena berkaitan
   dengan pembungkusan nanobot.
5. **Tidak ada pelanggaran lisensi ditemukan.** Tidak ada kode
   GPL/AGPL/SSPL/PolyForm/CC BY-NC di pohon Puspa. dusk.bot
   (PolyForm) hanya dipelajari desainnya. Tidak ada fork GitHub.
