# Naming Audit — PuspaBot Phase 0

Date: 17 November 2026. Auditor: Luna (naming-audit subagent).
Nero's order: make sure the naming of files, README, variables, and
everything else is all best practice, and DON'T invent terms of your own.

Glossary reference: `~/workspace/match-hunt/definisi-mvp-puspa-2026-11-17-revisi-7.md`
(revision 7, APPROVED). The definition and checklist were NOT changed — only referenced.
Rules: PEP 8; Indonesian domain terms must be in revision 7; one concept =
one name in all files.

## Summary

- Raw findings from three inventories (puspa package, tests+demo, documents+SQL):
  **127 findings** (18 documents+SQL, 45 puspa package, 64 tests+demo), with
  overlaps between reports (the same typo and test count found by two
  reports). Each finding was verified one by one before a decision.
- **Fixed directly: 47** (typos, document wording, comments,
  docstrings, internal variable/constant/function renames — all verified
  by pytest + demo dry-run, see § Evidence).
- **Rename proposals awaiting Nero's decision: 22 items** (proposals 1 to 22;
  each item bundles several raw findings) — modules/files/public APIs/
  table names/event names are not changed without approval.
- **Rejected after verification (not findings): 8** (auditor miscount,
  wrong line reference, or a pattern that is deliberate).
- **Accepted as deliberate patterns: 4** (Indonesian prose + English
  identifiers; `isi` vs `content` in different domains; the double
  spelling of `sub_agen` coming from revision 7; `CannedProvider` as a demo test double).
- The remaining raw findings (mainly test names and JSON keys) are
  bundled into the proposal items — all waiting on one big decision
  first: the identifier language policy (see § Decisions needed).

## Decisions needed (most important)

1. **Identifier language policy.** The code now mixes Indonesian and
   English on three layers: public names (`append_event`, `Ledger`, `Gate`),
   domain names (`tugas_id`, `kejadian`, `rupiah_max`), and test names
   (`test_..._gagal_jujur`). Without a written policy, a partial rename
   only adds a third variant. Proposal: **code identifiers = English**
   (standard technical concepts + public names), **stored domain values**
   (event types, SQL columns, statuses) **stay Indonesian per revision 7**,
   **prose (docstrings/comments/documents) = Indonesian**. This decision
   unblocks the proposal items below.

   > [Superseded 18 Nov 2026: Nero ordered an English-only refactor —
   > code identifiers, stored domain values, and prose are all English
   > now; see `docs/GLOSSARY.md`.]
2. **Big public renames** (see § Proposals, proposals 1 to 12): duplicate
   `canonical_json`, the `EVENT_TYPES` alias, `append_event`/`events`, two
   `skills.py`, two `session` modules, and mixed-language SQL names. All need
   Nero's word because they touch the public API / the already-pushed repo.
3. **Mock `baca_file` vs the real tool `read_file`** (finding 11): the mock harness
   uses a dummy tool `baca_file` while the demo uses `read_file`.
   The protocol evidence stays valid (tool names in the mock are free), but the evidence
   becomes less direct by comparison. Changing it means regenerating 9
   `mock_logs/` files + updating 2 documents + tests. Waiting on Nero's word.
4. **`requirements.txt` without version pins** (observation, not a naming
   finding): a fresh-clone install may differ from the tested VM.
   Pinning is recommended at the next push.

## Already fixed

### Typos (6)

| Location | Before | After |
| --- | --- | --- |
| `demo/MATRIKS_BYOK.md:159` | `orcarouter` | `openrouter` |
| `demo/NOTES.md:220` | `Autouser` | `Autouse` |
| `demo/byok_demo.py:646` | JSON key `"diexport_pada"` | `"diekspor_pada"` (no consumer reads this key) |
| `puspa/policy/spending.py:10,20,28,93` | `instansi` (4x, docstring) | `instans` |
| `puspa/quarantine/injection.py:93` | `didedup` (docstring) | `dideduplikasi` |
| `puspa/accounts/users.py:53` | `"password kosong"` | `"kata sandi kosong"` (no test asserts this message) |

### Misused terms (3)

- `puspa/policy/gate.py:15,256` — "pemeriksa batas" (the term `Pemeriksa`
  is a subagent class in revision 7, not the spending-limit enforcer) →
  "penegak batas belanja".
- `puspa/accounts/access.py:133` — comment "Pembaca bermilik" (the term
  `Pembaca` is a subagent class) → "fungsi baca bermilik (scoped
  readers)"; the same phrase in `CONTRACTS.md:116-117` fixed along with it.
