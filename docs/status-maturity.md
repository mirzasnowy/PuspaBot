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

## Wave 28 — Kosmos K11: per-area autonomy dial (DONE 19 Nov 2026)

K11 is the per-area autonomy dial: trust per action area,
moving with prediction calibration, with an emergency drop
that is instant. New package `puspa/autonomy/` (`dial.py`,
543 lines), deterministic, no LLM, in-memory by deliberate
choice (see the honest limits). First item built under the
2-worker mode: worker 1 in worktree `build-w1` on
`puspa_test_w1`; the coordinator verified, merged, and runs
the suite in `build/` on `puspa_test`.

Areas: `money`, `messaging`, `files` (irreversible);
`scheduling`, `system` (reversible); `accounts`
(irreversible + capped at 0.59, so it never rises above
`propose`). Every area starts at 0.50 (`propose`): trust is
earned, never assumed. A correct outcome closes 5% of the
remaining gap to 1.0 (slow rise: 5 correct in a row reach
`do_and_show` at 0.613; 24 reach `do_silently` at 0.854).
An ordinary wrong outcome drops the score by 0.15, floored
at 0.0 (fast fall on purpose: 1 wrong from 0.85 lands at
0.70; 2 wrong land at 0.55, back to `propose`). A serious
failure is the emergency drop: in an irreversible area the
score falls to 0.0 at once (rebuild: 18 correct to
`do_and_show`, 37 to `do_silently`); in a reversible area
it falls by 0.30. A serious failure is an action that
failed harmfully, or an irreversible action that went
wrong — not a plain tool error (that is an ordinary wrong
outcome). `reset_area` is an operator action (tests, or a
human resetting trust after a review); the agent itself
never calls it. Each area keeps an audit history (cap 100
records): the dial's audit lives there, not in the ledger,
by design (no ledger event type for dial moves, keeping
the 31 revisi-7 types closed, same pattern as K10).

