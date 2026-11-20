# Status: repo maturity phase (Fase Kematangan)

Date: 18 Nov 2026. Coordinator: maturity phase (1af6c4c4), after the
deepening phase (main `68901c4a`, 1,321 passed, 5 skipped). This doc is
the living status log for the maturity phase. `docs/status-brain-deepening.md`
is frozen: the deepening phase is done, and that file sits in the
file-size ratchet baseline at 1,134 lines, so it is not appended to
anymore. Per-iteration commits: one item per commit, each pushed only
after a green SOLO full-suite run by the coordinator (workers do not
push; the coordinator runs the suite centrally and pushes, because
parallel suite runs collide on the shared `puspa_test` DB).

## Standing rules (inherited)

- One item = one iteration with its own commit + push after a SOLO
  green suite. Real code + tests + wiring per file, not empty
  scaffolds. Depth bar "SANGAT DALAM": core mechanism + flagship
  really exist, not a seam without a schedule.
- Patterns/design/architecture are taken and rewritten as Puspa code,
  NEVER copied verbatim. DuskBot PolyForm Noncommercial (study only).
  hermes-agent MIT (attribution when adapted very closely). OpenBot
  MIT, Intelligence separately licensed + enforcement server closed
  image (patterns from docs only). Deps MIT/Apache-2.0/BSD via pip;
  REUSE.md + NOTICE.md updated per iteration.
- NO Indonesian naming anywhere in the repo. No forks ever.
- LICENSE is Nero's decision: never pick it unilaterally (recorded
  pending in `docs/verification.md`). Billing model (credits vs
  subscription vs selling compute) is Nero's decision: build the
  mechanism, make the model a config, report it to the parent.
- Postgres 16.15 is fragile (VM replacements wipe packages/roles/
  DBs). Recovery: apt install postgresql-16 + postgresql-16-pgvector,
  pg_ctlcluster 16 main start, run infra/schema_core.sql; if apt is
  blocked by the broken cloudflare-warp package, dpkg --remove
  cloudflare-warp first. Check pg_lsclusters before the suite.
- Docker containers cannot run on this VM (kernel rejects setns).
  Venv build/.venv, use `.venv/bin/python -m pip`. NO_PROXY=
  localhost,127.0.0.1 for the GitHub API helper. Push via Git Data
  API (helper: `hidden_files/push.py`); git CLI has no credentials.

Older wave entries (Waves 1-8 and the CI lanes adoption) were archived
to `docs/status-maturity-archive-2026-11-18.md` on 18 Nov 2026, when this
file crossed the 2,000-line hard cap of the file-size ratchet. Waves 9-13
were archived to `docs/status-maturity-archive-2026-11-19.md` on 19 Nov
2026, when the live log passed the 1,000-line soft cap (1,060 lines
against a 1,055 baseline). Waves 14-15 were archived to
`docs/status-maturity-archive-2026-11-19-waves-14-15.md` on 19 Nov
2026, when the live log approached the soft cap again (975 lines).
Waves 16-17 were archived to
`docs/status-maturity-archive-2026-11-19-waves-16-17.md` on 19 Nov
2026, when the live log passed the soft cap again (1,002 lines).
Waves 18-19 were archived to
`docs/status-maturity-archive-2026-11-19-waves-18-19.md` on 19 Nov
2026, when the live log passed the soft cap again (1,032 lines
with the Wave 26 entry). Waves 20-21 were archived to
`docs/status-maturity-archive-2026-11-19-waves-20-21.md` on 19 Nov
2026, when the live log approached the soft cap again (980 lines
before the Wave 28 entry). Waves 22-23 were archived to
`docs/status-maturity-archive-2026-11-19-waves-22-23.md` on 19 Nov
2026, when the live log passed the soft cap again (1,051 lines
with the Wave 28 K12 entry).
History is never deleted, only archived.

## Wave 30 — B9 + B10 (2 workers parallel, pipeline rule, 19 Nov 2026)

B9 delegated tasks with approval (extends the B6 board, no
second board). `puspa/rooms/delegation.py`: delegate to the
agent (G25 handle verified, forged handles refused) or to a
teammate (guests can neither receive nor delegate); cycle
delegated → in_progress → done, block/resume, progress
reports on the board; irreversible steps go through G4
`ApprovalPolicy.decide` and, when filed, G5 `Gate.execute`
bound to the content hash (24h expiry); with no policy wired
in an irreversible step stays `awaiting_approval` and can
never run. PWA API `puspa/pwa/rooms_api.py` (GET rooms/board,
POST tasks/steps/approve/reject) + Tasks screen "Shared task
boards" section + generated client types. Module tests: 30
passed (23 delegation + 7 PWA API, verified by coordinator).
Honest limits: the agent worker itself is not executed here
(caller runs the child via `SubagentLifecycle`); no approval
policy/gate wired into the HTTP layer; G9 decider not
consulted for delegation decisions (later ticket).

B10 meeting notes and follow-ups (`puspa/meetings/`:
deterministic `parse.py`, `store.py`, `reminders.py`,
`schema.py`; `infra/schema_meetings.sql`). Minutes from
explicit markers only (`Decision:`, `Action:`/`TODO:`/`- [ ]`,
`Title:`, `Attendees:`, `Note:`) — unmarked talk is ignored,
never invented; owner via `@Name`, deadline via `by <date>` /
weekday / `tomorrow` (English + Indonesian names). One B6
board task per action item; each decision is a room-memory
`decision` row AND a ledger `evidence_recorded` event
(`kind: meeting_decision`); action items are ledger
`task_created` events — no new event type (closed set of 31
untouched). Reminders are queries + a G8 cron hook (12h,
Asia/Jakarta), never sends. Module tests: 14 passed
(verified by coordinator). Honest limits: B2 not built here
(the ledger events are the stand-in B2 adopts later);
extraction is marker-based; owner resolution needs the
caller-supplied display-name map; `write_minutes` is not one
cross-store transaction (partial write possible, minutes row
is the anchor).

Ratchet fix (Wave 30): the B9 rooms API grew
`docs/openapi-pwa.yaml` to 2,152 lines, past the 2000-line
hard cap. The rooms contract (5 endpoints + 5 schemas) was
split into `docs/openapi-rooms.yaml` (461 lines); the main
spec keeps $ref stubs and is now 1,727 lines (baseline
re-recorded with this reason). The generated client
(openapi-typescript 7.13.0) resolves the external refs —
regenerated and verified (`tsc -b` clean, rooms types still
present). The Error schema stays in the main spec and is
referenced back from the rooms file.

## Wave 29 — K13 + B6 (2 workers parallel, 19 Nov 2026)

K13 anti-manipulation design rule (`puspa/policy/conflicts.py`,
991 lines; `ConflictGate`, five closed conflict kinds,
`ConflictSpec`, `ConflictStore`, Postgres table
`conflict_records` via `infra/migrate_conflict_records.sql`).
Wired into the turn loop after the intent advisor (K1) and the
autonomy dial (K11), so even a `do_silently` action is surfaced
when it carries a conflict. Surfacing writes
`approval_requested` and the choice writes
`approval_granted`/`approval_rejected` through the existing
ledger event types — no 32nd event type. Review checklist in
`docs/anti-manipulation.md` covers every implemented K item
(K1, K3, K4, K5, K6, K10, K11, K12); K2, K7, K8, K9 gain a row
when they land. The loop seam lives in
`puspa/turns/conflict_wiring.py` (loop.py kept at 999 lines,
under the ratchet cap). Module tests: 32 passed
(`tests/test_anti_manipulation.py`, verified by coordinator).
Honest limits: store in-memory by default (restart loses
unpersisted records); detector scores are caller-assessed,
not calibrated; nothing yet maps the user's reply text to
`record_user_choice` — follow-up wiring needs a decision.

B6 shared rooms (`puspa/rooms/`: rooms, invites, memory,
tasks, money, routing; `infra/schema_rooms.sql`, 7 tables +
RLS). Invite by link (7-day TTL) or one-time 6-digit code
(24-hour TTL), hash-only secrets, roles Owner/Member/Guest,
5-person beta cap, last-owner rule; leaving deletes the
leaver's private memories and money approvals in the same
transaction. Three-layer memory (personal never in a room,
room-visible, private answers). Money gate: owner alone, or
two people. Routing: linked group + @Puspa mention answers
with the room as principal; channel `handle_inbound` gained
an optional `group_handler` hook — without it group messages
are still dropped (DM-only default preserved). Module tests:
83 passed (verified by coordinator). Honest limits: no live
Telegram/WhatsApp bot verified (MockTransport level, as G14);
WhatsApp groups postponed per plan §12.4; summaries are
deterministic, not model-written; room actions are not ledger
event types (B2 follow-up).

Verification note: both workers claimed full worktree suites
green (K13 3,476; B6 3,527) but neither left a suite log file,
and the elapsed time does not fit a 40-minute suite, so both
claims are treated as unverified. The central suite below is
the deciding gate. Postgres recovery during this wave: the VM
was replaced and Postgres was gone entirely; coordinator
reinstalled postgresql-16 + pgvector 0.6.0, started the
cluster, recreated role root + DBs puspa_test /
puspa_test_w1 / puspa_test_w2, applied `schema_core.sql` to
all three, verified RLS.
## Wave 31 — B11 + B12 (2 workers parallel, pipeline rule, 19 Nov 2026)