- `demo/NOTES.md:317` — status `menunggu_persetujuan` (not in the code;
  the correct gate status is `diminta`) → "(persetujuan berstatus
  `diminta`)".

### Documents (16)

- `README.md` — test count 203 → **244** + breakdown +41 BYOK tests; the
  line "push hanya setelah parent mengonfirmasi" (stale, the push already
  happened 17 Nov 2026) → commits `8e91a92` + `657ee78`; added the
  **Istilah** section (PuspaBot, MVP, Buku Kerja, Pembaca terkarantina,
  token izin, gerbang, Ed25519, Argon2id, Fernet, RLS, DSN, HKUDS, chassis).
- `CONTRACTS.md` — layout tree completed (`test_byok_provider.py`,
  `demo/`, `NOTICE.md`, `nanobot-INVENTORY.md`); the sentence "Semua tabel
  aplikasi punya `user_id`" fixed (pengguna's PK is `id`; kejadian
  uses `agen_id` + `tugas_id`); chain order `id` → `(waktu, id)`
  (matching the implementation + the existing deviation note).
- `NOTICE.md:11` — "Versi yang di-vendor" → "Versi yang dipakai sebagai
  referensi (clone lokal, belum di-vendor)" (aligned with the README).
- `STATUS.md` — line 6 annotated "(7 jalur awal; jalur ke-8 di baris
  11)" so 7 vs 8 doesn't read as a contradiction; added a paragraph on
  the suite after the BYOK generalisation (244 passed, 17 Nov 2026 ~19:33 WIB). Historical
  lines (203 passed ~18:38 WIB, 7 paths) NOT changed — correct at the time.
- `demo/MATRIKS_BYOK.md` — BYOK (Bring Your Own Key) definition + surrogate
  (`hsurr:...`) at first mention; provider naming convention (tables =
  lowercase code values; prose = standard OpenRouter form); note that
  bedrock has no mock log file (fake client, not HTTP).
- `demo/LAPORAN_DEMO.md` — BYOK definition + surrogate; title "Run terakhir
  (e3416931)" → "Run e3416931" (f1c46b9d is newer); convention "a file without
  a run-id suffix = evidence for run e3416931; other runs use the suffix `-<run_id>`".
- `demo/NOTES.md` — see typos + terms above.
- `demo/byok_provider_proof.md:72` — `get_surrogate()` → `get_openrouter_surrogate()` (the function was already renamed; references in the proof document updated along with it).

### Helper duplication (1)

- `puspa/policy/skills.py` — `_b64url_encode`/`_b64url_decode` identical
  duplicates of `puspa/policy/tokens.py` (evidence: function bodies exactly
  the same) → removed from `skills.py`, imported from `.tokens`. No public
  API change (`__all__` contains neither).

### Verified internal renames (21)