Calibration uses signals that already exist: the dial does
not score predictions itself, it only aggregates outcomes
already scored per area through `record_outcome` (the
world-model `WorldModel.stats` scores, the K5 per-contact
accuracy, and the learning-loop verdicts are its sources;
the turn loop is the first feeder, from tool-execution
results). The wiring chosen is the turn loop (same advisor
pattern as the K1 intent advisor), not approvals G4: one
clear gating path, and the dial feeds back from execution
results. `TurnLoop` takes an optional `autonomy_dial` (+
`dial_area_for_tool` overrides); the dial is consulted in
`_run_tool_calls` AFTER the intent advisor, so an intent
`ask` wins. At `propose` the tool does NOT run: the turn
records a `dial_ask` event and returns a
`dial_ask_payload` (`tool_was_run: false`). At `do_and_show`
the tool runs and the turn records `dial_show`; at
`do_silently` it runs and records `dial_silent` (the model
still sees the tool result, so it can continue the turn;
the presenting surface must honour the `show: false` flag
— the loop itself does not silence the model's narration).
Every executed outcome feeds the dial (`record_outcome`,
correct = ran as expected, wrong = error), wrapped so a
dial failure never breaks the turn; a broken dial fails
open (the call runs as if no dial were wired in). Verdicts
land on `TurnResult.dial_decisions`.

Files (worker 3afbe2f2 in build-w1, merged by the
coordinator): created `puspa/autonomy/dial.py` (543 lines),
`puspa/autonomy/__init__.py` (45 lines),
`tests/test_autonomy_dial.py` (584 lines, 36 tests),
`docs/autonomy-dial.md` (148 lines); changed
`puspa/turns/loop.py` (859 -> 969, still under 1000).
`puspa/world/model.py` (1,395) and `puspa/world/agents.py`
untouched. No new ledger event type (31 stay closed).

Verification (coordinator, re-ran herself in build-w1 on
`puspa_test_w1`): module tests
`tests/test_autonomy_dial.py`: 36 passed, including the
immediate-drop test (climb to `do_silently` with 24
correct, `record_serious_failure`, next turn blocks
`gmail.send` with an empty registry and payload
`tool_was_run: false`). Neighbour regression (autonomy
dial / intent turn wiring / approvals taxonomy / turn loop
/ turn intercepts): 148 passed. Ruff check + format:
clean. Mypy: clean. Read in full: the dial rules
(`record_outcome`, `record_serious_failure`, `_stage_for`)
and the loop wiring (`_run_tool_calls`,
`_consult_autonomy_dial`, fail-open).

Worktree suite (worker, build-w1, `puspa_test_w1`):
3,427 passed, 23 skipped, 1 failed, 36m21s, EXIT=1 —
total 3,428 tests, exactly 3,392 (Wave 27) + 36 K11. The
single failure was `tests/test_backup.py::
test_create_and_backup_items` asserting the manifest
database equals the hardcoded `"puspa_test"`, while the
backup manifest writes the real DSN database
(`puspa_test_w1`). Pre-existing environment mismatch, not
a K11 effect (the worker touched no backup code). Fixed
by the coordinator in `tests/test_backup.py` (rides with
this wave): the manifest assertion now reads the dbname
from the test DSN, and the restore database is derived
per-DSN (`<dbname>_restore`) instead of the shared
`puspa_test_restore`, so two worktree suites can no longer
race on it. Verified in build-w1: `tests/test_backup.py`
4 passed (including the restore test against
`puspa_test_w1_restore`).

Suite (coordinator, SOLO in build/ on `puspa_test`): 3,444
passed, 23 skipped, 1 warning, 21m52s, EXIT 0. The first
K11-only attempt was SIGTERM'd by a VM replacement at 54%
(no failures visible); the verification re-ran as one
combined run after the K12 merge, log
hidden_files/suite-wave28-k12.log.
Reconciliation: 3,392 (Wave 27) + 36 (K11 module) + 16 (K12
module) = 3,444.

Honest limits: the dial is in-memory — a restart returns
every area to 0.50 (the safe direction: only more
cautious, never bolder). Postgres persistence is a
follow-up. The calibration signal today is tool-execution
results (ran / errored), a proxy, not proof the prediction
was right; a stronger feeder (K8 outcome watcher) is
planned to use the same `record_outcome` entry point. At
`do_silently` the loop records `show: false` and
`dial_silent`, but the model still sees the tool result;
the presenting surface must honour the flag. No LICENSE or
billing model decision was made.

Remaining: Kosmos K12, K13, then B features
(B6, B9, B10, B11, B12, B13), then B15 culture LAST. Then uji
+ 15 gates + 10 reference tasks (waiting on Nero's decisions).
## Wave 28 (continued) — Kosmos K12: audit log outside agent reach + kill switch + export (DONE 19 Nov 2026)

K12 is the audit log the agent cannot touch, the kill switch
that stops it, and the export that lets the user take their
data home. Second item of the 2-worker mode: worker 2 in
worktree `build-w2` on `puspa_test_w2`; the coordinator
verified, merged, and runs the suite in `build/` on
`puspa_test`. (The worker's own suite hit the same backup
mismatch as K11's — see the K11 section above; the fix
rides with K11 and is already in `build/`.)

Audit log: no second ledger — the existing `puspa/ledger/`
hash chain is hardened. New `AuditWriter`
(`puspa/ledger/audit_writer.py`, 108 lines): a `Ledger`
bound to the `puspa_app` role (`SET ROLE` right after
connecting; `Ledger` takes an optional `role`, validated
against a role-name regex because `SET ROLE` takes an
identifier, never a parameter). Postgres itself enforces
the role: `events` is SELECT + INSERT only for
`puspa_app` (UPDATE/DELETE revoked, pre-existing in
`schema.sql`); the agent role can append and read, never
rewrite history. `probe_tamper` proves it: it attempts
UPDATE and DELETE on a log row as the agent role and
reports both blocked (`InsufficientPrivilege`); each
attempt runs in its own transaction and is rolled back
afterwards, and the probe re-issues `SET ROLE` before
each attempt because a rollback resets the role to the
session user — a psycopg behaviour documented in
`docs/audit-kill-switch-export.md`. `ensure_audit_role`
creates the role when missing (operator step, superuser);
the schema grants apply only when the role already
exists, so the role must exist before `create_schema`
runs — the test suite's shared fixture does this once per
session.

Kill switch: one row in a new `kill_switch` table
(`schema.sql` 76 -> 100 lines; `puspa/ledger/kill_switch.py`,
304 lines). The application role has SELECT only on the
flag table; only the table owner (the operator
connection) can INSERT/UPDATE it, so the agent can
neither arm nor disarm it. `KillSwitch.activate` /
`deactivate` are explicit operator calls (actor +
reason required, both non-empty); the flag flips FIRST
and the ledger write follows, so if the ledger is
unreachable the agent still stops and `KillSwitchError`
is raised (fail-closed, safe direction). Activation is
recorded as `task_stopped`, deactivation as
`user_message` — existing types only, the 31 revisi-7
types stay closed. The harness
(`puspa/harness/loop.py`, 788 -> 821 lines) takes an
optional `kill_switch`: a new `STATUS_KILLED` outcome,
checked before the run, before each attempt, and in
plan/answer modes, so a switch flipped mid-run stops the
run before the next attempt (0 generator/executor calls
when active from the start). The scheduler
(`puspa/scheduler/scheduler.py`, 410 -> 444 lines) takes
`kill_switch` in `tick` / `run_trigger` / `run_missed`:
while active, `tick` is a total no-op (no heartbeat) and
triggers stay due. `is_kill_switch_active` accepts None
(no switch wired: behaviour unchanged), a callable, an
object with `is_active()`, or a boolean `.active`
attribute — anything else raises `TypeError` instead of
silently reading as "off". A missing `kill_switch` table
raises `KillSwitchError`, never reads as "off".

Export: `puspa/ledger/export.py` (227 lines) —
`export_user_data` / `build_user_export` writes one
portable JSON file, atomically, with 10 sections
(profile, episodic, procedural, summaries, slice
grants, style examples, onboarding, consent, ledger
events, vault metadata) and an explicit `excluded` list
so the gap is visible, not silent. Never any secrets:
no `password_hash`, no session token hashes, no vault
ciphertext (entry names + update times only), no slice-
grant `token_ref` values (written as `«withheld»`), and
every string passes through `redact_secrets`, so a
password the user pasted into a chat line is redacted
(`«redacted»`). Export is a point-in-time snapshot; no
incremental/scheduled export yet.

Files (worker 9b6c42d8 in build-w2, merged by the
coordinator): created `puspa/ledger/kill_switch.py`
(304 lines), `puspa/ledger/audit_writer.py` (108 lines),
`puspa/ledger/export.py` (227 lines),
`tests/test_audit_kill_switch.py` (492 lines, 16 tests),
`docs/audit-kill-switch-export.md` (170 lines); changed
`puspa/ledger/schema.sql` (76 -> 100),
`puspa/ledger/ledger.py` (457 -> 483),
`puspa/ledger/__init__.py` (79 lines),
`puspa/harness/loop.py` (788 -> 821),
`puspa/scheduler/scheduler.py` (410 -> 444). No file
over 1000 lines; `puspa/world/model.py` (1,395) and
`puspa/world/agents.py` untouched. The merge deliberately
did NOT copy w2's `puspa/turns/loop.py` (K11's version in
`build/` is newer), `tests/test_backup.py` (the
coordinator's DSN-aware fix in `build/` is newer), or
`tests/test_file_size_ratchet.py` (the archive note in
`build/` is newer).

Verification (coordinator, re-ran herself in build-w2 on
`puspa_test_w2`): module tests
`tests/test_audit_kill_switch.py`: 16 passed. Neighbour
regression (ledger / event types / audit trail /
bitemporal / coding harness / harness modes / harness
unified / scheduler push / RLS / access): 188 passed.
Ledger/audit set (K12 module + ledger + event types +
audit trail): 72 passed. Ruff check + format: clean.
Mypy: clean. Read in full: `kill_switch.py` (flag
lifecycle, fail-closed reads, flip-before-record),
`audit_writer.py` (SET ROLE + `probe_tamper`), the
`schema.sql` grants (events SELECT+INSERT only,
kill_switch SELECT only), the harness + scheduler
wiring, and the export exclusions (verified
`password_hash` / ciphertext / `token_ref` handling +
`redact_secrets` in code). Event types still 31
(checked live).

Worktree suite (worker, build-w2, `puspa_test_w2`):
3,407 passed, 23 skipped, 1 failed, 36m49s, EXIT=1 —
total 3,408 tests, exactly 3,392 (Wave 27) + 16 K12. The
single failure was the same backup-test mismatch as
K11's (hardcoded `"puspa_test"` vs the real DSN
database `puspa_test_w2`); fixed by the coordinator in
the K11 merge.

Suite (coordinator, SOLO in build/ on `puspa_test`, log
hidden_files/suite-wave28-k12.log): 3,444 passed, 23 skipped,
1 warning, 21m52s, EXIT 0.
Reconciliation: 3,392 (Wave 27) + 36 (K11 module) + 16 (K12
module) = 3,444.

Honest limits: tampering by the owner/superuser is only
DETECTED (the hash chain breaks and `verify_chain`
flags the row), not prevented — prevention at that
level is detection. The kill switch stops new actions,
not an action already running (checks run between
attempts, not inside one execution). The flag lives in
Postgres: if the database is down the switch cannot be
read, and a missing table raises `KillSwitchError`
instead of reading "off". A caller that does not wire a
switch (`kill_switch=None`) has no protection — wiring
it into the production entry point is a deployment step,
not part of this change. `probe_tamper` proves the grants
on the database it runs against; a new database whose
`create_schema` ran before the `puspa_app` role existed
is unprotected — run `ensure_audit_role` first, then
`create_schema`. The `kill_switch` table is not
TRUNCATE'd by the shared conftest fixture; the K12
module-test fixture resets the flag itself before/after
each test. No LICENSE or billing model decision was made.

Remaining: B features (B11, B12, B13), then B18
(second-brain graph view), then culture LAST (B15, B16, B17, in
that order). Then uji + 15 gates + 10 reference tasks (waiting
on Nero's decisions).

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
