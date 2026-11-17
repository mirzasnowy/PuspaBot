# STATUS — Phase 1 Puspa MVP (Persona + Memory + Onboarding)

Definition: revision 7 (approved by Nero 17 Nov 2026 ~17:42 WIB).
Dates in the definition ignored on Nero's order, 21:53 WIB (only
ordering, not deadlines). Local work in `build/fase-1/`; DO NOT push
to GitHub; DO NOT start Phase 2.

## Numbers (all runs SOLO, one pytest at a time)

| Suite | Result | Time |
| --- | --- | --- |
| Phase 1 (new) | **58 passed** | 17 Nov 2026 ~15:07 WIB (after Postgres recovery, see below) |
| Phase 0 (re-verification) | **244 passed, 4 skipped** | 17 Nov 2026 ~15:07 WIB, solo |
| System-side onboarding (4 steps) | **0.29 seconds** (5-minute target) | measured 17 Nov 2026 ~15:07 WIB |
| Backup + restore to a clean DB | **0.3 seconds**, 14 tables recovered, hash chain intact | measured 17 Nov 2026 ~15:07 WIB |

4 Phase 0 skips = live-container Docker tests (kernel rejects `setns`) —
the same environment block as Phase 0, not a regression.

## What was built (per definition scope)

### 1. 3-layer memory (§2.7) — `puspa/memory/`
- **Profile** (`profile.py`): facts + deterministic UNION merge;
  sources `user_stated` / `user_confirmed` / `inferred`;
  inferred facts go to Proposals (not the profile) until confirmed /
  rejected; a new fact replaces the old version WITHOUT deleting history
  (status `replaced` + `valid_until`, conflicts returned as
  `konflik_dengan`); valid from/until, one active version; view /
  correct / delete (delete = status `rejected`, history stays).
- **Episodic** (`episodic.py`): chat + task history, pgvector index
  (`vector(384)` + HNSW index `idx_episodik_embedding`,
  verified present in the DB); write path + read path work.
  Embedding: deterministic `HashEmbedding` (see "Decisions" below).
- **Procedural** (`procedural.py`): written AUTOMATICALLY from
  successful tasks, no editor; lifecycle `trial` -> `verified`
  (3 successful runs, min 2 agents) -> `broken` (2 consecutive
  failures); customer numbers never enter procedures. LIMIT: the full
  service flow graph (canary, cross-agent reuse) is built in Phase 3 —
  here the write path + storage + base status.
- **Scoping** (C56): core profile (agent_id NULL, held by Puspa)
  visible to all agents; per-agent facts only to that agent; episodic /
  procedural per user + agent; RLS per table (tested as `puspa_app`).
- **Slices** (`slices.py`, C57): subagents do not touch storage;
  the agent grants a slice (query max 500 characters + closed column
  set {profile, episodic, procedural} + token_ref); grants are recorded
  in the audit table `slice_grants`. CONFLICT: the definition asks for
  it to be recorded IN THE LEDGER, but the closed event-type set
  (31 types) has no `slice_granted` — see "Conflicts" below.
- **Idle-time consolidation** (`consolidate.py`, C60): a deterministic
  job (extractive, no model, Rp0) summarizes old episodic entries per
  task; each summary points to its source `event_ids`; successful
  patterns are promoted to procedural; already-summarized episodes
  are not repeated. The idle-time scheduler caller is Phase 2 (2.14).

### 2. Persona (§2.16) — `puspa/persona/`
- 3 style examples (`santai`, `rapi`, `campur`) + optionally paste
  10-20 of your own messages (max 20, oldest dropped; no WhatsApp
  chat import).
- Corrections ("lebih santai", "jangan pakai gue", ...) are stored as
  `user_confirmed` facts and apply immediately (`get_persona`).
- Gate 3 blind-test harness (`blind_test.py`): 20 items (10 original +
  10 persona), deterministic shuffle (seed), test sheet + scoring
  (60% threshold). Finished example in `blind-test-contoh/` (20 items,
  10/10). THE HUMAN TEST IS WAITING ON NERO (5 friends have not
  answered yet) — no results invented.