| Location | Before | After | Reason |
| --- | --- | --- | --- |
| `demo/byok_provider.py:506` | alias `build_provider = build_byok_provider` | removed | imported by no one (all imports use `build_byok_provider`); this name clashes with the demo seam in `byok_demo.py:320` |
| `demo/byok_demo.py:320,454` | `build_provider` (demo seam) | `build_demo_provider` | tell apart from the `build_byok_provider` factory |
| `demo/openrouter_provider.py:38` + `demo/byok_provider.py:263,265` | `get_surrogate` | `get_openrouter_surrogate` (imported in `byok_provider` as `_openrouter_surrogate`) | one name in both modules |
| `demo/byok_provider.py:397-493` | local `nama` | `provider_name` | English, consistent with `provider_name=` in the provider constructor |
| `demo/byok_provider.py:397-493` | local `kunci` | `key` | English, consistent with the `key` parameter |
| `demo/byok_provider.py:193-195` | `awalan, nama` (loop vars) | `prefix, provider_name` | English |
| `demo/byok_provider.py:205,217` | local `nama` in `_canonical` | `name` | match the `name` parameter |
| `demo/byok_provider.py:239,242-243` | local `nama` in `register_provider` | `canonical_name` | avoid shadowing the `name` parameter, which the error message still uses |
| `demo/byok_provider.py:307-326` | local `nama` in `resolve_key` | `provider_name` | English |
| `demo/byok_provider.py:460` | error string `register_provider(nama, base_url)` | `register_provider(name, base_url)` | the actual parameter is named `name` |
| `demo/byok_demo.py` | `PENGHITUNG` (9x) | `penghitung` | UPPER_SNAKE is wrong for a mutable object |
| `demo/byok_demo.py` | `USAGE_PERMINTAAN` (6x) | `usage_permintaan` | same |
| `demo/byok_demo.py` | `FASE0`, `DEMO_DIR` (public) | `_FASE0`, `_DEMO_DIR` | private, aligned with `byok_provider.py` + `test_byok_provider.py` |
| `demo/byok_demo.py:646` | JSON key `"diexport_pada"` (already counted in § Typos) | — | — |
| `demo/mock_provider_server.py:64-65,274,297,320,342` | `MESSAGES`, `TOOLS` | `MOCK_MESSAGES`, `MOCK_TOOLS` | descriptive; not imported by tests |
| `tests/test_quarantine.py` | `ALLOWED` | `ALLOWED_DOMAINS` | descriptive |
| `tests/conftest.py:28-30` | `_nanobot_local` | `_NANOBOT_LOCAL` | UPPER_SNAKE for a path constant; aligned with `demo/` |
| `tests/test_byok_provider.py:70,149,154` | `gagal_jujur` (3 test names) | `gagal_terlihat` | revision 7 §2.6 uses the term "gagal terlihat" for honest failure |
| `puspa/accounts/users.py:25,54,88` | `_HASHER` | `_PASSWORD_HASHER` | descriptive |
| `puspa/policy/gate.py:113,116,128,246,249,251` | parameter `void` | `invalidate` | `void` is not a word that states the meaning (cancel the approval) |
| `puspa/quarantine/injection.py:36,86` | `_POLAS` | `_PATTERNS` | English, consistent with the `nama pola` field, which stays Indonesian in the data |
| `puspa/quarantine/reader.py:29,95` | `_NUM_RE` | `_NUMBER_RE` | unclear abbreviation |