B11 patungan / shared splitting (`puspa/rooms/splitting.py`,
exactly 1,000 lines; `infra/schema_rooms.sql` gains
`room_splits`, `split_shares`, `split_payments`,
`split_settlements` + RLS). Money is integer sen only
(1 rupiah = 100 sen); equal / custom / weighted shares with
exact sums (leftover sen to earliest participants, payer's own
share settled at creation); partial payments, overpayment
refused, never creates credit; reminders are queries only
(debtors only, G8 12h hook, Asia/Jakarta); settlement is a
B8 prepare-only seam through the B6 MoneyGate (owner alone,
or requester + one other owner/member), `prepared` row with
verified recipient = payer, no `execute_settlement` (a test
asserts it does not exist), optional ledger `evidence_recorded`
with `executed: false`. PWA API: GET/POST splits, POST
payments, POST settlements prepare (403 until the money gate
approves). Module tests: 27 passed (21 splitting + 6 PWA API,
verified by coordinator in build/). Honest limits: no live
chat delivery verified; no `@puspa patungan` chat command yet;
no payment rail named on a prepared settlement (B8's job);
no PWA screen yet (API only); a split creates no B9 room tasks
for its debtors.

B12 group research and shopping (`puspa/rooms/research.py`;
`infra/schema_rooms.sql` gains `room_research_links` + RLS).
The room collects product links; `@puspa compare` renders one
group comparison (price + seller per item, each with its
`evidence_id` and verifier verdict) without calling the agent;
cheapest is computed over Verified prices only; unreadable
links are listed unchecked with their reason, never guessed;
seller reported only when the reader reports one
(`ResultRead.seller`, JSON-LD `offers.seller`). Agreement is
explicit (every agreer must be a room member); the decision
lands as room-memory kind `decision` (same as B10) so B3 can
adopt it later. ToS gate (K-15): in `live` mode all three
marketplaces are held, the reader is never called. Module
tests: 18 passed (verified by coordinator in build/). Honest
limits: no live marketplace read verified (live mode held
behind K-15); no B1-style reliability downgrade (group links
carry no category); no live Telegram/WhatsApp delivery
verified; WhatsApp groups stay postponed.

Merge notes (coordinator): both workers changed the same
three files (`puspa/rooms/__init__.py`,
`puspa/rooms/schema.py`, `infra/schema_rooms.sql`).
Resolved with `git merge-file` (3-way, base = build/).
Lesson: sorted-union resolution is right for sorted Python
lists but DESTROYS SQL — the first attempt scrambled the
CREATE TABLE statements (column lines sorted alphabetically);
redone with concatenation, validated by applying the merged
file to a scratch database (all 12 room/split tables created,
exit 0). Coordinator fixes during merge: (1) B11's
`split_reminder_lines` was listed in `__all__` but missing
from the `from .splitting import` block — added (the worker's
ruff run passed only because the project config does not
flag it; the name genuinely was not importable from the
package); (2) B11 added the Split paths/schemas to
`docs/openapi-rooms.yaml` but not the $ref stubs in
`docs/openapi-pwa.yaml`, so the generated client had no Split
types — stubs added for the 3 paths + 3 schemas, client
regenerated (openapi-typescript 7.13.0, Split types present),
`tsc -b` clean.

Central suite after Wave 31: 3,647 passed, 23 skipped, 1
warning, EXIT 1 on first run — the single failure was the
file-size ratchet (`docs/openapi-pwa.yaml` 1,739 lines vs its
1,727 baseline: the 12 B11 $ref stub lines). Fixed by
re-recording the baseline at 1,739 with the reason in
`tests/test_file_size_ratchet.py` (Wave 30 precedent); the
ratchet test re-run green alone (3 passed). No other test
failed; the only change since the full run is the baseline
number in that one test.
## Wave 32 — B13 + B18 (2 workers parallel, pipeline rule, 19 Nov 2026)

B13 co-drafting with context memory (`puspa/drafting/`:
`voice.py`, `drafting.py`, `errors.py`, `schema.py`;
`infra/schema_drafting.sql` gains `drafts` +
`draft_approvals` with owner-only RLS). The voice profile is
built from active memory facts in the `voice.*` /
`preference.*` / `decision.*` namespaces (G7/G24 — no second
memory system); corrections parse into rules (`never use X`,
`always start with X`, `keep it short`, `casual`, `formal`,
English or everyday Indonesian) or are kept as surfaced
style notes, never dropped. Default register is everyday
Indonesian, never formal; English briefs draft in English.
A brief that contradicts a recorded past decision is still
drafted, but the decision is flagged (word-overlap match,
warning only, never a silent rewrite). Send is approval-gated
by construction: `send_draft` is the only send path and
requires a recorded approval id bound to the SHA-256 of the
draft text (24h expiry, single-use; editing a draft voids
its approval; re-send after use is refused). Delivery is an
injected sender; with none wired an approved send raises
`NoDeliveryChannelError` and the approval is NOT consumed.
PWA API `puspa/pwa/drafts_api.py` (POST/GET drafts, approve,
send — 403 without a usable approval, 501 with no delivery
channel wired — voice profile + corrections). Module tests:
29 passed (23 service + 6 API, verified by coordinator in
build/). Honest limits: draft assembly is deterministic
template assembly, not free generation — no LLM call; decision
matching is word overlap, not meaning; no delivery channel is
wired into the PWA yet (live sends return 501 until
Telegram/WhatsApp/email is connected).

B18 second-brain graph view (`puspa/pwa/graph_api.py`;
`apps/pwa/src/components/GraphView.tsx` — a real SVG graph,
deterministic circle layout, click a node to inspect — on the
Memory screen). One `KnowledgeGraph` per user in process
memory, lazily seeded from the user's episodic memory (max
200 episodes) with ledger corrections replayed on top.
`GET /api/graph` is bounded (limit clamped to 100, offset
pagination, `kind` + `since` filters; edges only touch the
page's nodes). Edits: rename / add / delete node, add /
change / delete edge, `correct_fact` — every edit writes
back to the KnowledgeGraph and is recorded in the ledger as
a user correction (`evidence_recorded`, `kind:
"user_correction"`, before/after detail, one task ledger per
user) — no 32nd event type. Edit order: apply to the graph
first, then record; if the ledger write fails the graph is
rolled back (cache dropped, re-seeded + replayed), so an
edit never lands without a record. Code-graph nodes (kind
`file` / `function` / `class` / `module` / `table` /
`config`, or `*.py` names) refuse edits with 403 and leave
zero ledger entries. `GET /api/graph/search` runs HippoRAG
over the same graph, so a correction changes the next
retrieval answer through it. Module tests: 9 passed
(verified by coordinator in build/); frontend vitest 28
passed (GraphView + client). Honest limits: the graph lives
in process memory, not a DB table — a restart rebuilds it
from episodes + replayed corrections, and episodes recorded
after seeding do not appear until restart; seed capped at
200 episodes; corrections do NOT yet change the episodic
pipeline (`search_episodes_pipeline` builds its own graph
per query) — only answers read through this graph service
change; write-back of corrections to the episodic table is a
follow-up. The circle layout reads well for tens of nodes
and crowds at hundreds (mitigated by the 100-per-request
cap + the list below the graph).

Merge notes (coordinator): both workers changed
`puspa/pwa/app.py` (different regions — merged with `git
merge-file`, one import-line conflict resolved by union).
Both workers also changed `docs/status-maturity.md` and
`tests/test_file_size_ratchet.py` in their worktrees —
NEITHER copy was taken (worktree status docs go stale; the
worktrees were refreshed before the Wave 31 ratchet fix, so
their ratchet baselines were the old 1727). The Wave 32
section here is the coordinator's own. Spec split (Wave 30
precedent): B18's graph contract (5 paths + 9 schemas)
would have pushed `docs/openapi-pwa.yaml` to 2,314 lines,
past the 2000-line hard cap, so it moved to
`docs/openapi-graph.yaml` (592 lines) with $ref stubs in the
main spec (Error refs rewritten to point back at the main
spec, as the rooms file does). Main spec now 1,785 lines
(baseline re-recorded from 1739 with this reason);
`docs/openapi-rooms.yaml` grew to 1,247 lines with the B13
draft contract and gained its own baseline entry with a
reason. Generated client regenerated from the merged spec
(Draft + Graph types present), `tsc -b` clean. Status log
archived: Waves 24-27 moved to
`docs/status-maturity-archive-2026-11-19-waves-24-27.md`
(496 lines) when the live log hit 1,006 lines; the live log
is now Wave 28 onwards.

Central suite after Wave 32: 3,686 passed, 23 skipped, 1
warning, EXIT 0, 25m08s (3,648 after the Wave 31 ratchet
fix + 29 B13 + 9 B18, exact). First run failed at setup
with 3,703 connection errors — the VM had been replaced
mid-run and Postgres was gone entirely; coordinator
reinstalled postgresql-16 + pgvector 0.6.0, started the
cluster, re-applied `infra/schema_core.sql`, recreated DBs
puspa_test / puspa_test_w1 / puspa_test_w2, verified RLS,
then re-ran the full suite green.

## Wave 33 — B1 + B2 (2 workers parallel, pipeline rule, 19 Nov 2026)

B1 "Cek Sebelum Beli" beta slice (commit f19ed121) and B2
verified outcome records (commit 34241f7b) — the first two
items of the B queue (coordinator 4bab3713, continued by
ae20593d after 4bab3713 stopped mid-flight at 21:48 WIB
with the central suite still running). B1: new
puspa/check_before_buy package (classifier, answer,
outcome, records, wiring) + cassette + docs/beta-slice.md,
wired into the turn intercepts; evidence rules enforced
(fair price = median of recorded observations, verified
needs 2+ sources; seller/account only from the recorded
store; advice acts only when all applicable checks are
green); mode="live" refused outright per decision 15 (ToS
gate still open), no fetch attempted; account numbers
masked. B2: new puspa/evidence/outcomes.py single shared
writer (records without evidence rejected with
OutcomeError); B6/B9-B13 modules routed through it;
outcome indexes in the ledger schema;
docs/outcome-records.md.

Central suite Wave 33, first run: 22 failed, 3,701 passed,
23 skipped, 13 errors, SUITE_EXIT 1 (31m32s). Root cause of
34 of the 35 problems: the suite ran with the dirty NO_PROXY
(bracketed-IPv6 [::1]), which breaks httpx (ValueError:
invalid literal for int(): ':1]') — channels (15),
rooms_routing (3), reranker setup errors (13), billing
fixtures (2), phase-3 gate e2e (1). Fix: re-run with
NO_PROXY=localhost,127.0.0.1 (documented in ~/TOOLS.md).
NO B1/B2 test failed — all 50 new tests passed. The 35th
problem was real (B2's doing): the ratchet —
puspa/rooms/delegation.py grew 998 -> 1,039 lines and
puspa/rooms/splitting.py 1,000 -> 1,031 lines, both past
the 1000-line soft cap; recorded in BASELINE_LINES with a
reason + split candidate (agents.py precedent), riding in
the B2 commit. Ratchet test alone: 3 passed.

Central suite Wave 33 re-run (sanitized NO_PROXY): 3,736
passed, 23 skipped, 1 warning, EXIT 0, 29m22s (3,686 + 29
B1 + 21 B2, exact). Both items pushed: B1 f19ed121, B2
34241f7b. Pre-existing drift NOT pushed with these items:
build/README.md is the stale Phase-0 text (HEAD has the new
README), and build/puspa/rooms/rooms_api.py is a stale local
file (the repo has puspa/pwa/rooms_api.py instead). Also:
the /tmp/head snapshot is incomplete (missing
test_file_size_ratchet.py, docs/status-maturity.md) — verify
file-vs-HEAD via the GitHub contents API + git hash-object,
not /tmp/head alone.

## Wave 34 — B3 + B4 (2 workers parallel, pipeline rule, 19 Nov 2026)

B3 shared anti-fraud and price graphs (commit 2befcd89)
and B4 trust and proof surface (commit c0039687). B3: new
puspa/graphs/shared.py (shared anti-fraud + price graphs,
per-user privacy via per-(user,subject) pseudonym hash,
shared reads return aggregates only, private records in an
own table namespaced by user_id); infra/schema_graphs.sql
gains 3 shared tables + RLS on the private table; wired
into B1's answer (shared fair-price fallback only when the
local store has nothing; shared flag reason never shown,
only the aggregate count); docs/shared-graphs.md. B4: new
puspa/evidence/proof.py (Receipt for ledger events,
attach_proof, audit-trail export reusing the K12 shape;
reads over the one hash chain, no second ledger) +
puspa/pwa/proof_api.py (/proof page + API) +
apps/pwa/templates/proof.html; wired into B1's outcome
(proof block on every answer); docs/proof-surface.md.

Central suite Wave 34, first run: 1 failed, 3,785 passed,
23 skipped, SUITE_EXIT 1 (30m00s). The single failure was
a merge miss by the coordinator, not a code bug: the B4
file list taken from the worker's report omitted
apps/pwa/templates/proof.html (21 lines), so
test_proof_page_renders_receipts failed with
TemplateNotFound. Lesson recorded in the pipeline doc:
never trust a reported file list for a merge — verify with
diff -rq worktree-vs-build over puspa/, tests/, docs/,
infra/, apps/ before copying, and diff again after. Fix:
copied the template, re-ran the B4 tests in build/
(56 passed). B3's own lines had contained Indonesian
(docstrings in graphs/__init__.py, comment block in
schema_graphs.sql) — translated to English before merge.
Pre-existing Indonesian identifiers in schema_graphs.sql
(tautan_id, sumber, vonis, prosedur_id, versi, jalan_pada,
sisa, dicatat, pasal, keputusan; idx_tautan_* etc.) are
NOT touched here — renaming DB columns needs a migration,
its own ticket.

Central suite Wave 34 re-run: 3,786 passed, 23 skipped, 1
warning, EXIT 0, 29m58s (3,736 + 23 B3 + 27 B4, exact).
Both items pushed: B3 2befcd89, B4 c0039687.

## Wave 38 — E-05 + E-06 (2 workers parallel, pipeline rule, 20 Nov 2026)

E-05 SPA inside the image (commit a6a4c688), E-06 Postgres
and SQLite consistency plus kill -9 tests (commit e2e1a226).