### 3. Onboarding (§2.16) — `puspa/onboarding/`
- 4 steps: name+style, notifications+test send, connectors (can be
  later), guided first task. Measured end-to-end: **0.29 seconds system
  side** (human reading/typing time not included — this number is code
  time, not human time; the 5-minute target is a human-flow target and
  can only be measured in user testing).
- Step 2 is HONEST: Phase 2 Web Push does not exist yet, `StubNotifier`
  reports `terkirim=False` (not pretend-sent).
- Step 4 is a MEASURED STUB: the Phase 3 price-compare/bill wedge does
  not exist yet; the stub calls the real path (validation, ledger
  `task_created`+`task_completed`, episodic, time measurement) and
  returns `terbukti=False` + a reason. No price/bill is invented.
- After the first task: 3 built-in workflow templates offered (list
  only; custom workflows are Phase 4).

### 4. Google login (decision 16, §2.15) — `puspa/auth/`
- OAuth 2.0 authorization code + PKCE S256, anti-CSRF state, minimal
  login scope (`openid email profile`); code exchange, refresh,
  revoke, RS256 ID token verification (JWKS, iss/aud/exp); tokens
  stored in the Phase 0 encrypted vault (`google_oauth`); per-device
  sessions via Phase 0 `user_sessions.py` (reused, unchanged).
- Google-only accounts: a `users` row is created with a random
  password hash nobody knows (the `password_hash` column is NOT NULL
  in the Phase 0 schema); password login is no longer used.
- Backup email link (`email_link.py`): one-time HMAC token,
  15-minute TTL, replay rejected.
- BLOCKED: publishing the app to "In production" needs Nero's browser
  (Testing status = refresh tokens expire after 7 days). Code complete +
  tested (HTTP mock + real RSA JWKS); publishing = Nero's pending step.

### 5. Backup + data consent (§2.17) — `puspa/backup/`, `puspa/consent/`
- Backup: `pg_dump -Fc` -> Fernet encryption (key only from env
  `PUSPA_BACKUP_KEY`, no default) -> `build/fase-1/backups/`
  (outside the DB); 14-day retention (automatic prune); JSON manifest.
  Automatic daily scheduling waits for the Phase 2 scheduler (2.14).
- The first restore test was RUN (test `test_restore_to_db_bersih_and_verifikasi` +
  a manual run): restore to a clean DB in 0.3 seconds, 14 tables, the
  ledger hash chain verified intact. Evidence:
  `backups/puspa-20261117-150740.dump.enc` (+ `.json` manifest).
- Consent: text `v1-2026-11-17` (6 required points: what data, where,
  how long, who can see it, export/delete, shared graph without
  identity); recorded in the `data_consents` table + the ledger
  (the `data_consent` type already exists in the ledger).
- Account export: profile + episodic (without vectors) + procedures +
  summaries + consents + onboarding + session list — WITHOUT vault
  secrets (tested: tokens are not included in the export).
- Account deletion: deletes memory/procedures/examples/onboarding/
  consents + account (sessions + vault via Phase 0 `users.delete_user`);
  the hash chain stays intact (no UPDATE/DELETE on `events`).
  LIMIT: the ledger event `content` of a deleted user STAYS —
  see "Conflicts".

## Recorded decisions
1. **Free embedding: `HashEmbedding` (Rp0).** Deterministic from
   token hashes, no model, no network, no new dependencies.
   Limited semantic quality (similarity = word overlap).
   The replacement seam is ready: `EpisodicStore` takes any embedder
   (`embed` + `dim`); upgrade candidates: local BAAI/bge-small
   via fastembed, or cheap OpenRouter embeddings. Changing dimensions
   needs a `vector(N)` column migration.
2. **pgvector 0.6.0** installed via apt (`postgresql-16-pgvector`);
   extension active in `puspa_test`; episodic HNSW index installed.
3. **Deterministic consolidation summaries** (extractive, no model):
   Rp0, testable. Model-based summaries = a Phase 2+ improvement.
4. **No new dependencies.** Phase 1 uses what is already in the
   Phase 0 venv (psycopg, cryptography, argon2-cffi, httpx, pytest).
