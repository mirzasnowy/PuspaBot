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

## Brain phase (Fase Otak)

One new dependency: `fastembed==0.9.1` (MIT) - local embedding
model for the learning loop and memory retrieval. It is OPTIONAL:
`puspa/memory/embeddings.py` loads it lazily and falls back to the
built-in `HashEmbedding` when fastembed or the model is unavailable.
The model is BAAI/bge-small-en-v1.5, dimension 384, the same as
`EMBEDDING_DIM`, so it is a drop-in replacement with no schema
migration. fastembed 0.9.1 requires `loguru<0.8`, so `loguru` was
downgraded from 0.8.0 to `0.7.3` in `requirements.txt` (which still
satisfies nanobot-ai's `loguru>=0.7.3`). `pip check` after the
change reports only the pre-existing conflict `sse-starlette 3.7.0
requires starlette>=0.49.1, but starlette 0.46.2 is installed`.
The MCP SDK (`mcp==1.30.0`, MIT) was already installed as a nanobot
dependency; nothing was installed for the planner workstream.
Everything else in this phase adapts only PATTERNS (rewritten, not
copied). Full per-workstream notes stay as archive in
`docs/reuse-fase-otak-world.md`, `docs/reuse-fase-otak-harness.md`,
`docs/reuse-fase-otak-learning.md`, and
`docs/reuse-fase-otak-planner.md`.

### World model (`puspa/world/`)

| Big idea | From which repo (license) | What was adapted | What was rewritten from scratch |
| --- | --- | --- | --- |
| Memory stream plus reflection into higher level inferences plus plans retrieved and revised | joonspk-research/generative_agents (MIT) | The shape only: observations feed a structured model, a missed prediction revises the model, plans are simulated against the model before acting | `puspa/world/model.py`: entities plus relations plus learned price and bill beliefs as plain in-memory data; `predict` before acting; `update` corrects on a miss |
| Automatic curriculum plus self-verification plus a growing skill library | MineDojo/Voyager (MIT) | The self-verification shape only: every prediction is checked against the observation that follows it, and a miss corrects the model and is reported onward | `puspa/world/model.py`: prediction scoring, misprediction records, trend adoption only after a repeated daily move; `puspa/world/simulator.py`: deterministic bill plus price watch world as the verification ground |
| Learning loop as the consumer of mispredictions | NousResearch/hermes-agent (MIT) | The idea that a miss is learning material, not just an error log | The seam in `puspa/world/model.py`: lazy import of `record_misprediction` from `puspa/memory/learning_loop` inside `_report_misprediction`, record dict with keys `task`, `predicted`, `observed`, `error`, `context`; best effort, never raises, model works without the loop module |

Evidence (deterministic simulated world, 3 episodes, 17 scored
predictions each): episode 1 accuracy 0.647 (11 of 17, flat-price
predictions miss both drifting stores until the daily trend is
confirmed after 3 repeated moves); episode 2 accuracy 1.000 (17 of
17, learned daily drifts predict every price exactly); episode 3
accuracy 1.000 (17 of 17, learning persists across episodes).
Test: `test_prediction_accuracy_improves_across_episodes` in
`tests/test_world_model.py` (13 tests).

### Coding harness (`puspa/harness/`)

| Big idea | From which repo (license) | What was adapted | What was rewritten from scratch |
| --- | --- | --- | --- |
| Coding agent loop: write code, run tests, read the failure, fix it, repeat until green or a limit | anomalyco/opencode (MIT) | The loop shape itself: attempt, execute, grade by test exit code, feed the failure back to the next attempt | `puspa/harness/loop.py`: `CodingHarness.run()`, `AttemptRecord` (code plus its test result per attempt), `default_verdict()` (grades the exit code only, never the agent's words) |
| Per-tool permission model: allow / ask / deny per tool, not one blanket allow per turn | anomalyco/opencode (MIT) | The three levels and the rule that a child token may only narrow a permission, never widen it | `puspa/policy/tokens.py`: optional `tool_permissions` map on the token payload, `tool_permission()`, `is_subset()` extended with the deny < ask < allow ordering; `puspa/policy/gate.py`: `Gate.authorize_tool()` |
| Per-tool permission model and approval before acting | anthropics/claude-code (PROPRIETARY, DO NOT USE ITS CODE) | The idea only: permissions per tool, and a hard approval step before a consequential action (here: deploy) | Same Puspa code as the opencode row above, plus kind `"deploy"` in `puspa/policy/gate.py` and `CodingHarness.request_deploy()` / `deploy()` (approval, then exactly-once execution through the gate) |
| Harness shape: fixed FAIL_TO_PASS tests decide pass/fail; a task is an issue plus a repo snapshot plus its tests | SWE-bench/SWE-bench (MIT) | The task shape: fixed acceptance tests carried by the task, graded by running them | `puspa/harness/loop.py`: `CodingTask` (description plus fixed `test_code`), pass defined as the generated program (implementation plus those tests) exiting 0; no PASS_TO_PASS set yet, that is future work for the self-develop module evaluation |
| Self-verification: generated code is trusted only after it is actually run | MineDojo/Voyager (MIT, dormant research archive, DO NOT USE AS A DEPENDENCY) | The self-verification step only: test generated code by running it in the sandbox before trusting it | `puspa/harness/executor.py`: `SandboxExecutor` runs every attempt through `puspa/sandbox/runner.py`; the Minecraft code, the curriculum, and the skill library are NOT part of this workstream |
| Readable handoffs between steps: each step leaves a record the next step can read | OpenBMB/ChatDev (Apache-2.0) | The handoff idea: every attempt leaves its code and its test result behind, so the next attempt (and a human reader) can see exactly what failed | `puspa/harness/loop.py`: `AttemptRecord` plus the in-memory event log (`harness_started`, `attempt_started`, `attempt_finished`, `harness_passed`, `harness_failed`, `budget_exhausted`, `sandbox_unavailable`, `deploy_requested`) |
| Fail-closed sandbox execution: no host fallback, ever | puspa/sandbox/runner.py (Puspa's own, Fase 0) | The existing fail-closed contract: `SandboxUnavailable` means nothing runs anywhere | `puspa/harness/executor.py` and `loop.py`: `SandboxUnavailable` ends the run with status `sandbox_unavailable`; `production=True` rejects any executor that is not a `SandboxExecutor`; `create_production_harness()` can only build a sandbox executor |

Environment note (honest limitation): on this dev VM the real
Docker sandbox cannot start (the kernel rejects the `setns`
syscall, so `runc` fails and `Sandbox.check_available()` returns
False). The production path is therefore fail-closed here:
`SandboxExecutor.run()` raises `SandboxUnavailable` and no
generated code runs anywhere. The end-to-end loop test
(`test_e2e_loop_to_green`) runs on the test-only
`LocalTestExecutor`, which executes the generated code in a temp
directory on the host with a scrubbed environment. That executor
is test-only by construction (rejected when `production=True`,
unreachable from `create_production_harness()`), and it is NOT a
production bypass: the production path stays sandbox-only and
fail-closed. A real end-to-end sandbox run still needs a host with
working Docker (for example the Contabo VPS), and has not been done
yet.

### Learning loop + memory hardening (`puspa/memory/`)

| Big idea | From which repo (license) | What was adapted | What was rewritten from scratch |
| --- | --- | --- | --- |
| Skills created and repaired automatically; persistent user model; learning loop | NousResearch/hermes-agent (MIT) | The idea of a learning loop that drafts skills from successful tasks | `puspa/memory/learning_loop.py`: curriculum, experience log, correction table, skill library; every learned skill stays inactive until the user approves it; learning never overwrites a manual user edit |
| Automatic curriculum, self-verification, skill library of tested executable code indexed by embeddings | MineDojo/Voyager (MIT, dormant research archive) | The shape only: curriculum proposes the next task, verification tests generated code, successful code is stored with an embedding key | `learning_loop.py` (`propose_next_task`, `verify_skill`, `find_reusable_skill`) plus draft persistence in `puspa/memory/procedural.py` under the `learned_draft:` service prefix; the Minecraft code is not reusable and was not used |
| Self-improvement from experience | letta-ai/letta-code (Apache-2.0) | The idea of improving procedures from experience | Decay in `procedural.py` (`apply_decay`, `decay_stale_procedures`); contradiction handling (`detect_contradiction`, `propose_conflicting_procedure`, `resolve_contradiction`). A demotion rule (one failure drops a verified procedure to trial) was tried in this phase and removed during integration because it broke the revision 7 canary contract (see `procedural.py`). |
| Reflection turns episodes into higher-level inferences | joonspk-research/generative_agents (MIT) | The reflection step as an idea: episodes become inferences | `puspa/memory/consolidate.py` (`reflect_episodes`, `reflect_and_propose`): deterministic, keyword-based inferences routed through Proposals in `puspa/memory/profile.py`, never written to the profile silently; each inference carries its evidence event ids and needs at least 2 |
| Dual-level retrieval: specific entities plus abstract concepts, over a graph plus vectors | HKUDS/LightRAG (MIT) | The dual-level retrieval idea (entity level + concept level + vector similarity) | `puspa/memory/episodic.py` (`extract_entities`, `extract_concepts`, `dual_level_search`, `search_episodes_dual`) and `puspa/graphs/identity.py` (`rank_products`, `search_products_dual`) on top of the existing Postgres + pgvector store; the library was not installed |
| Temporal facts; procedures as templates without personal data | getzep/graphiti (Apache-2.0) | Already the pattern behind `profile.py` and `procedural.py` | Decay reuses the existing trial/verified/broken statuses, so no schema change was needed |
| Memory extraction with provenance; inferred facts held for confirmation | mem0ai/mem0 (Apache-2.0) | Already the pattern behind `profile.py` Proposals | Reflection proposals reuse `profile.add_fact` with source `inferred` (status `proposed`) |

Approval gap, recorded honestly: `puspa/policy/gate.py` has no
kind for skill activation (its kinds cover outbound actions only)
and that file must not be modified by this phase. Skill activation
therefore reuses an approved Gate request record:
`LearningLoop.activate_skill` verifies through the Gate's stored
requests that an approval for the user exists with status
`approved` or `executed`. The caller obtains that approval through
the Gate with the activation described in the request content. A
dedicated Gate kind for skill activation would need a change owned
by the gate workstream.

Database coverage: the DB-backed wrappers (`decay_stale_procedures`,
`propose_conflicting_procedure`, `search_episodes_dual`,
`search_products_dual`, `consolidate_idle` with `reflect=True`) are
covered against Postgres by `tests/test_memory_brain_db.py` (11
tests, added during integration).

### Planner + evaluation + skills/hooks (`puspa/runtime/planner.py`, `puspa/evidence/eval_harness.py`, `puspa/memory/optimizer.py`)

| Big idea | From which repo (license) | What was adapted | What was rewritten from scratch |
| --- | --- | --- | --- |
| Goal to plan to tool loop with replanning on failure | FoundationAgents/OpenManus (MIT) and the classic AutoGPT loop (MIT core) | The loop shape: decompose, execute step by step, feed the failure back into a revised plan, with a hard cap on replans | `puspa/runtime/planner.py`: keyword decomposition into `Step` objects, `Planner.run()` driving the existing `TaskRunner`, retry-then-drop replan strategy, `max_replans` default 2 |
| Stateful orchestration with checkpoints and resume | langchain-ai/langgraph (MIT) | The idea of a resumable, checkpointed run only | Nothing from the library: the no-LangGraph decision stands (zero LangGraph imports). Checkpointing here is the append-only ledger plus the replan record, not a graph runtime |
| Task, solver, scorer structure; multi-turn tool use; sandboxed execution; scoring from evidence, not words | UKGovernmentBEIS/inspect_ai (MIT) | The task/solver/scorer shape and the rule that the scorer checks a record, not the agent's words | `puspa/evidence/eval_harness.py`: `EvalTask` (versioned, pinned cassette, expected state, policy), `cassette_solver` (replays recordings), `score_state` (final-state comparison with per-key details) |
| Simulated user plus domain policy plus final database state decides success | sierra-research/tau-bench (MIT) | The simulated-user shape and the final-state check; the policy the agent must not break | `puspa/evidence/eval_harness.py`: `cassette_solver` accepts a simulated-user callable, `derive_expected_state` builds the goal state from the cassette by code, the policy list travels on every task |
| Versioned task configs with a pinned prompt and scorer, so regression runs are reproducible | EleutherAI/lm-evaluation-harness (MIT) | The versioned-config shape only | `puspa/evidence/eval_harness.py`: `HARNESS_VERSION = "1.0.0"` pinned on every report, one `EvalTask` per reference cassette, `EvalReport.summary()` computed by code |
| Programs, not prompts: declarative modules tuned by an optimiser against a metric on a fixed dataset | stanfordnlp/dspy (MIT) | The program-plus-optimiser shape and the rule that the optimiser tunes prompts, never security rules | `puspa/memory/optimizer.py`: `PromptProgram` (planner/reader/checker templates plus tunable knobs), `Optimizer.optimize()` scoring deterministic variants against the 10 cassettes, `plan_coverage` as the metric, hard refusal of any gate target |
| Type-safe structured output: numbers filled by code from evidence, never typed by the model | pydantic/pydantic-ai (MIT) | The type-safe tool and structured-output pattern | `puspa/chassis/tools.py`: `McpToolSpec` as a pydantic model validated at registration; `puspa/evidence/eval_harness.py`: `render_report` fills markers through the existing `puspa/evidence/renderer.py`, and all report numbers are computed by code |
| Official MCP client: the standard way tools are exposed to agents | modelcontextprotocol/python-sdk (MIT) | The client shape: list tools, call a tool with typed arguments, over stdio | `puspa/chassis/tools.py`: `McpClientWrapper` plus `StdioMcpTransport` (official SDK `ClientSession` + `stdio_client`, installed already as `mcp==1.30.0`, MIT, verified from distribution metadata, no install needed) plus `StubMcpTransport` for offline tests. The signed-skill gate and the outbound gate sit in front of every call; the SDK is never given an unguarded path |
| Skill collection plus hooks: a hook point fires when a skill is installed or used | affaan-m/ECC (MIT) | The hook-point idea only | `puspa/chassis/hooks.py`: `SkillHookRegistry` (register, emit, unregister, broken-listener isolation), hook points for install, use, approve, reject, block. Enforcement stays in the signed-skill gate and the outbound gate, never in the hooks |
| Learning loop: skills created from successful tasks, kept inactive until the user approves | NousResearch/hermes-agent (MIT) and MineDojo/Voyager (MIT, dormant research archive) | The gated-learning idea: a learned skill is a draft until approved, and learning never overwrites manual edits | `puspa/chassis/hooks.py`: `LearnedSkill` (`is_active` is False unless status is `approved`), `LearnedSkillStore` (read-only view; `from_procedures` maps `trial` to pending approval and `verified` to approved; no write methods exist), `check_skill_use()` (emits `skill_blocked` for unapproved skills). The learning loop itself (`puspa/memory/learning_loop.py`) is built by the learning workstream; this module only reads its draft store |
| Model-driven agent loop with Cedar-style approval policies | strands-agents/harness-sdk (Apache-2.0) | The approval-policy idea only | No SDK adopted. The approval policy already exists as `puspa/policy/gate.py`; the MCP wrapper routes outbound kinds through it, and the planner never replans around it |

Evidence: reference evaluation through the new harness: 10/10
reference tasks passed (score 1.0); price-injection test passed
(tampered price Rp1.000 against the true cheapest Rp11.999.000,
verifier verdict Uncertain, not Verified). Optimiser on the 10
cassettes: planner program baseline score 1.0 (all 10 reference
tasks covered); best variant equals the baseline, so no prompt
change was adopted.

**Not used in the Brain phase (recorded so it is not repeated):**
- No code was copied from any repo above. No fork was created.
  Voyager is dormant as a research archive and its Minecraft code
  is not reusable; only its self-verification pattern was studied.
- claude code source code (proprietary, all rights reserved):
  pattern study only, from public docs. The leaked March 2026
  source-map code was never looked at and never used.
- opencode as a chassis or dependency (TypeScript coding agent, no
  channels, memory, cron, or per-bot computer): pattern only.
- Kilo-Org/kilocode (MIT): its modes and agents plus MCP pattern
  was studied for `puspa/runtime/subagents.py`, but no mode system
  was built in the harness workstream; the harness loop has no
  modes. That row stays open for the sub-agents workstream.
- letta-ai/letta-code (Apache-2.0): its self-improvement-from-
  experience loop was studied, but no learning loop was built in
  the harness workstream; the harness only verifies code.
- SWE-bench as a benchmark or dependency: only its task shape was
  adapted. No SWE-bench dataset, instance, or score is used, and no
  score is claimed.
- DSPy, inspect_ai, tau-bench, and lm-evaluation-harness as
  libraries (not installed): only their program-plus-optimiser,
  task/solver/scorer, simulated-user, and versioned-config shapes
  were adapted.
- langgraph / langchain as libraries (not installed, by explicit
  decision): only the checkpointed-orchestration idea was noted.
- strands-agents SDK (not installed): only its approval-policy idea
  was noted; Puspa's own gate already implements it.
- LightRAG as a library (not installed): only the dual-level
  retrieval idea was adapted on top of Postgres + pgvector.
- No GPL, AGPL, SSPL, PolyForm, or CC BY-NC code was used.

### Brain deepening, rework 1 follow-up: bge reranker swap (18 Nov 2026)

One new dependency: `FlagEmbedding==1.4.2` (MIT, verified
from its GitHub repo LICENSE file, FlagOpen/FlagEmbedding)
plus its direct deps (`datasets==5.2.0`,
`accelerate==1.16.0`, `peft==0.21.3`, `ir-datasets==0.6.3`,
`sentencepiece==0.2.3`) and the transitive ones datasets pulls
in (`pyarrow==26.0.1`, `pandas==3.1.1`, `dill==0.4.1`,
`multiprocess==0.70.19`, `xxhash==4.1.0`, `lz4==4.4.5`,
`psutil==7.2.2`), all pinned in `requirements.txt`. The model
is `BAAI/bge-reranker-v2-m3` (Apache-2.0, verified from the
HuggingFace API tag `license:apache-2.0` on 18 Nov 2026),
multilingual, loaded through `FlagReranker.compute_score` in
`CrossEncoderReranker.from_flagembedding`; `make_reranker`
takes `backend="bge"` (also spelled `"flagembedding"`) and
`"bge"` is now the default cross-encoder backend.

Measured on this dev VM, 18 Nov 2026 (total RAM 7,934 MB):
weights file 2,271,071,852 bytes (fp32); load about 23-28 s;
peak RSS 2,148 MB for one scored pair; a 4-pair batch scores
in about 4-7 s on CPU. English price query: answer 4.63,
stuffing 1.62, generic -1.58, off-topic -10.77. Indonesian
price query: answer 4.30, stuffing 0.75, generic -1.43,
off-topic -11.00. Scores deterministic (exact equality on
repeat calls); batch scores match single-pair scores within
1e-4 (FlagEmbedding sorts pairs by length before batching, so
padding differs and the last float bits differ). Free RAM on
this shared VM fluctuates (about 0.5-3.0 GB observed while
working); when about 2.2 GB is not free the load fails with
`RerankerUnavailable` and the caller falls back to the small
MS MARCO backend or the deterministic reranker.

Honest limit, locked in as a test: the swap does NOT close the
Wave 1 multi-hop recall gap. For a trust question the fraud
report ranks last in both languages (English stuffed -0.27,
answer -4.18, fraud -9.40; Indonesian stuffed -1.28, answer
-4.47, fraud -8.73), same as the MS MARCO model: a reranker
judges answer-relevance, not trust evidence. No code was
copied from FlagEmbedding or BAAI; only the public
`FlagReranker` / `compute_score` API was used.

## Maturity phase (G11 — multi-tenant hosting auth/relay/pairing)

No new dependency. Only PATTERNS adapted (rewritten, not copied):

| Big idea | From which repo (license) | What was adapted | What was rewritten from scratch |
| --- | --- | --- | --- |
| Loopback trust only when Host + Origin are loopback; a browser never gets it; `service` mode restricts sessionless loopback to an explicit allowlist | DuskBot (PolyForm Noncommercial — study only, no code used) | The trust rule + the explicit (method, path regex) allowlist shape | `puspa/hosting/loopback.py` (loopback parsing, browser markers `Sec-Fetch-Mode` / `Mozilla/` UA) + `puspa/hosting/service_allow.py` (`re.fullmatch` allowlist, default 3 read-only endpoints) + `puspa/hosting/gateway.py` (`authorize_request`: Google session → pairing session → loopback/service-allow → deny) |
| Pairing: short code, 30-day session, 5 wrong → 10-minute address lock | DuskBot (PolyForm Noncommercial — study only, no code used) | The code/session/lockout numbers | `puspa/hosting/pairing.py`: 6-digit code, 5-minute TTL, single-use, SHA-256 hashes only, 30-day session token, per-address lockout; in-memory store |
| Card seal: per-start sealing key never written to disk, each seal signed by the long-term identity key pinned by the app | DuskBot (PolyForm Noncommercial — study only, no code used) | The seal shape (ephemeral sealing key + identity signature + pinning) | `puspa/hosting/identity.py` (Ed25519 identity key, file mode 0600, backup-excluded) + `puspa/hosting/seal.py` (HPKE `DHKEM(X25519)` + `HKDF-SHA256` + `AES-256-GCM` via pinned `cryptography==50.0.2`; no fallback needed — HPKE is available) |
| Link/relay: 7-character code without l/o/0/1, rate limits, AES-256-GCM relay frames, fallback direct → tunnel → relay | DuskBot (PolyForm Noncommercial — study only, no code used) | The code alphabet, the rate-limit numbers, the frame shape, the fallback order | `puspa/hosting/link.py` (7-char codes, 30/hour per address + 2,000/hour global) + `puspa/hosting/relay.py` (AES-256-GCM frames, 1 MiB cap, in-memory `RelayHub` holding no keys) + `puspa/hosting/fallback.py` (fixed order, injected probes, attempt accounting) |

Google login (`puspa.auth`) stays in front; this pattern is the
server-to-server path behind it. No DuskBot code was copied (its
code is TypeScript; Puspa's is Python) and no DuskBot code is
vendored or depended on.

## Maturity phase (G12 — Sentinel + decision log)

No new dependency. Only PATTERNS adapted (rewritten, not copied):

| Big idea | From which repo (license) | What was adapted | What was rewritten from scratch |
| --- | --- | --- | --- |
| Periodic self-scanner over the server's own records with 5 detectors (toolError, turnStop, queueWait, cost, retryLoop) + default thresholds (queue 5 min, cost $5, retry min. 3, lookback 7 days, rescan 30 min) + state file (readState/readIssues/markCounted/upsertIssues/pruneSeen/writeSentinelFiles) | DuskBot (PolyForm Noncommercial — study only, no code used) | The five detector names, the default numbers, the state-file function shape | `puspa/sentinel/detectors.py` (pure detectors over ledger events: `tool_completed` status, `task_stopped`/stall, `trigger_fired`→`task_created` wait, summed `cost_rp`, consecutive failed tool calls) + `puspa/sentinel/thresholds.py` + `puspa/sentinel/state.py` (atomic JSON state, open/acknowledged/resolved, prune rules) + `puspa/sentinel/scanner.py` (rescan gate, auto-resolve of cleared conditions, scheduler entry `run_sentinel_scan`) |
| Decision log: every decision recorded (actor, source, via) into daily files, 180-day retention | DuskBot (PolyForm Noncommercial — study only, no code used) | The daily-file shape + the 180-day retention number | `puspa/sentinel/decision_log.py` (`DailyDecisionLog`: JSONL daily files, SHA-256 entry hash over the decision without the timestamp, `prune` touches only `decisions-*.jsonl`) |
| Usage traces as span trees; anonymous analytics + crash reports, switchable off | DuskBot (PolyForm Noncommercial — study only, no code used) | The span-tree idea + the analytics off-switch | `puspa/sentinel/traces.py` (span tree from ledger events, span ids reuse event ids, unpaired calls stay visible) + `puspa/sentinel/analytics.py` (snake_case counter names only, crash reports without the exception message, no network sender at all) |
| Batch trajectory runner over a prompt dataset (parallel runs, per-batch output, checkpoint + resume, aggregate tool usage) + trajectory storage with redaction before saving + ShareGPT trajectory format | NousResearch/hermes-agent (MIT) | The batch-runner shape (checkpoint = output file, resume skips done tasks), the redact-before-save rule, the ShareGPT mapping | `puspa/sentinel/trajectory.py` (runs on top of the existing `puspa/evidence/eval_harness.py`, not a replacement; `record_prompt_run` for bare-prompt agent runs; redaction via G7 `puspa.memory.trust.redact_secrets`) + `docs/trajectory-format.md` |
| Session insights from `state.db`: tokens, cost estimate (sub-cent labels at 4 decimals, never `~$0.00`), tool/skill usage, activity, model/platform breakdown | NousResearch/hermes-agent (MIT) | The insight categories + the 4-decimal sub-cent label rule | `puspa/sentinel/insights.py` (ledger events + G26 `state.db` read-only; tokens via G19 `count_tokens`; cost via the router catalog or recorded `cost_rp`; nothing invented when data is absent) |
| Independent verifier as the detectors' eyes, kept in a separate module so the two evolve independently (K8 fold) | Puspa's own K8 design (verifier pattern from the maturity report) | — | `puspa/sentinel/verifier.py` (`confirmed` / `stale` / `duplicate` / `unverifiable`, fail-closed, never writes) |
| Verification discipline as a written rule (isolated fixtures, never verify a mutation on live user data) | DuskBot `docs/verification/` (PolyForm Noncommercial — study only) | The rule itself (already written by G15 in `docs/verification.md`) | Sentinel-specific section added to `docs/verification.md` ("Sentinel self-checks": fresh-read re-verification, synthetic-event tests, read-only ledger access, own home directory for writes) |

No DuskBot code was copied (its code is TypeScript; Puspa's is
Python) and no DuskBot code is vendored or depended on. No
hermes-agent code was copied; only the documented shapes above
were followed. New wiring: `Ledger.recent_events` (read-only
window query, no new event types) in `puspa/ledger/ledger.py`
and the `sentinel_scan` trigger type + `schedule_sentinel_scan`
+ dispatch in `puspa/scheduler/scheduler.py`.

## Maturity phase (G1 — full PWA)

Only PATTERNS adapted (rewritten, not copied). New npm dependencies
(all MIT/Apache-2.0, see the table; no dependency with a
non-commercial or copyleft license):

| Big idea | From which repo (license) | What was adapted | What was rewritten from scratch |
| --- | --- | --- | --- |
| API-first: OpenAPI spec first, TypeScript client generated from it — frontend never hand-writes the API client | OpenBot (MIT) | The contract-first order + the generated-client rule | `docs/openapi-pwa.yaml` (contract for every PWA route) + `apps/pwa/src/api/generated-types.ts` (generated with openapi-typescript 7.13.0 — never edited by hand) + `apps/pwa/src/api/client.ts` (thin hand-written fetch wrapper typed against the generated types) |
| 8 product primitives as the consistent menu structure | OpenBot (MIT) | The primitives-as-menu idea | Revision 7's own 7 screens as the menu (`apps/pwa/src/api/bootstrap.ts` `SCREENS`, `Nav.tsx`) — no 8th screen |
| Split components / lib / per-feature state; one-tap approval cards | DuskBot (PolyForm Noncommercial — study only, no code used) | The `components/` / `lib/` / `state/` split + the one-tap card shape | `apps/pwa/src/components/` (ApprovalCard, Nav, WorkStrip) + `apps/pwa/src/state/` (one store file per screen: chat, tasks, approvals, memory, connectors, office, modules) + `apps/pwa/src/api/` (client, bootstrap) |
| 26-page React+Vite dashboard as a page checklist | NousResearch/hermes-agent (MIT) | The page list as a checklist | hermes pages folded into the 7 screens as sections, not new screens: Channels + Cron + Skills + Sessions + Analytics checklist in `docs/pwa.md` (Channels in Connectors, Cron note in Tasks, Skills note in Modules, Sessions/Analytics live in Office via SSE) |

New npm dependencies (licenses read from each package's own
`package.json` in `node_modules/`): `react@19.3.1` (MIT),
`react-dom@19.3.1` (MIT), `vite@6.4.6` (MIT),
`@vitejs/plugin-react@4.7.0` (MIT), `typescript@5.9.3`
(Apache-2.0), `vitest@3.2.7` (MIT), `jsdom@26.1.0` (MIT),
`@testing-library/react@16.3.3` (MIT),
`@testing-library/user-event@14.6.12` (MIT),
`openapi-typescript@7.13.0` (MIT), `@types/react@19.3.0` (MIT),
`@types/react-dom@19.3.0` (MIT). No OpenBot, DuskBot, or
hermes-agent code was copied, vendored, or depended on. Server
wiring: `puspa/pwa/app.py` serves the built SPA (`apps/pwa/dist`)
with a server bootstrap (`window.__PUSPA_BOOTSTRAP__`) and falls
back to the Jinja templates when `dist` has not been built; no API
route or auth behaviour was changed. Note: `node_modules/` and
`dist/` are build outputs on disk and are not part of the pushed
sources — a fresh checkout must run `npm install && npm run build`
in `apps/pwa/` before the SPA is served (see `docs/pwa.md`).

## Maturity phase (G13 — multi-tenant teams / hosting)

No new dependency. Only PATTERNS adapted (rewritten, not copied):

| Big idea | From which repo (license) | What was adapted | What was rewritten from scratch |
| --- | --- | --- | --- |
| Team as its own entity, separate from billing; owner/member roles; invitations via token; visibility as a function of (thread, member); per-member bot access with access requests; workspace moves with move-freeze | DuskBot (PolyForm Noncommercial — study only, no code used) | The team entity split from billing, the two-role shape, the invitation-token shape, the per-(thread, member) visibility rule, the bot-access-gate + access-request shape, the move-freeze rule | `puspa/teams/teams.py` (create/delete, owner-only role changes, last-owner guard) + `puspa/teams/invitations.py` (hash-only tokens, 7-day TTL, single use, idempotent accept, revoke) + `puspa/teams/visibility.py` (`can_see_thread` pure function + `BotAccessGate` grants/requests) + `puspa/teams/moves.py` (pending/in_progress/done/failed/cancelled, freeze derived from move records, failed stays frozen and resumable) |
| Signed entitlement per machine-id as the "may this home run" gate; without a valid entitlement only a signed admin + the liveness probe are answered; public key pinned in the release | DuskBot (PolyForm Noncommercial — study only, no code used) | The signed-entitlement gate shape + the pinned-key idea | `puspa/teams/entitlements.py` (Ed25519-signed `v1.<payload>.<signature>` tokens, canonical JSON payload, tier list from G2 billing tiers + `admin`, `HomeGate.decide` with plain deny reasons; Ed25519 — not G2's HMAC — because the home must not hold the server secret) |
| Organisations, roles, org audit | OpenBot (MIT) | The organisation/role/audit idea | Team roles + the durable `bot_access_requests` audit table (`puspa/teams/visibility.py` + `infra/schema_teams.sql`); no org-audit HTTP surface yet |
| Deploy: multi-stage Dockerfile, s6-overlay as PID 1, UID/GID remap, dashboard bound to 127.0.0.1 by default, remote access only via SSH tunnel or authenticated reverse proxy, API server dead unless its key is uncommented; gateway + dashboard in compose | NousResearch/hermes-agent (MIT) | The deploy shape (s6-overlay, localhost default, UID remap, API key opt-in, gateway + dashboard services) | `Dockerfile` (builder + runtime stages, s6-overlay 3.2.3.2, cont-init home fix, one s6 service dropping to the puspa user) + `docker-compose.yml` (gateway on 127.0.0.1:8001, dashboard on 127.0.0.1:8000, shared `puspa-home` volume) + `puspa/teams/deploy.py` (offline parse + rule validation — Docker cannot run on the dev VM, so the files are validated by parsing, not by building) |

No DuskBot code was copied (its code is TypeScript; Puspa's is
Python) and no DuskBot code is vendored or depended on. No
hermes-agent or OpenBot code was copied; only the documented shapes
above were followed. Schema: `infra/schema_teams.sql` (seven tables
+ RLS policies following `puspa/accounts/schema.sql`); docs:
`docs/teams.md`.

## Maturity phase (G14 — Telegram / WhatsApp channels)

No new dependency. Only PATTERNS adapted (rewritten, not copied):

| Big idea | From which repo (license) | What was adapted | What was rewritten from scratch |
| --- | --- | --- | --- |
| One core, many adapters: platform events normalised to `MessageEvent`, then the same flow (authorise -> resolve session key -> agent -> reply through the origin adapter); `GatewayRunner` facade + one module per topic (inbound, delivery, notification, heartbeat); adapter guide `ADDING_A_PLATFORM.md` | NousResearch/hermes-agent (MIT) | The gateway shape (runner facade, per-topic sibling modules, normalised event, per-platform adapter), the delivery-ledger + bot-loop guard + heartbeat/restore ideas, the DM-pairing-as-authorisation idea, the single-writer gateway profile rule | `puspa/channels/` (16 modules: `events.py`, `base.py`, `telegram.py`, `whatsapp.py`, `authz.py`, `ledger.py`, `inbound.py`, `delivery.py`, `agent.py`, `notify.py`, `heartbeat.py`, `run.py`, `config.py`, `schema.py`, `errors.py`, `__init__.py`) + `infra/schema_channels.sql` + `docs/channels.md` + `docs/adding-a-platform.md`; Telegram adapter first (raw Bot API over pinned `httpx`, long polling + webhook), WhatsApp second (raw Cloud API over pinned `httpx`, webhook handshake + HMAC signature); own `channel_deliveries` table (not the core ledger — see `docs/channels.md`) |
| Notifications carry only the bot name, never message content; pairing via short letter code | DuskBot (PolyForm Noncommercial — study only, no code used) | The content-free notification rule and the short-code pairing shape | `puspa/channels/notify.py` (notification text is the bot name only) + `puspa/channels/authz.py` (`ChannelPairing`: 6-digit codes, hash-only, 5-minute TTL, single use, per-chat lockout) |
| Multi-platform channel surface (Telegram, WhatsApp, …) as access channels, not the home | OpenBot (MIT) | The channel-as-access-surface idea (the home stays the PWA) | The channel package itself; no OpenBot code or dependency |

No DuskBot code was copied (its code is TypeScript; Puspa's is
Python) and no DuskBot code is vendored or depended on. No
hermes-agent or OpenBot code was copied; only the documented shapes
above were followed. No new pip dependency was added for G14
(`httpx==0.28.1`, already pinned). Schema:
`infra/schema_channels.sql` (one table, `channel_deliveries`); docs:
`docs/channels.md`, `docs/adding-a-platform.md`. Honest limit: no
live bot was verified (no real token, no real message sent); all
HTTP in tests goes through `httpx.MockTransport`.

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