E-05: multi-stage Dockerfile — a Node stage (`node:20-slim`,
`npm ci`, `npm run build`) builds the React SPA and the
runtime stage copies `/pwa/dist` into `/app/apps/pwa/dist`,
so the deployed image serves React instead of the Jinja
fallback; new unauthenticated `/app` SPA entry route (same
pattern as `/masuk`: shell only, empty context, the SPA
routes anonymous visitors to login); new
puspa/teams/image_smoke.py (pure-HTML shell/bundle checks
plus run_image_smoke: build, run one container on a free
127.0.0.1 port, wait for /manifest.json, check /app, always
`docker rm -f`); offline deploy validation now requires a
Node stage, `npm run build`, and a `COPY --from=<node
stage> ... dist`; the CI sandbox lane builds the deploy
image and runs test_image_smoke.py, with
test_image_smoke_live in the fail-if-skipped list; new
.dockerignore (dist is excluded on purpose: it is rebuilt
in the Node stage, so a stale local dist cannot hide a
broken Node build). Verified without Docker: an isolated
copy of exactly the files the Dockerfile copies builds a
dist/index.html byte-identical to a local `npm run build`
(apps/pwa/static must be copied too, or the icon link
differs). Honest limit: the image has never been built or
run anywhere (no Docker daemon on this VM); the live smoke
test skips here and the CI lane is its first real run; the
image build takes minutes (torch is in requirements.txt).

E-06: one-page consistency rules in docs/data-consistency.md
plus the mechanism. Postgres is the authority for task
status in the ledger; SQLite state.db holds only session
transcripts + the search index and must be rebuildable
(rebuild_sessions_from_ledger rebuilds transcripts from
ledger user_message/agent_message events, marked
recovered_from_ledger; a second rebuild is a no-op). New
puspa/consistency/: idempotency keys (task_id:step:SHA-256
of canonical JSON args), WriteAheadLog (intent
`tool_called` before the effect, result `tool_completed`
after it, same key, both ordinary ledger events so the
31-type set stays closed and the hash chain keeps running),
ConsistentStepRunner + recover_task (uncertain read-only
steps retried once, uncertain outbound steps NEVER
auto-retried — they land in needs_user_decision; a task
completes only when every step has a result, otherwise
interrupted), reconcile_startup (stale running tasks
marked interrupted via append-only `task_stopped` with
interrupted: true, then session housekeeping + FTS5
rebuild/verify). TaskRunner now records heartbeat + intent
+ result per step (best-effort, like every other sink: a
dead ledger must not kill the turn); the one exact event
order assertion in test_world_brain_wiring.py was updated
for the 3 new events per step. The kill drill
(run_kill_drill, 7 cases: 5 boundaries on an outbound step
+ 2 read-only retry cases) proves it on this VM: every
worker died with returncode -9, the hash chain stayed valid
in all cases, the outbound effect ran 0x when killed before
the effect and exactly 1x when killed after it (never 2x),
the read-only-last-step kill recovered to completed, the
rest recovered to interrupted with the uncertain outbound
step in needs_user_decision. Honest limits: the drill is
fault injection on processes on this VM, not kill -9 on a
live container (waits for a Docker host); reconcile_startup
is exported and tested but not yet wired to a call site —
the app has no startup hook today (no lifespan, no
production scheduler loop), so when one is added it is the
function to call; the tasks projection table is not updated
by E-06 (it is rebuilt from the ledger).

VM replaced again mid-wave (~21:25 UTC, uptime 0 min): the
E-06 verification run was killed by the service restart;
Postgres packages were restored by the system replay
itself (16.15 + pgvector 0.6.0, cluster down, started
manually); role root recreated, schema_core.sql re-run,
w1/w2 DBs recreated + ledger/accounts schemas applied, RLS
true on users/sessions/vault in all three; the E-06
verification run was re-run after recovery (180 passed).

Central suite Wave 38 (log suite-wave38-e05-e06.log):
4,089 passed, 25 skipped, 1 warning, SUITE_EXIT 0, 37m12s
(4,065 + 14 E-05 + 10 E-06; the extra skip is the E-05
live Docker smoke test).

## Wave 37 — B14 + E-01 + E-02 + E-03 + E-04 (2 workers parallel, pipeline rule, 20 Nov 2026)

B14 Indonesian context pass (commit 096ed355), E-01 CI sandbox +
Chromium lane (commit efc47e30), E-02 VPS validation +
machine-replacement drill (commit ae0001c9), E-03 sandbox
hardening (commit 4d2454ff), E-04 nightly live smoke lane
(commit ae1acf20). B14: checklist pass
over B1, B2, B3, B4, B5, B7, B8 only (plan §15, items
15.1-15.12), recorded in docs/indonesian-context-pass.md
with a per-ticket sign-off (all pass). Four real gaps found
and fixed: new puspa/local_data/money.py
(parse_rupiah_amount for `50rb`, `1,5 jt`, `seratus ribu`,
`dua jeti` and friends, bare digit runs return None so an
account number is never read as money; format_rupiah with
dot thousands) + new puspa/graphs/fraud_patterns.py (the
eleven 15.5 local scam patterns as data, match +
classify_flag_reason), wired into B1 (a forwarded text
matching a pattern gives do_not_act with the pattern named
and its plain-language warning) and B7 price extraction
(`harga 50rb` now fills the price slot); B1 replies now
render Rupiah with dots (`Rp12.999.000`, was commas); B8
approval records carry a `consequences` list and the render
states what approving means (15.2). Recorded as future:
per-contact register + profile timezone, admin-fee display,
Harbolnas/THR/mudik templates, Indonesian proof-surface
copy, Hijriah dates, offline/HP-OTP, the 20-person panel,
parent-companion mode.

E-01: new `sandbox` job in .github/workflows/ci.yml (8 jobs;
Postgres service + schema_core.sql, requirements install,
build infra/sandbox.Dockerfile into puspa-sandbox:3.12-local
+ smoke run, playwright install --with-deps chromium, pytest
on test_sandbox.py + test_computer_backends.py +
test_readers.py with JUnit, and an anti-skip guard that fails
the lane if any live test skips; sandbox joins the
all-checks-pass gate). The lane must build the image first:
the default image exists only on the dev VM (docker import)
and no registry. The Chromium reader test now also finds
Playwright's managed browser; the Docker fail-closed test no
longer assumes this VM (dead-endpoint DOCKER_HOST); one
docker sync test skips by design (no persistent filesystem).
Honest limit: the lane has never run on GitHub (Docker +
Buildx on ubuntu-24.04 verified against the runner-images
list; first real run happens there); the Dockerfile cannot
be built on this VM (no docker daemon here).

E-02: everything buildable without a VPS. New
scripts/vps-rebuild.sh (one command, 11 timed steps, role
root BEFORE schema_core.sql, idempotent, peer auth only, no
credentials, --dry-run) + puspa/teams/vps.py health checks
(Postgres connect + tables + RLS, dashboard HTTP probe on
/manifest.json, Docker precondition, offline deploy-file
validation, allowlist probe judging) +
docs/vps-validation.md (ordered procedure + timed drill
table + honest pending table) + 15 new tests in
tests/test_vps_validation_e02.py (including a real
encrypted backup-restore round trip on puspa_test_w2 with
identical row counts). Honest limit: the Contabo VPS is NOT
bought yet, so compose up, sandbox tests in a container,
the allowlist probe on the real egress path, restore on a
real VPS, and the timed live drill are all still PENDING.

VM replaced mid-wave (~02:36 WIB 20 Nov, uptime 0 min):
the first Wave 37 central suite was killed by SIGTERM at
~65% (no SUITE_EXIT) and re-run from scratch after
recovery. Postgres packages were restored by the system
replay itself (16.15 + pgvector 0.6.0 present, cluster
started manually); role root recreated, schema_core.sql
re-run, w1/w2 DBs recreated, RLS true on
users/sessions/vault in all three. New on this VM: a
`docker` binary exists (29.1.3) but with no compose plugin
and no daemon — coordinator fix in E-02 (in the E-02
commit): test_check_docker_available_matches_reality
assumed binary-present means the check passes; it now
handles the third honest state (binary without working
compose) and the stale "no docker binary" lines in the
test docstring + docs/vps-validation.md were corrected.

Central suite Wave 37 (re-run, log suite-wave37-b14-e01-e02-e03-e04-rerun.log):
4,065 passed, 24 skipped, 1 warning, SUITE_EXIT 0, 32m13s
(3,899 + 18 B14 + 0 net E-01 + 15 E-02 + 104 E-03 + 29 E-04).
The first full run went red on exactly one test
(test_backup_restore_round_trip_on_w2, E-02): the test
hardcoded "puspa_test_w2" as the manifest database while the
central suite runs on puspa_test — the same hardcoded-DB-name
trap as the K11 backup test. Coordinator fix (in the E-02
commit): the DB name is parsed from PUSPA_TEST_DSN and the
scratch restore DB is per-DSN (<dbname>_restore), so parallel
suites never race on a shared restore DB; the E-02 file then
re-ran 15 passed on the central DSN. The VM was replaced a
second time mid-wave (~02:48 WIB), killing the first retry
at ~14%; Postgres was recovered by the E-04 worker itself
(cluster start, role root, schema_core.sql, w1/w2 DBs, RLS
verified). E-03: new puspa/sandbox/egress.py (filtered egress
proxy: CONNECT + absolute-form, policy check before DNS,
pinned UDP DNS to 1.1.1.1/8.8.8.8, blocked-range checks
including 169.254.169.254, hash-chained JSONL egress ledger),
seccomp.py + vendored Docker default profile (moby/moby
v28.0.0, Apache-2.0, attributed in REUSE.md/NOTICE.md) + a
derived strict profile, runtime.py (runc/runsc/kata config
+ spawn-measurement harness, no runtime decision made yet),
warmpool.py (pre-created never-run containers, release
always rm -f + refill), tmpfs writes and a docker-flag
audit in the runner. Honest limits: no container has ever
run on this VM, so the flag derivations and pool create/start
paths are proven with fakes only; the egress ledger lives in
a JSONL file, not Postgres (the ledger has 31 closed event
types and no network type); egress.py is 967 lines, split it
before it grows. E-04: new puspa/nightly/ (7 probes:
OpenRouter turn + forced fallback, Ollama turn, skill
discovery, Google login, Telegram roundtrip + approval;
JSONL time-series store with pass rate, p50/p95 latency,
cost, shape_changed flag; runner with --only and exit 0
only when required probes pass) + .github/workflows/
nightly-smoke.yml (daily 02:00 UTC + dispatch; secrets by
name only) + Telegram callback-query support. Honest limits:
the lane has never run on GitHub (first live run needs a
human to create the low-capped keys and test accounts);
Google refresh tokens expire after 7 days in Testing mode;
the Telegram approval leg records a skip, not a pass, on
nights nobody taps. Shared-file note: .github/workflows/
ci.yml and docs/sandbox-deepening.md are shared by E-01,
E-03 and E-04, so the E-01 commit also carries the E-04
header comment and the E-03 section; the owning commits
show no diff for those lines. Honest limits: B14 fraud
table is a starting vocabulary, not a verdict; E-01 lane
never ran on GitHub; E-02 live checks wait for the VPS.

## Wave 39 — E-07 + E-08 (20 Nov ~07:1x WIB)