5. **Language policy** (Nero's order, 21:53 WIB): English identifiers,
   Indonesian domain values (revision 7), Indonesian prose. The 22
   rename proposals P-1–P-22 were NOT applied (waiting on Nero's word).
6. **Temporary deterministic persona rendering** (`render_reply`):
   rule transforms (gue->aku etc.) as a stand-in for model rendering
   until Phase 2. The blind-test harness uses this transform — the
   human blind test will therefore test the transform persona, not a
   model persona; recorded so it is not misread.

## Definition vs build conflicts (recorded, definition unchanged)
1. **C57 — slices recorded in the ledger.** The closed event-type set
   (31 types, §2.3) has no type for slice grants.
   Meanwhile: the audit table `slice_grants` (with RLS, auditable),
   with an `event_id` column prepared to link once the type exists.
   Needed: add a `slice_granted` type (revision 8 / Phase 2, needs a
   Phase 0 schema CHECK migration).
2. **C129 — account deletion vs ledger content.** Definition 2.3 forbids
   UPDATE/DELETE on `events`, and 2.17 asks account deletion to delete
   memory etc. with the chain intact via tombstone. Phase 1
   deletes all user data OUTSIDE the ledger; event `content`
   holding user data stays. Needed: a tombstone event type /
   redaction policy (revision 8 / Phase 2).
3. **C126 — "dump every day".** The backup + prune + restore
   mechanism is done and tested; automatic daily scheduling needs the
   Phase 2 scheduler.
4. **C122 — "5 minutes".** What Phase 1 can measure is system-side
   time (0.29 seconds). Human time (reading, typing, choosing)
   can only be measured in user testing (gate 15: 8 of 10 test users
   reaching their first verified result within 5 minutes).

## Gates touched
- **Gate 3 (Persona blind A/B): PARTIAL.** Harness + 20 example items
  done and tested; the 5-friend human test is PENDING (waiting on Nero).
- **Gate 15 (Operations + onboarding): PARTIAL.** Consent recorded
  (11/11 not yet measured — needs test users); restore to a clean
  host succeeded within 30 minutes (measured 0.3 seconds on this VM);
  onboarding system time measured; 0 expired Google tokens over 4
  weeks of testing NOT yet measured (needs a test period + production
  publishing).
- **Gate 8 (Moat graph):** not yet — procedures stored, full graph
  in Phase 3.

## Needs Nero (human steps, not code blockers)
1. **Publish the Google OAuth app** to "In production" (Nero's
   browser; decision 19). Without this, refresh tokens expire after 7 days.
2. **Gate 3 blind test**: 5 friend judges + their answers.
3. **Legal review** of the v1 consent text (decision 20) before
   users outside the close circle.
4. **Decision 5** (PLN/PDAM/phone customer numbers) — needed in Phase 2,
   not Phase 1.

## Environment incidents during Phase 1
- **VM restart in the middle of the work** (uptime 0 minutes when
  checked ~15:04 WIB): Postgres 16 lost COMPLETELY (apt packages +
  cluster + data). Recovered: `apt-get update` + install
  `postgresql-16` + `postgresql-16-pgvector` +
  `pg_ctlcluster 16 main start` + `infra/schema_core.sql`
  (as postgres, file chmod 644 first) + `CREATE EXTENSION vector`.
  All workspace files intact. Both suites re-verified green AFTER
  recovery (Phase 1: 58 passed; Phase 0: 244 passed, 4 skipped).
  Lesson: Postgres on this VM does not survive restarts — the Phase 1
  encrypted backup is the right mitigation; the automatic daily
  backup schedule (Phase 2) matters.
- **Parallel pytest runs = false failures** (confirmed again): the
  first Phase 0 run showed 7 failed + 8 errors when two pytest runs
  ran at the same time on the same DB; solo runs are green. All numbers
  in this document come from SOLO runs.

## Large-repo patterns adapted (detail: `reuse.md`)
- **mem0** (Apache-2.0): sourced facts + scoped per user; inferred
  -> Proposals (not profile). Rewritten as `profile.py`.
- **Graphiti** (Apache-2.0): temporal facts (valid from/until);
  procedures as templates without personal data. Rewritten.
- **Letta** (Apache-2.0): sleep-time compute / idle-time consolidation.
  Rewritten as `consolidate.py` (deterministic).
- **elizaOS** (MIT): persona as data (character file).
  Rewritten as profile facts + style examples.
- No code copied verbatim; no new dependencies; no forks.