Note: the `nama`/`kunci` renames in `build_byok_provider` were done with
`tokenize` (not sed) so that prose string literals ("model = nama
deployment", "register_provider(nama, base_url)") didn't change along with them;
the diff was checked line by line before pytest.

## Rename proposals awaiting a decision

Nothing was changed — all need Nero's word because they touch the public API,
files, tables, or the already-pushed repo.

| # | Proposal | Reason | Impact |
| --- | --- | --- | --- |
| proposal 1 | Duplicate `canonical_json`: `ledger.py:84` (-> `str`) vs `tokens.py:75` (-> `bytes`) | one name, two return types; both exported in `__all__` | rename one (e.g. `canonical_json_bytes` in tokens) + 9 call sites (ledger 2, tokens 4, gate 1, skills 2) |
| proposal 2 | Remove the alias `EVENT_TYPES = JENIS_KEJADIAN` (`ledger.py:81`) | one concept, two names | 1 line + all importers of the alias |
| proposal 3 | `append_event`/`events`/`InvalidEventTypeError`/`compute_event_hash` → `tambah_kejadian`/`kejadian`/... OR the other way: unify to English | `kejadian` vs `event` mixed in the public ledger API | public ledger API + all users (chassis, tests, demo) |
| proposal 4 | `read_ledger_events_for_task` vs `revoke_all_for_tugas` | `task` vs `tugas` in public function names | 2 functions + users |
| proposal 5 | `create_agent_schema`/`register_agent` vs `agen_id` | `agent` vs `agen` in the public accounts API | 2 functions + users + SQL |
| proposal 6 | Money: `rupiah`/`nominal`/`amount`/`jumlah` (4 names) | one concept, four names | token payload, gate, spending, tests |
| proposal 7 | `KIND_TOOLS`/`KIND_SCOPES` + tool statuses (`ok`/`error` vs `ditolak`/`menunggu_persetujuan`) | mixed languages in stored values | values stored in the ledger — needs migration/compat |
| proposal 8 | Two `skills.py` (policy vs chassis) + two session modules (accounts/sessions.py vs chassis/session.py) | same file name, different contents | rename files + all imports |
| proposal 9 | Duplicate `is_allowed` (method + module function), `owner_of` vs `owner_of_token`, `validate` vs `verify` | similar names, different meanings | public policy/accounts API |
| proposal 10 | SQL: mixed columns (`vault.name`/`ciphertext` English vs `dibuat`/`diubah` Indonesian; `sesi.device_label`/`token_hash` vs `terakhir_dilihat`/`dicabut`) + the `kejadian_jenis_check` constraint | one table, two languages | SQL migration + all queries |
| proposal 11 | MD files: `nanobot-INVENTORY.md`, `demo/byok_provider_proof.md` (lowercase) vs the uppercase convention (README, STATUS, CONTRACTS, NOTICE) | file-name casing consistency | rename files + all references |
| proposal 12 | `RlsError` → `RLSError`; `_TINGKAT_RANK` (leftover — `_POLAS` and `_NUM_RE` already fixed, `void` already fixed) | PEP 8 acronyms (CapWords) | exception class + tests; `_TINGKAT_RANK` needs a decision together with the `tingkat` field |
| proposal 13 | Test names: Indonesian/English mixed across files + the `j1`-`j8` prefix + `A`/`B` in `test_attacks.py` + three wordings for the same unknown-type test | one test-naming convention | 219 test functions — wait for the language policy (§ Decision 1) |
| proposal 14 | `_payload` defined 4x with different `rupiah_max` (0 / 2,000 / 1,000,000 / 10,000,000) + duplicate helpers (`stub_log` 3x, `_token`/`_gate` 2x, `KirimTool` 2x) | duplicate fixtures, inconsistent values | test refactor (not just a rename) |
| proposal 15 | Duplicate `DEFAULT_DSN` (`ledger.py:40`, `users.py:14`) + 3 DSN names + duplicate DSN literals in tests | one constant, one place | small refactor |
| proposal 16 | `MAX_CHARS` (reader, 200) vs `_MAX_SPAN` (injection, 200) | two names for the same limit | unify the name + document it |
| proposal 17 | `spent_in_period` in `spending.py` delegates to a ledger that lacks the method (in-memory fallback via `hasattr`) | behaviour correct but architecture dangling | decide: add `spent_in_period` to Ledger, or remove the delegation |
| proposal 18 | `is_subset`, `owner_of`, `build_hook` are not exported in `__init__` even though they're used across packages | `__init__` exports don't match usage | add exports / document as private |
| proposal 19 | `turn`, `sink`/`make_ledger_sink`, `chassis`/`PuspaChassis`, `Galat` (docstring) vs `*Error` (class), `salinan`, `hilang`, `awalan`, `judul`/`angka`/`konteks`, `ringkasan`, `jalankan_kode`, `_tolak`/`alasan`/`daftar_scope`, `pola`/`tingkat`/`span` | new terms not in revision 7 and not standard technical terms | each needs: add to the revision 8 glossary, or replace with a standard term |
| proposal 20 | `BYOK` is not in revision 7 (never expanded in the demo files before this audit) + `surrogate` (`hsurr:...`) unexplained | new terms in the demo | already explained in MATRIKS_BYOK.md + LAPORAN_DEMO.md by this audit; add to the revision 8 glossary if BYOK becomes part of the product |
| proposal 21 | `baca_file` (mock) vs `read_file` (real) — finding 11 | mock evidence is less direct by comparison | regenerate 9 `mock_logs/` + 2 documents + tests |
| proposal 22 | JSON keys in `biaya_demo.json` mix languages + check ids mix languages (`cek_a` vs `check_b`) + `kalengan` vs `canned` in class names | language consistency in demo artefacts | regenerate demo evidence / rename classes (wait for the language policy) |

## Findings REJECTED after verification (not findings)

| # | Auditor's claim | Verification result |
| --- | --- | --- |
| review note 1 | finding 26: "41 tests / 244 passed" is wrong; the correct numbers are 32 functions / 219 functions | WRONG. `pytest --collect-only` collects **41 tests** from `test_byok_provider.py` (32 functions, 2 of them parameterised) and **248 total** (244 passed + 4 skipped). LAPORAN_DEMO is right. |
| review note 2 | `spent_in_period` calls a Ledger method that doesn't exist (`spending.py:233-239`) | WRONG. The file is only 105 lines; the code at `:72-80` uses an `hasattr` guard + in-memory fallback. Behaviour is correct. See proposal 17 for the architecture side. |
| review note 3 | README mentions the Verifier and the Office without definitions | WRONG. `grep` proves the README doesn't contain either word at all. Both appear in CONTRACTS/STATUS/code per the revision 7 definition. |
| review note 4 | STATUS / README / CONTRACTS contradict each other on 7 vs 8 attack paths | NOT a contradiction. Line 6 of STATUS is a historical note (the first 7 paths, by subagent F); path 8 is recorded on line 11. Given an explanatory annotation; the historical numbers were not changed. |
| review note 5 | `tombstone`/`fail-closed` are new terms | REJECTED. Both are defined in revision 7. |
| review note 6 | The 31 event types don't match the definition | REJECTED. A script compared `ledger.py`, both CHECK blocks in `schema.sql`, CONTRACTS.md, and definition 2.3 — exactly 31 types match. |
| review note 7 | The 6 RLS policies don't match the claim | REJECTED. Counted directly from `schema.sql` — 6 policies, matching. |
| review note 8 | The 203-test arithmetic is wrong | REJECTED. 105+5+26+21+12+7+26+1 = 203, correct at the time; now 244 (+41 BYOK). |

## Accepted patterns (not changed)

- **Indonesian prose + English identifiers.** Comments/docstrings/documents
  in Indonesian, code names in English for standard technical concepts —
  this is a consistent pattern across the whole tree, not an inconsistency.
  (`CannedProvider`, `canned-...`, `menunggu_persetujuan` as an internal
  demo status value, `isi` vs `content` in different domains.)
- **Revision 7 terms used as-is:** Buku Kerja, Pemeriksa, Pembaca
  terkarantina, alur, modul, Kantor, PuspaBot, Rekor Otonomi, kejadian, tugas,
  token izin, gerbang, batas belanja, angka yatim, vonis, kaset, pemicu,
  handoff, detak. Don't propose replacing them — what matters is
  consistent usage.
- **Two spellings `sub_agen` vs `subagen`:** both come from revision 7
  itself (the definition uses both in different sections). Needs fixing
  in the revision 8 definition, not in the code.
- **`requirements.txt` without version pins** is recorded as an observation
  (§ Decision 4), not a naming finding.

## Evidence

- Baseline before fixes: 244 passed, 4 skipped (solo suite,
  17 Nov 2026 ~19:33 WIB).
- After fixes: **244 passed, 4 skipped in 37.83s** (solo suite,
  `.venv/bin/python -m pytest tests -q`, 17 Nov 2026 ~21:50 WIB) — same
  as baseline; no test changed result.
- Demo dry-run after the renames: **7/7 checks passed** (run fe543761,
  mode=dry-run, provider=canned, 4.5s, 17 Nov 2026 ~21:51 WIB;
  evidence `demo/verifikasi_demo-fe543761.json`). This run's evidence file
  uses the `-fe543761` suffix, so the e3416931 evidence file (no suffix) was not overwritten.
- Postgres once vanished entirely from the VM (the apt package gone after a restart);
  recovered with `apt-get update` + `apt-get install -y postgresql-16`
  + `pg_ctlcluster 16 main start` + `infra/setup_db.sql` (per the README).

## Audit limits

- NO renames of modules/files/public APIs/table names/event names were
  done — all of them are in § Proposals.
- NO push to GitHub. NO phase 1 started. The revision 7 definition
  and checklist were not changed.
- 4 live-container tests still skip (Docker can't run on this VM);
  not a naming finding.

## Translator's note (18 Nov 2026)

This is a dated audit record (17 Nov 2026). Quoted old identifiers,
paths, line numbers, and before/after values are kept verbatim as they
were at audit time. Two things have changed since: (1) the language
policy this audit assumes — Indonesian prose and stored domain values,
English only for standard technical code names — is superseded by the
English-only refactor ordered by Nero on 18 Nov 2026, which resolves
the § Decisions-needed item 1 in the English direction (see
`docs/GLOSSARY.md`); several proposals 1 to 12 were implemented by
that refactor (e.g. the two `skills.py` → `skill_signing.py` /
`skill_loader.py`, the two session modules → `user_sessions.py` /
`agent_session.py`, mixed SQL columns renamed). (2) Audit-time paths
map to the renamed tree as: `STATUS.md` → `docs/status-phase-0.md`,
`CONTRACTS.md` → `docs/contracts.md`, `nanobot-INVENTORY.md` →
`docs/nanobot-inventory.md`, `infra/setup_db.sql` →
`infra/schema_core.sql`, `puspa/policy/skills.py` →
`puspa/policy/skill_signing.py`, `puspa/chassis/skills.py` →
`puspa/chassis/skill_loader.py`, `puspa/chassis/session.py` →
`puspa/chassis/agent_session.py`, `puspa/accounts/sessions.py` →
`puspa/accounts/user_sessions.py`, `schema_agen.sql` →
`schema_agent.sql`, `build/fase-0/` → the repo root.