- E-07 single-writer lock across processes (plan 5.2): puspa/sessions/single_writer.py (301 lines: flock on state.db + Postgres advisory lock per agent id, fail-closed unless advisory_lock=False) + SessionGateway.open(dsn/agent_id/advisory_lock). Two-process proof: same volume refused by flock, different volume same agent refused by advisory, SIGKILL releases both. Honest limit: the literal two-container form cannot run live here (no Docker); the container form is CI / E-02 work. Commit 0bae7959.
- E-08 migration tool + schema snapshot (plan 5.3): 19 numbered migrations in dbmate-compatible format (puspa/migrations/versions/0001-0019) + embedded runner (python -m puspa.migrations up/down/status), schema_migrations table, one transaction per migration. All 27 loaders rewired to the runner; no Python creates tables anymore except the runner bootstrap + SQLite state.db. Committed snapshot infra/schema_snapshot.sql (6,129 lines, pg_dump -s of a migrated empty DB). Dev Postgres via docker-compose.dev.yml (pinned pgvector/pgvector:0.6.0-pg16); CI test + sandbox lanes pin the same image and run the runner instead of schema_core.sql. The old 21 SQL files are deleted from the repo. Equivalence proof (one-time, before rewiring): legacy-built DB vs migrated DB, 786 statements identical except the schema_migrations table itself. The rebuild script (scripts/vps-rebuild.sh) now runs the migration runner on the app DB + puspa_test (10 steps, was 11). Honest limits: container path never verified live here (no Docker); runtime tables (tasks, task_runs, task_steps, plans, handoff_requests, approvals, heartbeats) have RLS policies but no grants to puspa_app, so those policies are inert — follow-up ticket needed; pre-existing mypy errors puspa/rooms/research.py:435,438 untouched. Commit f960c39d.
- Incidents this wave: (1) the E-08 merge used rsync --delete from the worktree into build/ and deleted the already-merged E-07 test file (build/tests/test_single_writer_lock.py); the first Wave 39 suite went green at 4,103 instead of the expected 4,115, which exposed the loss. The file was rebuilt from its surviving .pyc (marshal + dis) — all 12 tests, fixtures and helpers recovered — and re-ran 12 passed. Rule from now on: never rsync --delete worktree into build/; merge by explicit file lists and delete a worker's deleted files by name. (2) VM replaced mid-wave (~23:27 UTC): the suite rerun was SIGTERM-killed at ~73%; Postgres recovered via the migration runner (role root, three DBs, python -m puspa.migrations up on each, RLS 3/3) — the E-08 recovery path proven end to end. (3) Five tracked PNG assets (docs/assets/*.png, logo + screenshots) were missing from the local tree; restored from HEAD before pushing.
- Central suite Wave 39 re-run: 4,115 passed, 25 skipped, 1 warning, EXIT 0, 33m32s (4,089 + 12 E-07 + 14 E-08). Notion E-07/E-08 → Done / Suite green / commit.

## Wave 40 — E-09 + E-10 + E-11 + E-12 (20 Nov ~09:27 WIB)

- E-09 WAL backup + restore drills (plan 5.4): puspa/backup/wal.py, local_state.py, drill.py; tests/test_backup_wal.py (23 tests) + tests/test_restore_drill.py (2 tests); runbook + drill log docs; infra/pgbackrest.conf.example; puspa/backup/backup.py translated to English + fixed the _pg_env bug (parts.get("users") never matched, so PGUSER was never set). Real drill (CLI, 20 Nov 05:32 WIB): PASS, 5.86s vs 1800s target, 13/13 checks green. Honest limits: not a live clean-host drill (local cp WAL backend; live drill waits for E-02); 5.86s is a tiny canary-only DB; dev cluster puspa_test_w1 still archive_mode = off. Commit 1a15cebb.
- E-10 server-rendered approval cards + narrowed "always" (plan 6.2-6.3): puspa/policy/approval_cards.py (card rendered server-side from structured args only; narrative ignored; hash = gate hash; verify_card() re-renders); puspa/policy/approvals.py narrowed "always" (target patterns + nominal cap; beta ban for pay/send_to_other); ApprovalFatigueTracker (sub-1s decisions counted as unread); 32 new tests. Honest limits: beta_mode defaults False — beta deployment must construct ApprovalPolicy with beta_mode=True (no production instantiation yet); ApprovalCard.tsx still accepts a summary field (frontend follow-up); fatigue clock starts at first tasks-screen presentation, not at actual viewing. Commit 45ea94d6.
- E-11 audit outside agent reach, daily anchoring, jti kill switch (plan 6.6): delta only vs K12. Migration 0020_token_revocations.sql (token_revocations per jti + agent_revocations revoke-all per agent); puspa/policy/revocations.py (DB-backed RevocationList); puspa/policy/stop.py (stop_agent() + StopReport); puspa/ledger/anchor.py (daily anchoring, LocalAnchorStore write-once, Ed25519-signed, CLI); puspa/ledger/audit_daemon.py (separate-process audit writer, INSERT-only role, Unix-socket JSON protocol). Tokens carry jti, issue_short_lived_token() default TTL 300s max 900s; gate.py checks durable revocation every call; stopwatch: 4 subagents in 0.068s (target under 5s). Honest limits: live external anchor not proven (no second VPS / bucket; local store is a stand-in with the same write-once semantics); anchor locks chain head + event count, not every row (mid-chain rewrites are caught by verify_chain itself); interruption is cooperative (RUNNING to CANCEL_REQUESTED to CANCELLED at next boundary); revoke-all cutoff is second-granular (same-second tokens fail closed); daemon socket auth is filesystem permissions only (0600 socket, 0700 dir). Commit c2a40efa.
- E-12 skill revocation, hashed deps, secrets + git history (plan 6.7-6.8): puspa/policy/revocation.py (signed revocation list, Ed25519 over canonical JSON, 7-day TTL; classes renamed SkillRevocationList/SkillRevocationError at merge — see below); tests/test_skill_revocation.py (31 tests) + tests/test_vault_rotation.py (13 tests); requirements-hashed.txt (176 packages, 4,031 lines, fresh uv resolution); .gitleaks.toml (9 false positives by fingerprint); .github/dependabot.yml (weekly pip + github-actions); skill_signing.py (signed_at, key_expires_at 90-day default, keyid in manifest; rejects expired/future/mismatched/revoked keys); marketplace/discovery.py (feature flag PUSPA_SKILL_INSTALL_ENABLED, default OFF); accounts/vault.py (MultiFernet: decrypt tries all master keys, encrypt uses primary; env PUSPA_VAULT_MASTER_KEY comma-separated + file PUSPA_VAULT_MASTER_KEY_FILE mode 0600; new rotate_entry() + rekey_all()); CI supply-chain lane (hashed-file sync check, pip-audit, CycloneDX 1.6 SBOM + artifact, gitleaks full history fetch-depth: 0; in the all-checks-pass gate). Gitleaks full-history scan (mirror clone, 173 commits): 168 commits scanned, 9 findings, all false positives, 0 real secrets; re-scan clean. pip-audit on hashed file: exit 0, 0 vulnerable. SBOM: 177 components. Honest limits: GitHub settings (secret scanning, push protection, Dependabot activation) need a human with repo settings access — files ready, settings not done, do not claim; CI lane never ran on a GitHub runner; revocation list has no host yet (PUSPA_REVOCATION_LIST_URL / PUSPA_REVOCATION_ISSUER_KEY unset — refresh job reports unconfigured/unavailable). Commit d9d85a02.
- Merge collision this wave: E-11 and E-12 both defined RevocationList/RevocationError (E-11: DB-backed token list in puspa/policy/revocations.py; E-12: signed skill list in puspa/policy/revocation.py). E-11's tests import from the package, E-12's from the module — resolved by renaming E-12's classes to SkillRevocationList/SkillRevocationError and exporting both at package level with an explanatory comment in puspa/policy/__init__.py. The __init__.py was reconstructed per commit (E-10 version, E-11 version, merged version) so each commit imports cleanly on its own.
- Incidents this wave: (1) first central suite run RED: 4 failed, 4,168 passed. Root causes: E-10 grew puspa/policy/approvals.py to 1,047 lines with no BASELINE_LINES entry (ratchet); E-09 drill.py held a CREATE TABLE string that was SQLite, not Postgres (DDL scanner) — fixed by building the fake state.db through the real sessions gateway; test_loader_targets + test_rls_isolation_on_migrated_schema collided because the E-11/E-12 workers ran test_migrations.py concurrently on the same cluster and their scratch_dsn fixtures shared names (databases dropped mid-test). Fixed: scratch DB names now carry the pid (puspa_migtest_<pid>_<test>). All four failing tests + module tests re-verified: 67 passed, ruff clean. (2) Re-run 1 was SIGTERM-killed at 89% by a VM replacement (20 Nov 01:27 UTC, 5th replacement that day); Postgres recovered via the migration runner (apt install ~7 min, role root, three DBs, python -m puspa.migrations up on each, RLS 3/3). (3) Notion E-09 update first failed on a mistyped page id (404); retried with the id from notion-rows.json and succeeded.
- Central suite Wave 40 re-run 2 (covers E-09 + E-10 + E-11 + E-12): 4,244 passed, 25 skipped, 1 warning, EXIT 0, 41m49s (4,115 + 25 + 32 + 26 + 44 + 2 extra E-12 neighbor tests). Notion E-09..E-12 → Done / Suite green / commit.

## Wave 41 — E-13 + E-14 (20 Nov ~10:4x WIB)

- E-13 RLS differential tests + room membership policy (plan 6.9 + 12): tests/test_rls_differential.py (46 tests: same read through code layer alone, RLS layer alone, and HTTP; 8 per-user tables + 15 room tables; canary user with one marker row per table, marker rls-canary-e13 must never leak to another user; removed-member loses access on all three layers; fail-closed without a user). docs/rls-differential.md (decisions + notes). Real bug found and fixed by migration 0021_mockup_rls_grant.sql: the mockups_owner policy (migration 0015) read app.user_id, a variable nobody ever sets, and the table had no grant to puspa_app, so RLS denied even the owner's reads; the code layer hid it because MockupStore runs as owner. Honest limits: per-user stores trust the caller-supplied user_id (documented as a test, not fixed here — protection is HTTP session binding + RLS); 37 tables are RLS-enabled but have no grant to puspa_app, so their policies are dead letters until each gets a grant-or-remove-policy decision; no-FORCE-RLS decision kept (written reasons in the doc); composition gap — no production path runs both layers at once today, so the suite proves each layer separately, not the combined path; moving read services to puspa_app is a follow-up. Commit 244ba10c.
- E-14 visible embedding degradation (plan 7.4): migration 0022_embedding_health.sql (episodic.embedder_id + dimension + CHECK + index; tables embedding_state singleton, embedding_reembed_jobs, embedding_canary_baselines; puspa_app grants). puspa/retrieval/embedding_health.py (963 lines: EmbeddingHealth, evaluate_startup, fallback + canary-recall alarms, state/baseline/job DB layer, record_degraded_to_ledger) + canary_questions.py (frozen 30-question set, split out to stay under the 1000-line soft cap); puspa/pwa/health.py (GET /health payload + operator banner); write path refuses an embedder that differs from the active one (first writer claims it; change only via tiered re-embed job: 7 days, 90 days, rest); search filters by the querier's embedder_id + dimension; production hash-only starts degraded by default or refuses with PUSPA_EMBEDDING_PRODUCTION_POLICY=strict; degraded shows in /health, the ledger (heartbeat event, kind embedding_degraded — the closed set of 31 event types stays closed), and the operator banner. Canary verified live: HashEmbedding recall 1.0 (30/30), lossy first-token embedder 0.167 (5/30) — alarm fires on an 83% relative drop. Honest limits: the real fastembed model was never loaded in tests (stubs only); the baseline reference is 1.0 from HashEmbedding; the startup ledger write swallows DB errors by design (degraded still shows in /health + banner); pre-existing Indonesian text remains in episodic.py, app.py (module/function docstrings from earlier waves — the worker added English lines only, no rewrite). Commit 42b51b59.
- Merge collision this wave: both workers created migration 0021 (E-13 mockup grant, E-14 embedding health). Resolved by queue order: E-13 keeps 0021, E-14 renumbered to 0022 (file + schema.py target + comments). test_migrations.py merged to 22 by hand. Coordinator merge bug, caught by the merged run: the rollback-to-0019 test rolls back 3 migrations, not 2 — first merge mapped ["0021","0020"] to ["0022","0021"]; fixed to ["0022","0021","0020"] (re-apply ["0020","0021","0022"]), then 88 passed. Lesson: read the rollback TARGET before shifting numbers. The E-15/E-16 refresh overwrote w1's E-13 snapshot + test_migrations.py (rsync without --delete copies build/ over the worktree but leaves stale files), so the E-13 commit's 21-version files were reconstructed by the coordinator: test_migrations.py rebuilt by reversing E-14's hunks (verified: diff vs HEAD's 20-version shows only the 20 to 21 count changes) and the snapshot regenerated from a temp DB at target 0021 (verified: diff vs HEAD's snapshot shows only the mockups policy fix + grant). Lesson: refresh worktrees with --delete at spawn time (safe then — all prior work is merged) so stale files cannot linger and duplicate a migration version.
- Incidents this wave: VM replaced a 6th time (after the Wave 42 suite finished green); Postgres was gone entirely (no packages, no cluster, no socket). Recovered via apt install + role + three DBs + migration runner (22 migrations, RLS 3/3). The second suite warning is pre-existing (PytestUnhandledThreadExceptionWarning in test_sandbox_e03_egress.py::test_pinned_dns_fails_closed_when_no_server_answers), not from E-13/E-14.
- Central suite Wave 42 (covers E-13 + E-14): 4,318 passed, 25 skipped, 2 warnings, EXIT 0, 48m40s (4,244 + 46 + 28). Notion E-13/E-14 → Done / Suite green / commit.

## Wave 42 — E-15 + E-16 (20 Nov ~15:xx WIB)

- E-15 curriculum runner guardrails (plan 7.5): puspa/memory/curriculum_guardrails.py (DailyCaps: per-user 5 tasks / Rp10,000 per day, global 50 tasks / Rp100,000 per day, reservation-enforced so recorded spend can never exceed a cap; WeeklySkillCap: max 3 new skills per ISO week, repairs excluded; classify_experience quarantines orphan-number or unverified experience) + scheduler/curriculum.py (runner uses caps; failed runs quarantine instead of teaching; verified completions clear gaps; propose_next_task(exclude=...) so an open gap does not wedge the runner) + learning_loop.py (record_misprediction unverified/orphan-numbered quarantines; record_feedback with orphan-numbered corrections held until confirmed; record_experience / quarantine_experience / list_quarantined / confirm_experience; draft_skill under the weekly cap; evaluate_holdout + submit_for_approval; activate_skill refuses skills that have not passed holdout and been submitted). 36 new tests. Honest limits: the cap numbers are the worker's choice (plan 7.5 names none) and are parameters, not Nero's decision; user corrections are quarantined only when orphan-numbered (plain user corrections still apply at once — the user is their own verifier); world-model observations still teach by default — only the runner path is locked; quarantine + caps are in-memory only (lost on restart — persistence is a follow-up ticket). Commit 61f6536c.
- E-16 attack corpus, benchmark, mutation, scorecard (plan 8): new package puspa/eval/ (13 modules: intervals with Wilson + rule of three, corpus loader with hash-manifest verification, attack harness with TransformAttacker, 10 reference tasks, replay cassettes, BM25, retrieval sets, gold sets, AST mutation runner + CLI, gateway fuzzer, scorecard assembler + CLI) + data (attack_corpus_v1.json, benign_indonesian_v1.json, 3 gold sets, public_sources.md) + docs/eval-e16.md + eval-results/scorecard.json + scorecard.md; tests/test_eval_*.py (10 files, 50 new tests); requirements-dev.txt gains hypothesis 6.170.16 + mutmut 3.8.1. Measurements (20 Nov 2026, in eval-results/): reference tasks 30/30 (3 repetitions), replay cassettes 10/10 identical (gate 12 target 9/10); retrieval recall@5 BM25 0.84, plain vectors 0.86, HippoRAG 0.74 (answer accuracy 0.84 / 0.90 / 0.74) — HippoRAG does NOT beat plain vectors, so its triple-extraction cost is not justified by these numbers (decision needed before beta); attack corpus 60 live payloads (140 empty slots): 30 blocked, 25 held at approval, 5 success, ASR 5/60 (Wilson 95% 0.036-0.181); goal 1 (leak) 5/15 success (ATK-003, ATK-008, ATK-010, ATK-013, ATK-014 bypass scanner + input rail + output rail), goal 2 0/15, goals 3-7 0; beta target (ASR zero for goals 1 and 2) NOT reached; stress scenario (broad "always" share) 6/60; scanner false positives 7/40 (17.5%), tricky 4/10 (40%); utility under attack 40/40; gold sets: entity identity precision 0.83 / recall 1.00, quoted-number accuracy 30/35 (0.86), orphan hold rate 1.00, wrong-hold 0.00; gateway fuzz 500 iterations, 0 unexpected exceptions; mutation on puspa/policy/tokens.py (first 10 mutants): score 0.333 — 3 killed, 6 survived, 1 timeout (survivors are real test gaps: TOOL_PERMISSION_RANK values, _normalize_id non-string guard, jti_of isinstance guard, _validate_payload tools-string guard). Honest limits: the 60 live payloads were written by one internal corpus author, not people outside the team; the adaptive-attacker (60) and public (80) slices are empty slots — no attacker model ran, no public payload text imported (licenses recorded: garak Apache-2.0, AgentDojo MIT, 2WikiMultihopQA unconfirmed); ASR measures the deterministic stack (scanner, rails, approval policy), not end-to-end agent behavior with a live model; 0 successes from N means an upper bound of 3/N at 95% confidence, not "safe"; kill drill and restore drill not measured. Defense findings (5 goal-1 bypasses, scanner FP, mutation survivors) need separate defense tickets. Commit 3eea2f45.
- Incidents this wave: (1) first central suite run RED: 1 failed (test_files_over_soft_cap_only_shrink) — E-15 grew puspa/memory/learning_loop.py to 1,215 lines with no BASELINE_LINES entry. Fixed by recording it at 1,215 with the E-15 reason + split candidate (quarantine store or holdout evaluation into its own module); rides in the E-15 commit. (2) Re-run 1 SIGTERM-killed at 89% by VM replacement #7; re-run 2 SIGTERM-killed at 99% by VM replacement #8 (all dots green to 99%). Re-run 3 green. Each replacement wiped Postgres; recovery is now routine (replay apt first — do NOT kill it — then install postgresql-16 + pgvector, role root, three DBs, migration runner, RLS 3/3). The E-16 worker deleted a stale duplicate migration (0021_embedding_health.sql, byte-identical to 0022) from its worktree — correct, it was a leftover of the no---delete refresh; build/ never had it and HEAD is clean.
- Central suite Wave 43 re-run 3 (covers E-15 + E-16): 4,404 passed, 25 skipped, 1 warning, EXIT 0, 36m08s (4,318 + 36 + 50). Notion E-15/E-16 → Done / Suite green / commit.

## Wave 44 — E-17 + E-18 (20 Nov ~15:4x WIB)

- E-17 per-worker test databases + PR lane (plan 9.1): tests/worker_db.py (template DB built once from migrations, fingerprinted, advisory-lock serialized; CREATE DATABASE ... TEMPLATE per xdist worker; synchronous_commit = off; SOLO untouched), scripts/affected.py + pr_lane.py + setup_test_db.py + test_postgres_tmpfs.sh, tests/test_worker_db.py + tests/test_pr_lane.py (26 new tests), pytest.ini markers (unit/integration/live), tests/conftest.py per-worker cloning, pyproject.toml mypy overrides, requirements-dev.txt gains pytest-xdist==3.8.0, CI test lane runs on 4 xdist workers (was SOLO), pr-lane job added, nightly lane runs -m live, docs/verification.md drops the SOLO-only rule. Markers added across 20 existing test files. Measurements on this VM (1.5 shared CPU): SOLO 26m02s, -n 4 shared cluster 20m47s, -n 4 tmpfs 15m25s. Honest limits: the under-10-minute target is NOT reached on this VM (CPU-bound, not DB-bound — record as VM numbers, not CI numbers); fsync is cluster-wide and cannot be turned off per database — a fully tmpfs cluster with fsync off is opt-in via the script. Commit 148ab75f (PR #31, squash-merge, first PR-flow push).
- E-18 observability + alarms (plan 9.2): new package puspa/observability/ (tracing.py — trace id = task id, OTel trace id derived deterministically via sha256(task_id), local ring buffer max 1000 + OTel SDK in-memory exporter, no OTLP endpoint; metrics.py — all plan 9.2 metrics, in-memory, JSON-safe snapshot(); alarms.py — 5 alarms: cost per task over Rp2,000, embedding fallback over 0 (production only), sandbox p95 over 3s, provider quota under 10% remaining, kill drill failed (raise or over 5s); logging.py — RedactingFormatter using puspa.credentials.redact, [trace_id=...] prefix, rotating file handler 1 MB x 5; health.py — observability_block() shared by /health and operator). Wiring (all fail-open, wrapped in suppress): turns/loop.py, turns/turn_api.py (thread fix — call_model runs on the turn-model-call thread and contextvars do not cross threads, so the trace id is captured on the calling thread and re-set on the worker thread), router/fallback.py, router/router.py, sandbox/warmpool.py + runner.py, evidence/renderer.py, scheduler/scheduler.py, pwa/app.py + health.py + serve_modes.py, retrieval/embedding_health.py; requirements.txt gains opentelemetry-api==1.45.2 + opentelemetry-sdk==1.45.2; requirements-hashed.txt regenerated with uv (openai 3.56.0 to 3.57.0). 41 new tests. Honest limits: kill drill is an operator entry point only (no scheduled/automatic drill); approval latency tested at metric level only, not via HTTP TestClient; sandbox cold docker run path recorded as separate sandbox_run_ms, not in the p95 alarm; TTFT = time of first model reply (model calls are not streamed). Commit 40b325dc (PR #32, squash-merge).
- First PR-flow wave: E-17/E-18 are the first items pushed via one branch per item + PR + squash-merge (new commit format: conventional subject, 3-line body, Ticket footer). Tooling: hidden_files/pr_push.py + hidden_files/diff_vs_head.py. Incident during the E-17 push: the flow completed (PR #31 merged, branch deleted) but the script raised DynamicCredentialError on the branch-delete call — GitHub answers DELETE ref with 204 + empty body, which the shared helper cannot parse as JSON. Fixed by verifying deletion with a GET (404 = gone) instead of trusting the exception; the E-18 push then ran clean. Lesson: always verify the remote state before retrying a push step — the first run had already merged.
- Incidents this wave: (1) first -n 4 central suite run RED on the ratchet only — puspa/turns/loop.py grew to 1,080 lines (E-18 observability wiring) with no BASELINE_LINES entry. Fixed by recording it at 1,080 with the E-18 reason + split candidate (model-call step or tool-call loop into its own module); rides in the E-18 commit. Re-verified: ratchet + E-17/E-18 tests 70 passed, ruff clean. (2) A leaked build artifact (apps/pwa/tsconfig.tsbuildinfo) was found in build/ and removed, never pushed.
- Central suite Wave 44 re-run (-n 4, first time as the gate): 4,471 passed, 25 skipped, 5 warnings, EXIT 0, 16m55s (4,404 + 26 + 41). -n 4 is about 2.1x faster than the SOLO Wave 43 run (36m08s to 16m55s) and stays the gate; the under-10-minute target is NOT reached on this VM (CPU-bound). Notion E-17/E-18 → Done / Suite green / commit.
## Wave 45 — E-16 follow-up + E-19 + E-20 + E-21 (20 Nov ~17:xx WIB)
- Central suite re-run (-n 4): **4,593 passed, 25 skipped, EXIT 0, 15m37s** (log suite-wave45-e19-e20-e21-followup-rerun.log). Count reconciles: 4,471 + 40 (E-19) + 6 (E-20) + 60 (E-21) + 16 (follow-up: 15 in two new files + the dedup test in test_retrieval_hipporag.py). First run RED on 5 tests (1 ratchet + 4 retrieval), fixed by the coordinator before the re-run — see the follow-up notes.
- Follow-up E-16 (issue #30): pushed d83a86b1, PR #34 (Closes #30). Attack ASR 0/60 (was 5/60), goal-1 0/15, benign FP unchanged 7/40, HippoRAG recall@5 0.74 -> 0.84 (accuracy 0.74 unchanged), mutation 0.333 -> 1.000. The strict one-result-per-document dedup was narrowed by the coordinator after the first suite run caught 4 regressions (recall@10 1.0 -> 0.875 on the price-graph and judge sets): dedup now applies only with a reranker and top_k <= 5; the unconditional form is recorded in docs/eval-e16.md. Ratchet: approvals.py recorded at 1051 (beta share ban).
- E-19 (issue #12): pushed 37f400bd, PR #35 (Closes #12). Nanobot pin record + contract checks + bump script + monthly bump and weekly advisory-watch workflows. Live check: bump available, latest upstream 3e9024ea, 1 open advisory GHSA-434r-7c99-hwf3 (medium, SSRF in web_fetch, >30 days, fork rule not fired). Honest limits: no live bump PR; workflows never ran on GitHub; canary/rollback never ran on a real host.
- E-20 (issue #13): pushed 8a80fe11, PR #36 (Closes #13). Blind-except baseline counted 192 (11 narrowed, fallbacks unchanged), bandit baseline 119 findings (0 high), mypy strict on five packages (1,726 -> 1,713 errors; 615 -> 602 in the packages). Honest limits: mypy NOT strict yet (602 errors remain); bandit B608/B105 verdicts sampled, not exhaustive. The payments_api.py overlap with E-21 (both narrowed the same except clause) was split at push time: E-20's commit carries only its narrowing, E-21's carries the flags catch.
- E-21 (issue #14): pushed 87e299a7, PR #37 (Closes #14). One central flags file + beta profile (all six surfaces off) + canary compose override + one-command rollback + expand-then-contract rules. Honest limits: canary/rollback never ran on a real host (Docker cannot run on this VM); the 5% split and 24h window are unvalidated choices.
- Honest limits carried forward: HippoRAG answer accuracy 0.74 needs a real embedding model (model limit, not a ranking bug); a broad "always" grant for "post" is still recordable in beta (needs a product decision); E-19/E-21 workflows never ran on GitHub (first real run happens there); CI has still never run end-to-end on GitHub.
- Wave 28 (K11 + K12) archived verbatim to docs/status-maturity-archive-2026-11-20-waves-28.md (280 lines) to keep the live log under the soft cap; the live log is now 692 lines.
- P-01 worker still building in w2 (started during the Wave 45 suite). Next pair after the Wave 45 docs push: P-01 + P-02.
## Wave 46 — P-01 + P-02 + P-03 (20 Nov ~18:xx WIB)
- Central suite re-run (-n 4): **4,711 passed, 25 skipped, EXIT 0, 14m54s** (log suite-wave46-rerun.log). Count reconciles: 4,593 + 17 (P-01) + 54 (P-02 new files) + 10 (test_autonomy_dial.py additions) + 37 (P-03). First run RED on 3 tests, all fixed by the coordinator before the re-run — see the incidents below.
- P-01 memory read endpoints + memory screen (plan 12.6, issue #15): pushed a8719f80, PR #39 (Closes #15). MemoryReadService (facts read with source + confidence, consent status, guest mode) + GET /api/memory, /api/memory/facts/{id}, /api/memory/export, /api/memory/consent/withdraw, /api/memory/guest + the memory screen (facts with correction/forget, proposals, consent + export) + migration 0023_memory_rights (withdrawn_at, confidence + source_detail + CHECK). 17 new tests. Honest limits: forgetting removes facts + versions, not the source chat episodes; consolidation confidence still dropped at propose time (proposals show derived 0.5); correction commits before its ledger record; guest mode process-local, PWA /api/chat is an in-memory stub so no live chat turn observes guest mode yet; withdrawing consent stops future storage without deleting stored data.
- P-02 stop-all + autonomy dial + weekly report (plan 12.7, issue #16): pushed ad5f0e08, PR #40 (Closes #16). Red two-tap stop-all button in the PWA header (stops every agent the user owns + live subagents, with a stop report and audit-trail link), the autonomy dial in Settings (5 areas x Ask first / Balanced / Autonomous, raises need 10 accepted drafts in a row, Money locked to Ask first forever), and the weekly report in Insights (tasks done/corrected, cost, evidence links; time/money saved shown as unmeasured). 54 new tests + 10 added to test_autonomy_dial.py. Honest limits: dial/ceilings in-memory only (restart resets to Ask first — the safe direction); the turn loop consults the dial only in-process; Shopping is not consulted for tool calls (payments still go through Money, always Ask first); evidence streaks only 90 days back, PWA draft approvals write no ledger event; stop-all touches neither the global kill switch nor deletes anything; no real-browser check.
- P-03 PWA share target + voice messages (plan 12.4, issue #17): pushed d046c2bd, PR #41 (Closes #17). Web Share Target (manifest share_target, POST /share; shared text/URL lands as a prefilled chat draft, files go to the per-user share inbox, nothing auto-sent) + voice messages (record, transcript shown for confirmation/editing, send on confirm or discard; pluggable transcriber seam + WER harness + honest /api/voice/status). 37 new tests. Honest limits: share target verified only in the test harness, no live Android device; iOS Safari does not support Web Share Target; NO real ASR engine chosen (stub transcriber; engine selection awaits the 50-recording WER measurement); voice recording verified in jsdom only. No WhatsApp build (Meta bars general-purpose AI assistants on WhatsApp Business API from 15 Jan 2026).
- Incidents this wave: (1) VM replaced mid-wave (~11:10 UTC): Postgres 16 + pgvector + all 3 test DBs gone; restored the same turn (apt install, cluster start, role root + 3 DBs, all 23 migrations on each, RLS verified 75/100 tables, 131 policies). P-04 worker survived but its DB was gone — notified to verify migration status before testing. (2) First central suite run RED on 3 tests: bandit B101 37 vs baseline 36 (P-03's redundant `assert keep_dir is not None` in voice/pipeline.py — the guard above already raises; deleted, rides in the P-03 commit); the lite memory-page test (P-01 moved reads behind MemoryReadService, so the old profile-module patch stopped working; fixed in the test to patch the service seam + give the fake fact the full shape the template reads, found via the `f.confidence` UndefinedError); the hypothesis token-subset test (flaky, not a code bug — passed solo 4x and the file 2x; likely the 200ms hypothesis deadline under -n 4 load on 1.5 CPUs; no code change, passed in the re-run). (3) P-02 merge: the coordinator's merge script cut fetchWeeklyReport's closing brace (diff hunks can end mid-function) — caught by tsc, fixed; and app.py crossed the 1000-line soft cap (943 -> 1009 from P-01+P-02+P-03 wiring) — recorded as a new BASELINE_LINES entry with reason + split candidate, not a silent raise. (4) Push reconstruction: the first YAML property removal over-deleted (ate the following schemas' headers) — caught by the spec line count (1924 vs expected 1946) and the regeneration failure; fixed with indent-based removal. A span removal swallowed sendChat (it sits between postMultipart and the voice functions) — caught by the diff-vs-main verification. (5) Found while reconstructing: main's committed generated-types.ts was STALE (the /api/payments and /health paths were already in the spec but missing from the types). P-02's regeneration from the spec picks them up; noted honestly in the P-02 commit body.
- Shared-file merge note (P-02 vs P-03): w1 was refreshed before P-03 merged, so P-02's shared files (app.py, client.ts, client.test.ts, openapi-pwa.yaml, generated-types.ts, ratchet) were hand-merged onto build/'s P-03 versions by script (/tmp/merge_p02.py, verbatim hunks at string anchors) and verified by diff on both sides; the per-item pushes then reconstructed each item's own versions from build/ (/tmp/reconstruct_wave46.py) and verified them the same way (vs main: only this item's hunks; vs build/: only the other items' hunks) plus py_compile/ruff/YAML/TS-syntax checks. Final tree check after all three pushes: every blob on main matches build/ byte-for-byte (git hash-object, 0 mismatches, 0 missing).
- Docs gap found (not fixed in this wave): docs/modes-decision.md and docs/tickets-beta-plan.md exist in build/ but return 404 on main — they were never pushed. Flagged to the owner for a decision; the Wave 46 docs push carries status-maturity.md only, per the established pattern.
- Notion P-01/P-02/P-03 → Done / Suite green / commit. P-04 (receipt/note photos, §12.5) still building in w2; pairs with P-05 next.
## Wave 47 — Receipt photos (20 Nov ~20:xx WIB)
- Central suite re-run 2 (-n 4): **4,763 passed, 25 skipped, EXIT 0, 21m03s** (log suite-wave47-p04-rerun2.log). Count reconciles: 4,711 + 52 new tests. Two red runs before it — see the incidents below.
- Receipt/note photos (plan 12.5, issue #18): pushed f5a881fc, PR #43 (Closes #18). New package puspa/receipts/ (OCR seam + stub + mapping engine, parser with per-line evidence ids, identity/official-document detection + refusal, pipeline with Pillow validation, per-field accuracy harness) + puspa/pwa/receipt_api.py (upload -> draft -> confirm/discard, expenses list, honest status) + ReceiptUpload.tsx + docs/openapi-receipts.yaml (393) + docs/receipts-photo.md + 3 test files (52 tests) + fixtures (warung, supermarket, hostile, KTP/photo PNGs). 52 new tests. Honest limits: NO real OCR engine (engine_selected: false; tesseract/easyocr absent from the VM); confirmed expenses live in app.state only (no migration — lost on restart); no real-device check (harness + jsdom only); one photo per upload; rupiah only; the 4 receipt client functions have no client.test.ts coverage (the worker's client.test.ts changes were the voice tests, already in build/).
- Incidents this wave: (1) VM replaced ~13:06 UTC mid-suite (third today): the re-run 2 suite was SIGTERMed at 61%. Recovery: the boot os-intent replay held the apt lock (reinstalling recorded packages incl. docker.io) — waited for it instead of fighting it; postgresql-16 16.15 + pgvector installed, cluster online, role root + 3 DBs, all 23 migrations on each, RLS verified 75/100 tables, 131 policies, vector extension present. (2) First suite run RED on bandit B101 37 vs baseline 36: a dead `assert result.draft is not None` in receipt_api.py (proven dead — the pipeline returns a draft whenever it does not reject, and the endpoint returns 422 first). Deleted, rides in this commit; targeted re-check 55 passed, ruff clean. (3) Re-run 1 RED on the hypothesis flake `test_token_subset_is_reflexive` — this time `FailedHealthCheck: Input generation is slow` (2 valid inputs after 1.09s), not the deadline Wave 46 guessed: wall-clock starvation under -n 4 load + the w1 worker on 1.5 CPUs. Fixed narrowly with `@settings(suppress_health_check=[HealthCheck.too_slow])` on that one test, reason recorded in a comment; the property is still checked over 50 examples. File passes 3x solo. Rides in this commit. Lesson: a flake failing twice in four runs is not a one-off — fix it narrowly with the reason recorded instead of re-rolling the 19-minute suite. (4) Merge: w2's shared files were based on the P-01 state and lacked all P-02 content — hand-merged per-hunk verbatim from w2 (/tmp/merge_p04.py, assertions on every anchor), never copied wholesale; post-merge diffs show only P-02 content remaining. The same anchor class of bug as Wave 46 bit once: inserting the receipt functions AFTER the createTask doc-comment anchor glued that comment onto uploadReceipt's opener — fixed by moving the comment back above createTask. Lesson: when the insertion anchor is the FOLLOWING function's doc comment, insert BEFORE it, not after it. (5) The merged spec hit 2002 lines, 2 over the 2000-line hard cap: the 18 HTML page routes split out to docs/openapi-pages.yaml (272 lines; Error/Manifest/HealthStatus referenced back to the main spec, the voice-file precedent); the main spec is now 1783 lines and the regenerated client types are byte-identical before/after the split. Ratchet: app.py 1009 -> 1028, spec 1980 -> 1783.
- Commit hygiene note: the Wave 46 docs commit subject carried codes in parentheses and was amended by the owner to "docs: record Wave 46" (new tip 92302b14, same tree). This wave's commits carry no codes anywhere — verified on the pushed message (zero matches). The P-04 branch was created from the new tip.
- Notion P-04 → Done / Suite green / commit. P-05 (shared rooms lite) still building in w1; pairs with P-06 next (spawn in w2, refreshed from build/ post-P-04).
## Wave 48 — Shared rooms lite (20 Nov ~21:35 WIB)
- Central suite re-run (-n 4): **4,785 passed, 25 skipped, EXIT 0, 16m29s** (log suite-wave48-p05-rerun.log). Count reconciles: 4,763 + 22 new tests. First run RED on 2 tests, both fixed by the coordinator before the re-run — see the incidents below.
- Shared rooms lite (plan 12.1, issue #19): pushed d50871df, PR #45 (Closes #19). Rooms as a resource: create/join via link (7-day TTL) or 6-digit one-time code (24h TTL), Owner/Member/Guest roles with guest read-only enforced in the service layer and at HTTP, three memory layers with walls (personal, room, bring-personal-into-room per item), membership-based RLS (E-13, reused), removed member loses access instantly (403 + SSE cut + personal notes gone, shared notes stay), per-room serial job queue (migration 0024 room_job_queue, advisory lock per room, summarize/note handlers), room activity over the existing SSE stream, and the Rooms screen in the PWA. 22 new tests (13 API + 9 queue) + 7 vitest (rooms state + Rooms screen). Honest limits: productive extras inside rooms NOT built (meeting notes to actions, shopping/dues, debt notes, group reminders, light voting); the agent does not speak on its own in rooms and jobs run only when a member runs them; Telegram stays on MockTransport; the P-01 /api/memory/rooms/* alias stays; bring origin recorded as a trailing line in the note, not a separate column; SSE is a snapshot on connect, not a long push.
- Incidents this wave: (1) First central suite run RED on 2 tests: the blind-except ratchet (P-05 added 3 swallowing handlers — the SSE frame, the job runner, and a thread test) and the live-marker test (transient collection error under load; passed 3x solo right after). Fixed by the coordinator, riding in this commit: the SSE handler narrowed to its provable failure modes (psycopg.Error, ValueError, TypeError, KeyError, AttributeError) with the modes recorded in a comment; the thread test restructured to ThreadPoolExecutor + as_completed so worker failures re-raise in the main thread and no handler is needed; the job runner recorded in the blind-except baseline at 1 with the reason in docs/e20-blind-except-audit.md section 2.1 (a job runner must isolate arbitrary handler failures — narrowing would let an unexpected handler failure stop the whole queue), total 192 -> 193. The narrowing grew app.py by 5 lines, so its file-size baseline moves 1064 -> 1069 with reason. Targeted re-check 58 passed, ruff clean; re-run green. (2) Merge: w1 was based on the pre-P-04 state, so its shared files lacked all P-04 content — hand-merged per-hunk verbatim from w1 (/tmp/push-wave48/merge_p05.py, assertions on every anchor), never copied wholesale; the P-05 block in client.ts goes BEFORE the B18 doc comment (the Wave 46/47 anchor lesson). The generated types were regenerated from the merged spec (only room types added; SseEvent gains room_activity). (3) VM replaced mid-wave in w1 (the P-05 worker's first neighbour batch SIGTERMed at ~712s; Postgres restored by the coordinator); the worker re-ran both neighbour chunks clean (303 passed). Four failures in the worker's old concurrent batch were flakiness (all four passed solo, never recurred in the clean sequential runs). (4) Found while merging: puspa/rooms/rooms_api.py is a local-only duplicate shim (imports puspa.pwa.rooms_api, nothing imports it, not on main) — kept in sync locally, never pushed (standing never-push list, same as docs/modes-decision.md).
- Notion P-05 → Done / Suite green / commit. P-06 (mockup canvas v1) still building in w2; pairs with P-07 next (spawn in w1, refreshed from build/ post-P-05).
## Wave 49 — Mockup canvas v1 (20 Nov ~22:10 WIB)
- Central suite (-n 4): **4,788 passed, 25 skipped, EXIT 0, 18m58s** (log suite-wave49-p06.log). Count reconciles: 4,785 + 3 new tests. Green on the first run — no re-run needed.
- Mockup canvas v1 (plan 12.2, issue #20): pushed d445845e, PR #47 (Closes #20). A delta on B7, not a rebuild: B7 already covered the beta scope, so the inventory found three real gaps and fixed them. (1) Asset-loss bug: image upload at POST /api/mockups/{id}/assets mutated the stored doc without an op, so the version did not bump and a cached undo session overwrote the doc without the asset on the next edit — reproduced with a failing test first, then fixed: the upload now writes a numbered `add_asset` op (cap 32 assets, 256 KiB each, data URIs only). (2) The editor had no reorder or replace-image controls although backend + state already supported `reorder`/`set_image` — now Move up/down, Add image, Replace image buttons. (3) The canvas hid child nodes (a catalog card showed only the label "card") — child nodes now render inside their parent, and image nodes show the real image. 3 new tests (2 doc-level `add_asset` + 1 API regression: op -> upload -> op, asset survives, version 3). Honest limits: the personal brand library (logo, colors, font, tone) is named in the beta-scope sentence but has no criteria in the Notion page and needs a new table plus migration, so it is NOT built here — it is a follow-up proposal for the owner to decide (new ticket or a scope call), not a silent omission. Vision-model self-critique stays deterministic (geometry/contrast rules, not a vision model on pixels). No module binding, no Figma import. Drag-and-drop and upload verified in jsdom only, not in a real browser or phone. The Notion product metrics (prompt to first export under 3 minutes, export-without-hand-edit rate, template reuse) cannot be measured from code.
- Merge: w2 was based on the post-P-04 state, so its shared files predated P-05 — 10 files taken wholesale from w2 (build/ == base for all of them, verified against main a60a2c6c); client.ts hand-merged (the 8-line `addMockupAsset` wrapper appended at EOF, P-05's room block intact); generated-types.ts regenerated from the merged spec (only the `add_asset` enum value + the asset-endpoint description changed). No migration, main spec untouched (1783 lines). The worker's neighbour run hit a fixture error from two parallel pytest runs sharing puspa_test_w2 — run serially in build/, all 95 neighbours passed.
- Notion P-06 → Done / Suite green / commit. P-07 (morning summary + notes-to-actions) still building in w1; pairs with P-09 next (spawn in w2, refreshed from build/ post-P-06).

## Wave 50 — Morning summary + notes-to-actions (20 Nov ~23:20 WIB)
- Central suite (-n 4): **4,824 passed, 25 skipped, EXIT 0, 20m07s** (log suite-wave50-p07.log). Count reconciles: 4,788 + 35 new tests + 1 lite screen case for the new screen (test_webui_lite.py parametrizes over SCREENS). Green on the first run — no re-run needed.
- Morning summary + notes-to-actions (plan 12.8, issue #21): pushed 03889580, PR #49 (Closes #21). Morning summary (GET /api/morning/summary, GET /api/morning/status): schedule from Google Calendar, bill candidates from Gmail, open tasks, national holidays from Nager.Date, prayer times from Aladhan (Kemenag RI method 20); each source reports its own honest status and a failing source drops only its own section; imsak shows only during Ramadan. Notes-to-actions (POST /api/morning/notes/text, POST /api/morning/notes/voice, GET /api/morning/notes[/{id}]): deterministic rule-based extraction (bullets, action verbs, todo, @Name, PJ:); owners are never invented; every action lands as a tracked task, notes in the new morning_notes table (migration 0025). 35 new tests. Honest limits: Google Calendar is not connected yet, so no real calendar event has flowed through the summary (fake connectors only in tests); Gmail has never read a real bill email; bill candidates are always verified: false and the screen tells the user to open the email to confirm amount and date; the transcriber is still a stub, so voice notes on a real phone are untested; the other three section 12.8 bullets (small-business assistant, light personal finance, contextual reminders) are out of scope and not built. Prayer/holiday sources were verified live against the real Aladhan and Nager.Date endpoints; two real bugs found during that live check are fixed here (the dateless Aladhan endpoint 302-redirects and httpx cannot follow it through the VM egress proxy, now calls the dated endpoint directly; GET /api/morning/status returned 500 when the vault master key was unset, now reports unavailable).
- Merge: w1 was based on the post-P-05 state, so its shared files predated P-06 — 9 shared files taken wholesale from w1 (build/ == base for all of them, verified against main 512200dd); client.ts needed no hand-merge (w1's copy is byte-identical to base — the morning functions live entirely in the new api/morning.ts); generated-types.ts regenerated from the merged spec (+513 lines, 0 removed). VM replaced again mid-wave (cluster down, role root + all 3 test DBs gone): recovered the same turn (role + DBs recreated, migrations up on all three, RLS verified 75 tables / 131 policies); the P-09 worker in w2 was notified to re-verify its DB before testing.
- Notion P-07 → Done / Suite green / commit. P-09 (live bot view + takeover) still building in w2; P-10 needs the P-09 capture, so it spawns only after P-09 merges.

## Wave 51 — Live bot view + takeover (21 Nov ~00:50 WIB)
- Central suite (-n 4): **4,861 passed, 25 skipped, EXIT 0, 16m43s** (log suite-wave51-p09-rerun2.log). Count reconciles: 4,824 + 32 new tests + 4 from the extended event-type checks + 1 lite screen case for the new screen. First run red on 4 tests (docs drift on the two new event types, the missing lite live template, and the worker-db test that runs the unit subset); all three root causes fixed in the item commit; re-run 1 SIGTERMed at 79% by a VM replacement (the fifth that day); re-run 2 green after Postgres recovery.
- Live bot view + takeover (plan 12.9, issue #22): pushed 3c16a98d, PR #51 (Closes #22). Live view (GET /api/live-view/{agent_id}, /stream): status held/working/idle, open task, last tool, replayable ledger events in order with the hash chain, plus an ephemeral live tail marked source "live"; the frame comes from real events through a FrameSource seam for the replay work next. Takeover (POST /api/live-view/{agent_id}/takeover, /release): hold row + lifecycle cancel + PWA pool stop + takeover_started in the ledger; the turn loop refuses new work while held (turns end interrupted, history intact); releasing deletes the hold and appends takeover_released. Unlike stop-all it does not revoke the agent's own tokens, so work can resume. 32 new tests. Honest limits: no video or desktop screenshot (screenshot always null); no browser/CDP; click/type takeover (V2) out of scope; no live end-to-end check with a real bot running (the gate is proven with fake model/tools plus the real hold store); the PWA pool is in-memory per process, so a hold only pauses the pool in the same PWA process that holds it.
- Merge: w2 was based on the post-P-06 state, so its shared files predated P-07 — 9 shared files hand-merged per-hunk (app.py, openapi-pwa.yaml, bootstrap.ts, App.tsx, test_pwa.py now 12 screens, test_migrations.py now 27, generated-types.ts regenerated +383/-1 line which is the old Bootstrap enum without "live"). Migration-number collision with P-07: both created 0025, so the takeover migrations are renumbered 0026/0027 (file names, TAKEOVER_MIGRATION, and every 0025/0026 reference in code, tests, and docs shifted +1). The coordinator's own merge script failed twice mid-run on stale anchors (caught by its assertions before any write) and is not idempotent after partial success — the migration half ran as a separate script. The first suite run also caught two gaps the worker's neighbour set missed: the docs contract + 0001 CHECK lists for the new event types (the project's own three-place drift rule) and the missing lite live template — both fixed in the item commit, plus a ruff-format wrap of the 8-item re-apply list in test_migrations.py.
- Notion P-09 → Done / Suite green / commit. P-10 (replay timeline, needs the P-09 capture) + P-11 (visual approval overlay, needs the P-09 live view) spawn next, in w1 + w2 refreshed from build/ post-P-09.

## Wave 52 — Replay timeline + visual approval overlay (21 Nov ~02:20 WIB)
- Central suite (-n 4): **4,918 passed, 25 skipped, EXIT 0, 16m54s** (log suite-wave52-p10-p11.log). Count reconciles: 4,861 + 25 new P-10 tests + 1 lite screen case for the replay screen + 31 new P-11 tests. Green on the first run — no re-run needed.
- Replay timeline (plan 12.10, issue #23): pushed 2578f267, PR #53 (Closes #23). Replay from the P-09 capture: reads ledger events for one task, derives each step's reason + result from the event content, verifies the hash chain (chain_ok + broken event ids), scrubs to any step, exports a GIF from deterministic Pillow frames (480x270, 800 ms per frame). 25 new tests. Honest limits: no real desktop screenshot — each step's "screenshot" is a rendered frame from the step data, never a desktop image; tasks over 500 steps are rejected with 400, not silently cut; the screen is verified in vitest + Jinja render only, no live-browser end-to-end check.
- Visual approval overlay (plan 12.10, issue #24): pushed 6dbb5851, PR #54 (Closes #24). The visual layer over the G4 gate: the overlay store + API under /api/live-view/{id}/approvals (propose classifies via G4, decide approves or rejects, rejection cancels the action in the gate via the new Gate.reject and is recorded in the ledger), the highlight against the live frame, the ApprovalOverlay panel in the live view. 31 new tests. Honest limits: no screenshot and no pixel highlight — the overlay points at rows of the event-based live frame; the overlay store is in-memory like the gate's requests (a restart clears pending overlays, the ledger stays the durable record); the PWA does not hold the gate — rejection in the gate happens when the store is built with the turn loop's gate. No new migration (the event types already existed since revision 7), so no 0028 collision with P-10.
- Merge: w1 (P-10) was based on the post-P-09 state, so its 18 files copied wholesale. w2 (P-11) was also based on the post-P-09 state, so its 6 shared files were hand-merged per-hunk (app.py, client.ts, client.test.ts, openapi-pwa.yaml) and 14 files copied wholesale; test_pwa.py kept build/'s 13-screen version (P-11 did not change the screen count). The merge script had a real bug — its insert-after-anchor helper split four constructs (replay path $ref, ReplayTaskSummary $ref, fetchReplayTasks's signature, the P-11 describe nested inside P-10's) and its tag extract swallowed the office + settings tags and duplicated them; all four were caught by the coordinator's own verification (YAML parse error on generate) and fixed before any push (fix_merge_p11.py). Ratchet re-measured in build/ after merging: app.py 1133, openapi-pwa.yaml 1889, client.ts 1084.
- Notion P-10 + P-11 → Done / Suite green / commit. P-12 (rich cards in Chat) + P-13 (task progress rail beside Chat) spawn next, in w1 + w2 refreshed from build/ post-P-11.

## Wave 53 — Rich cards in Chat + task progress rail (21 Nov ~03:10 WIB)
- Central suite (-n 4): **4,944 passed, 25 skipped, EXIT 0, 17m16s** (log suite-wave53-p12-p13.log). Count reconciles: 4,918 + 26 new P-12 tests (P-13 added no Python tests). Green on the first run — no re-run needed.
- Rich cards in Chat (plan 12.10, issue #25): pushed fc9fa0a8, PR #56 (Closes #25). Draft email card (copy + mailto:), option card (tap once to send the label), data chart card (SVG bars from Insights data), map card (free OpenStreetMap tiles, marker + route polyline). Payloads are declarative JSON in the B7 shape; drafts reuse B13, charts reuse Insights; the slippy-map tile math is verified on both sides (Python + TypeScript). 26 new tests. Honest limits: live tile loading is NOT verified in a real browser (no live browser on this VM) — tile URLs are correct and the template renders, but no map screenshot is claimed; the map is static 320x240 with no pan/zoom (interactive map is P-14's scope); the chart is bars only; copy is unavailable in the lite template (no JavaScript), the full draft text is shown instead.
- Task progress rail (plan 12.10, issue #26): pushed f0c8d675, PR #57 (Closes #26). The rail beside Chat (230px, stacks below 640px) follows running tasks live from the same SSE office frame Chat already receives — no new endpoint, no polling, no migration, no duplicated Tasks logic. No new screen, still 13 screens. Honest limits: the rail shows task ids, not purpose/stage (those are not in the office projection and the rail does not invent them — follow-up: a richer projection or a task-list endpoint); it does not know why a task finished; "live" means per SSE frame, not a push per second; movement on a real screen is not verified (component + state tests only).
- Merge: w1 (P-12) was based on the post-P-11 state, so its 13 files copied wholesale; the 2 shared files (ChatScreen.tsx, openapi-pwa.yaml) were hand-merged per-hunk with the Wave 52 insert-before lesson applied (chat/cards paths inserted BEFORE /api/voice/upload, never after a key whose $ref follows). ChatScreen needed a real merge: P-13 had inlined the send path into a single onSend while P-12's option-pick needs a shared sendText helper — the merged file restores sendText (P-12's shape) with P-13's logic intact, and keeps both the rail layout and the card render. The P-12 worker also found a neighbour gap (NOT fixed in this merge, by the worker's own call): GET /api/drafts/{non-UUID id} (B13) returns 500 (InvalidTextRepresentation) instead of 404 — a one-line fix in the same shape as P-12's own UUID validation, recorded here as an open follow-up, not silently slipped into the P-12 commit. Ratchet re-measured in build/ after merging: app.py 1138, client.ts 1124, openapi-pwa.yaml 1907 (93 lines of headroom left under the 2000-line cap).
- VM replaced a 6th time mid-wave (~02:06 WIB): Postgres down, role + 3 DBs gone. Recovered the same turn without apt (binaries + pgvector already present): cluster started, role + DBs recreated, all 27 migrations on each, RLS 75/131 verified. No VM restart from our side, per Nero's order. P-12 worker survived, was notified Postgres was back, and re-verified its DB at 0027 before its final tests.
- Notion P-12 + P-13 → Done / Suite green / commit. P-14 (interactive components in Chat) + CLA spawn next, in w1 + w2 refreshed from build/ post-P-13.

## Wave 54 — Interactive components in Chat + CLA (21 Nov ~03:5x WIB)
- Central suite (-n 4): **5,016 passed, 25 skipped, EXIT 0, 18m22s** (log suite-wave54-p14-cla.log). Count reconciles: 4,944 + 40 new P-14 tests + 32 new CLA tests. Green on the first run — no re-run needed.
- Interactive components in Chat (plan 12.10, issue #27): pushed e8ad4332, PR #59 (Closes #27). Five widget types as declarative JSON on chat messages: calculator (number/slider inputs + a safe formula evaluator, whitelist AST, never eval), form (text/number/select/checkbox + submit validation), comparison (sortable numeric columns + cheapest row), diagram (nodes + edges, cycles rejected via Kahn), simulation (the existing world simulator exposed as-is: per-store prices per day, an Rp150,000 PLN bill due day 20, advance_day / pay_bill / buy_from_store actions). Six endpoints under /api/chat/widgets; the PWA renders all five types and calculates live in the browser. 40 new tests (32 + 8) + 11 vitest. Honest limits: widget interaction is verified in vitest/jsdom + Python tests only — no live browser on this VM, so taps and drags on a real screen are not verified; simulation prices and bills come from the deterministic world simulator, not live market data, and "pay bill" only changes the simulated world; the server compute endpoint is the authoritative check (if the browser calculator disagrees, the server answer wins); each simulation step appends a new chat message instead of editing the old one in place.
- CLA (issue #28): pushed c6612231, PR #60 (Closes #28). The contributor agreement, lightweight, no copyright assignment: CLA.md (v1.0, broad copyright + patent licence, relicense/sublicense rights), CONTRIBUTING.md (how to contribute + how to sign), scripts/cla_check.py (stdlib only, reads PR comment JSON, verdicts signed / exempt / not_signed, exit 0/1/2), and a GitHub Actions workflow (.github/workflows/cla.yml, pull_request_target + issue_comment, checks out the base ref so PR code never runs) that fails the job, adds the `cla: missing` label, and posts a one-time guidance comment when unsigned. Signing is one PR comment with the exact agreement sentence; a registry file was rejected with reasons recorded in docs/cla.md (anyone can write another person's name into a file, so it proves nothing — a comment is posted by the GitHub account itself). The maintainer and bot accounts are exempt. 32 new tests. Honest limits: the workflow has NEVER run on GitHub (no CI has ever run in this repo) — YAML is valid and the checker passes unit tests including simulated PR payloads, but "the bot works" can only be claimed after the first real PR run; making the check a required status check is a maintainer step after that first green run, deliberately not done here; the repo still has NO LICENSE file (decision D-01, open) — until a licence is chosen, the grants in the CLA itself are the licence, and the CLA only becomes a full project licence grant once a licence is chosen, so do not present this as "project licensing is complete" until D-01 is closed.
- Merge: CLA was verified and merged into build/ first (8 files, wholesale — build/ == w2 base, verified by diff; README kept the +2 CLA lines). P-14 merged second (15 files copied wholesale — CLA touched none of P-14's shared files; generated-types.ts regenerated from the merged spec, byte-identical to the worker's). Push mechanics: P-14's files from build-w1/ (P-14-only shared versions), CLA's files from build/ on a branch created after P-14's merge. Lesson (diff_vs_head.py): NEVER pipe it through `tee ... | head -N` — head exits after N lines, tee gets SIGPIPE and dies, leaving the output file truncated mid-alphabet (first run: 1017 lines instead of 2711; the puspa/ and tests/ files silently missing, looked like a real diff). Run with a plain stdout redirect, then grep the saved file. Ratchet re-measured in build/ after merging: app.py 1138 -> 1144, client.ts 1124 -> 1186, openapi-pwa.yaml 1907 -> 1926 (74 lines of headroom left under the 2000-line cap). Vitest 227 (38 files). Screens still 13. Migrations still 27 (neither item needed one, so no 0028 collision). No VM replacement during this wave; Postgres stayed up throughout.
- Notion P-14 + CLA → Done / Suite green / commit. L-02 (minimal docs: ToS, privacy, data consent, content policy) is the last queue item and spawns next, alone.

## Wave 55 — Minimal legal documents (21 Nov ~04:20 WIB)
- Central suite (-n 4): **5,021 passed, 25 skipped, EXIT 0, 18m20s** (log suite-wave55-l02.log). Count reconciles: 5,016 + 5 new tests. Green on the first run — no re-run needed.
- Minimal legal documents (issue #29): pushed 7c86113c, PR #62 (Closes #29). Five documents, all draft 0.1, 21 Nov 2026: terms of service (142 lines), privacy policy (150), data consent (115), content policy (130), plus docs/legal.md (99) as the status index. Prices quoted from the billing config (Free Rp0/15 credits, Plus Rp99,000/60, Pro Rp199,000/150, Team Rp499,000/400 + Rp99,000 per extra seat; credit packs 20/Rp9,000, 100/Rp35,000, 500/Rp150,000; 14-day trial in code; per-task cost ceiling Rp2,000), consent text quoted verbatim from the product consent text (v1-2026-11-17), payment facts from the prepare-only flow (execution limit Rp0, approval is not execution, 24h expiry). A drift test (tests/test_legal_docs.py, 5 tests) checks the five documents exist with their key sections, the consent version matches the code, prices match the config by parsing (not hardcoding), and no licence name or legal-compliance claim appears. Honest limits: NOT reviewed by a lawyer — the legal review (L-01) is still open and needs a human, so these must not be shown outside the close circle as final; account deletion does NOT delete ledger event content (the tombstone event type that would make the consent text's claim fully true is not built yet, revision 8 / Phase 2); no per-feature consent withdrawal (only stop using + delete account); no content-report button, no published support contact, no general content classifier, no age rule, no real-money billing yet (local gateway is a stub).
- Merge: w1 was based on the pre-Wave-54 state (its README lacked the CLA lines and its status log lacked the Wave 54 section), so only the 6 new files were copied wholesale; build/'s README and status log were kept. The spawn brief wrongly told the worker the worktree had been refreshed from the post-Wave-54 HEAD — the worker's files were all new and unaffected, but the claim was wrong and is corrected here. No VM replacement during this wave; Postgres stayed up throughout.
- Notion L-02 → Done / Suite green / commit. THE QUEUE IS DONE: all 22 items (E-16 follow-up, E-19..E-21, P-01..P-07, P-09..P-14, CLA, L-02) are built, pushed, and green. Remaining open work is outside this queue: the GATES ticket (comprehensive QA, needs Contabo VPS + Nero's decisions 15, 5, 6, 19), L-01 legal review (needs a human), L-03 trademark (needs a human), D-01..D-07 (Nero's decisions), culture B15/B16/B17 + C (parked), E-02 live check (needs Contabo VPS).
